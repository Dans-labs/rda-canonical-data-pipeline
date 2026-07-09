from fastapi import APIRouter, BackgroundTasks, HTTPException, Body, Query
from typing import Optional, Dict, Any
from datetime import datetime
import threading
import requests
import uuid
import subprocess
import sys
from pathlib import Path
import os
import time
import errno
import logging as logger

from src.cannonical_data_pipeline.infra.commons import app_settings

# ...existing code...

from src.cannonical_data_pipeline.deduplication.apply_deduplication import apply_deduplication
from src.cannonical_data_pipeline.deduplication.add_columns import apply_add_columns
from src.cannonical_data_pipeline.deduplication.update_uuids import apply_update_uuids
from src.cannonical_data_pipeline.deduplication import check_duplicates as dup_mod

router = APIRouter(prefix="/sync", tags=["sync"])

# In-memory state for simple background runs and schedules
_last_run_lock = threading.Lock()
_last_run: Dict[str, Any] = {"time": None, "success": None, "report": None}

_schedules_lock = threading.Lock()
_schedules: Dict[str, Dict[str, Any]] = {}

# Pipeline process guard
_pipeline_lock = threading.Lock()
_pipeline_process: Optional[subprocess.Popen] = None
_pipeline_task: Dict[str, Any] = {"task_id": None, "start_time": None, "end_time": None, "returncode": None}

# File-based lock (POSIX atomic create) to prevent concurrent runs across processes
# Default lock file placed in repo root so it's shared by processes on the same host
_repo_root = Path(__file__).resolve().parents[3]
_file_lock_path = _repo_root / ".run_pipeline.lock"
_file_lock_fd: Optional[int] = None


def _acquire_file_lock(timeout: float = 0) -> Optional[int]:
    """Attempt to acquire a lock by atomically creating the lock file.

    Returns the opened file descriptor if successful, or None if lock is held by another live process.
    If a stale lock file is found (PID not alive), it will be removed and acquisition retried.
    timeout=0 means try once; >0 will retry until timeout seconds.
    """
    start = time.time()
    lock_path_str = str(_file_lock_path)
    while True:
        try:
            fd = os.open(lock_path_str, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            # Write our pid for debugging and future stale detection
            try:
                os.write(fd, f"{os.getpid()}\n".encode())
                os.fsync(fd)
            except Exception:
                pass
            return fd
        except FileExistsError:
            # Someone else created the lock file — check if it's stale
            try:
                with open(lock_path_str, 'r') as f:
                    content = f.read().strip()
                    pid = int(content.splitlines()[0]) if content else None
            except Exception:
                pid = None

            if pid:
                try:
                    # check if process is alive
                    os.kill(pid, 0)
                    # process exists -> lock held
                    return None
                except OSError as e:
                    if e.errno in (errno.ESRCH,):
                        # stale PID: remove lock file and retry
                        try:
                            os.unlink(lock_path_str)
                        except Exception:
                            pass
                        # retry immediately
                        continue
                    else:
                        # unknown error checking pid — treat as locked
                        return None
            else:
                # no pid found in file -> remove and retry
                try:
                    os.unlink(lock_path_str)
                except Exception:
                    pass
                continue
        except Exception:
            # unexpected OS error — don't acquire
            return None
        # timeout handling
        if timeout > 0 and (time.time() - start) >= timeout:
            return None
        time.sleep(0.1)


def _release_file_lock(fd: Optional[int]):
    """Release the acquired file lock and remove the lock file."""
    lock_path_str = str(_file_lock_path)
    try:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        # Attempt to remove the lock file; ignore failures
        try:
            if os.path.exists(lock_path_str):
                os.unlink(lock_path_str)
        except Exception:
            pass
    finally:
        return


def _update_last_run(success: bool, report: Dict[str, Any]):
    with _last_run_lock:
        _last_run["time"] = datetime.utcnow().isoformat() + "Z"
        _last_run["success"] = success
        _last_run["report"] = report


def _run_mode(mode: str, schema: str = "public") -> Dict[str, Any]:
    """Execute one of the supported modes and return its report."""
    mode_map = {
        "apply-deduplication": apply_deduplication,
        "add-columns": apply_add_columns,
        "update-uuids": apply_update_uuids,
        "run-all": lambda schema="public": {
            "apply": apply_deduplication(schema=schema),
            "add_columns": apply_add_columns(schema=schema),
            "update_uuids": apply_update_uuids(schema=schema),
        },
        "check-duplicates": lambda schema="public": dup_mod.generate_duplicates_report(table_name="deduplicated_institutions_kb", only_with_duplicates=True),
    }

    func = mode_map.get(mode)
    if func is None:
        raise ValueError(f"Unknown mode: {mode}")

    # Call function; make sure we pass schema when supported
    try:
        # many of our functions accept schema kwarg; try to pass it
        return func(schema=schema) if "schema" in func.__code__.co_varnames else func()
    except TypeError:
        # fallback: call without schema
        return func()


def _run_in_background(task_id: str, mode: str, schema: str):
    try:
        report = _run_mode(mode, schema=schema)
        _update_last_run(True, {"task_id": task_id, "mode": mode, "report": report})
    except Exception as e:
        _update_last_run(False, {"task_id": task_id, "mode": mode, "error": str(e)})


@router.post("/trigger")
def trigger_sync(
    mode: str = Body("run-all"),
    schema: str = Body("public"),
    background: bool = Body(False),
    background_tasks: BackgroundTasks = None,
):
    logger.info("start trigger_sync: mode=%s schema=%s background=%s", mode, schema, background)
    """Trigger a sync operation.

    - mode: which sync action to run (apply-deduplication|add-columns|update-uuids|run-all|check-duplicates)
    - schema: optional schema name
    - background: if true, runs the task in background and returns a task id
    """
    if not background:
        try:
            report = _run_mode(mode, schema=schema)
            _update_last_run(True, {"mode": mode, "report": report})
            return {"success": True, "mode": mode, "report": report}
        except Exception as exc:
            _update_last_run(False, {"mode": mode, "error": str(exc)})
            raise HTTPException(status_code=500, detail=str(exc))

    # background run
    task_id = str(uuid.uuid4())
    thread = threading.Thread(target=_run_in_background, args=(task_id, mode, schema), daemon=True)
    with _schedules_lock:
        # store as ad-hoc task entry (no repeat)
        _schedules[task_id] = {"mode": mode, "schema": schema, "type": "oneoff", "thread": thread, "created": datetime.utcnow().isoformat() + "Z"}
    thread.start()
    return {"accepted": True, "task_id": task_id, "mode": mode}


@router.get("/last")
def last_run_status():
    """Return the last run status and report."""
    logger.info("start last_run_status")
    with _last_run_lock:
        return _last_run.copy()


def _schedule_runner(name: str):
    """Internal runner that executes the scheduled job and reschedules it."""
    with _schedules_lock:
        conf = _schedules.get(name)
        if not conf or not conf.get("enabled"):
            return
        interval = conf.get("interval_seconds")
        mode = conf.get("mode")
        schema = conf.get("schema")

    # Run sync (synchronously in this thread)
    try:
        report = _run_mode(mode, schema=schema)
        _update_last_run(True, {"schedule": name, "mode": mode, "report": report})
    except Exception as e:
        _update_last_run(False, {"schedule": name, "mode": mode, "error": str(e)})

    # Reschedule next run
    with _schedules_lock:
        conf = _schedules.get(name)
        if conf and conf.get("enabled"):
            timer = threading.Timer(interval, _schedule_runner, args=(name,))
            conf["timer"] = timer
            timer.daemon = True
            timer.start()


@router.post("/schedule")
def create_schedule(
    name: str = Body("default"),
    mode: str = Body("run-all"),
    interval_seconds: int = Body(...),
    schema: str = Body("public"),
    start_immediately: bool = Body(False),
):
    """Create a recurring schedule that runs `mode` every `interval_seconds` seconds."""
    logger.info("start create_schedule: name=%s mode=%s interval_seconds=%s schema=%s start_immediately=%s", name, mode, interval_seconds, schema, start_immediately)
    if interval_seconds <= 0:
        raise HTTPException(status_code=400, detail="interval_seconds must be > 0")

    with _schedules_lock:
        if name in _schedules:
            raise HTTPException(status_code=409, detail=f"schedule '{name}' already exists")

        conf = {"mode": mode, "schema": schema, "interval_seconds": interval_seconds, "enabled": True, "created": datetime.utcnow().isoformat() + "Z"}
        _schedules[name] = conf
        # start timer
        timer = threading.Timer(interval_seconds, _schedule_runner, args=(name,))
        timer.daemon = True
        conf["timer"] = timer
        timer.start()

    if start_immediately:
        # spawn immediate background thread to run once now
        thread = threading.Thread(target=_run_in_background, args=(f"sched-{name}-{uuid.uuid4()}", mode, schema), daemon=True)
        thread.start()

    return {"created": True, "schedule": name, "interval_seconds": interval_seconds, "mode": mode}


@router.get("/schedule")
def list_schedules():
    logger.info("start list_schedules")
    with _schedules_lock:
        out = {name: {k: v for k, v in conf.items() if k != "timer" and k != "thread"} for name, conf in _schedules.items()}
    return out


@router.delete("/schedule")
def delete_schedule(name: str = Query(...)):
    logger.info("start delete_schedule: name=%s", name)
    with _schedules_lock:
        conf = _schedules.get(name)
        if not conf:
            raise HTTPException(status_code=404, detail="schedule not found")
        conf["enabled"] = False
        timer = conf.get("timer")
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        del _schedules[name]
    return {"deleted": True, "schedule": name}


@router.post("/manage/enable")
def enable_schedule(name: str = Body(...)):
    logger.info("start enable_schedule: name=%s", name)
    with _schedules_lock:
        conf = _schedules.get(name)
        if not conf:
            raise HTTPException(status_code=404, detail="schedule not found")
        if conf.get("enabled"):
            return {"enabled": True, "schedule": name}
        conf["enabled"] = True
        # start a timer for next run
        timer = threading.Timer(conf["interval_seconds"], _schedule_runner, args=(name,))
        timer.daemon = True
        conf["timer"] = timer
        timer.start()
    return {"enabled": True, "schedule": name}


@router.post("/manage/disable")
def disable_schedule(name: str = Body(...)):
    logger.info("start disable_schedule: name=%s", name)
    with _schedules_lock:
        conf = _schedules.get(name)
        if not conf:
            raise HTTPException(status_code=404, detail="schedule not found")
        conf["enabled"] = False
        timer = conf.get("timer")
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
    return {"disabled": True, "schedule": name}


@router.post("/sync-now")
def sync_now(background_tasks: BackgroundTasks = None):
    """Run the sync pipeline immediately."""
    logger.info("start sync_now")
    # Ensure only one pipeline process at a time (in-memory) and across processes (file lock)
    with _pipeline_lock:
        global _pipeline_process, _file_lock_fd
        if _pipeline_process and _pipeline_process.poll() is None:
            raise HTTPException(status_code=429, detail="Pipeline is already running")

        # Try to acquire file lock (non-blocking). If lock exists and held by live process -> 429
        fd = _acquire_file_lock(timeout=0)
        if fd is None:
            raise HTTPException(status_code=429, detail="Pipeline is already running (file lock)")
        # remember fd so monitor can release it later
        _file_lock_fd = fd

        # Path to the runner: repo/src/run_pipeline.py
        runner_path = Path(__file__).resolve().parents[3] / "run_pipeline.py"
        if not runner_path.exists():
            # Release file lock before raising
            _release_file_lock(_file_lock_fd)
            _file_lock_fd = None
            raise HTTPException(status_code=500, detail=f"runner not found: {runner_path}")

        # Prepare command to run the pipeline
        cmd = [sys.executable, str(runner_path)]

        # Start the pipeline process
        try:
            _pipeline_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        except Exception as e:
            # failed to start process — release lock and re-raise
            _release_file_lock(_file_lock_fd)
            _file_lock_fd = None
            raise HTTPException(status_code=500, detail=f"failed to start pipeline: {e}")

    task_id = str(uuid.uuid4())

    def _monitor_pipeline():
        global _pipeline_process, _file_lock_fd
        try:
            stdout, stderr = _pipeline_process.communicate()
            returncode = _pipeline_process.returncode

            # Capture output and errors
            report = {
                "task_id": task_id,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "returncode": returncode,
            }

            if returncode == 0:
                _update_last_run(True, report)
            else:
                _update_last_run(False, report)
        except Exception as e:
            _update_last_run(False, {"task_id": task_id, "error": str(e)})
        finally:
            # Always clear in-memory process pointer and release file lock
            with _pipeline_lock:
                _pipeline_process = None
            try:
                _release_file_lock(_file_lock_fd)
            finally:
                _file_lock_fd = None

    # Run the monitor in the background
    if background_tasks is None:
        # if FastAPI did not supply BackgroundTasks, run monitor in a thread
        thread = threading.Thread(target=_monitor_pipeline, daemon=True)
        thread.start()
    else:
        background_tasks.add_task(_monitor_pipeline)

    return {"accepted": True, "task_id": task_id}


@router.post(
    "/es-sync",
    summary="Trigger Elasticsearch sync on configured RDA gateway",
    description="""
Trigger a background ingestion that indexes the application's database into the
search index. This action requests the configured indexing service to start
processing; the actual indexing work is performed by that service.

Authentication: you must call this endpoint with the application's API key
or bearer token (see your administrator for credentials).

Behavior: the endpoint asks the indexing service to start an ingest job and
returns the service's response (typically a short status or job identifier).

Example curl:

  curl -X POST "http://<service>/api/v1/sync/es-sync" \
    -H "Authorization: Bearer <YOUR_API_KEY>"
""",
)
def es_sync():
    logger.info("start es_sync")
    """Trigger a sync on the configured RDA gateway endpoint.

    This reads `RDA_GATEWAY_URL` and `RDA_GATEWAY_SERVICE_API_KEY` from `app_settings` (e.g. from conf/.secrets.toml)
    and issues a POST to that URL with the configured API key in the `x-api-key` header.

    Example: if `RDA_GATEWAY_URL` is `http://rda-gateway:3000/knowledge-base/index/deposits`, this will POST to that URL.
    """
    url = None
    api_key = None

    try:
        url = getattr(app_settings, 'RDA_GATEWAY_URL', None) or app_settings.get('RDA_GATEWAY_URL')
        logger.info(f"RDA_GATEWAY_URL={url}")
    except Exception:
        url = None
        logger.error("es_sync missing RDA_GATEWAY_URL in app_settings")
    try:
        api_key = getattr(app_settings, 'RDA_GATEWAY_SERVICE_API_KEY', None) or app_settings.get('RDA_GATEWAY_SERVICE_API_KEY')
        logger.info(f"RDA_GATEWAY_SERVICE_API_KEY={api_key}")
    except Exception:
        api_key = None
        logger.error("es_sync missing RDA_GATEWAY_SERVICE_API_KEY in app_settings")

    logger.info("start es_sync: url=%s", url)

    if not url:
        logger.error("es_sync missing RDA_GATEWAY_URL in app_settings")
        raise HTTPException(status_code=500, detail="RDA_GATEWAY_URL not configured")

    if not api_key:
        logger.error("es_sync missing RDA_GATEWAY_SERVICE_API_KEY in app_settings")
        raise HTTPException(status_code=500, detail="RDA_GATEWAY_SERVICE_API_KEY not configured")

    try:
        resp = requests.post(url, headers={"x-api-key": api_key}, timeout=60)
    except requests.RequestException as exc:
        logger.exception("es_sync request failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    logger.info("es_sync upstream returned status=%s content_length=%s", resp.status_code, len(resp.content) if resp.content is not None else 0)

    if resp.status_code >= 400:
        excerpt = resp.text[:200] if resp.text else ""
        logger.error("es_sync upstream error: status=%s excerpt=%s", resp.status_code, excerpt)
        raise HTTPException(status_code=502, detail=f"upstream {resp.status_code}: {excerpt}")

    try:
        return resp.json()
    except ValueError:
        return {"status_code": resp.status_code, "content": resp.text}

