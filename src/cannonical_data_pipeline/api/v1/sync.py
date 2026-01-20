from fastapi import APIRouter, BackgroundTasks, HTTPException, Body, Query
from typing import Optional, Dict, Any
from datetime import datetime
import threading
import uuid

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
    with _schedules_lock:
        out = {name: {k: v for k, v in conf.items() if k != "timer" and k != "thread"} for name, conf in _schedules.items()}
    return out


@router.delete("/schedule")
def delete_schedule(name: str = Query(...)):
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

