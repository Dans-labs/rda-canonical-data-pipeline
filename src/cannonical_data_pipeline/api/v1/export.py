from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
import subprocess
import os
import zlib
import logging as logger
from urllib.parse import quote

from src.cannonical_data_pipeline.infra.db import get_conn_params



router = APIRouter(prefix="", tags=["export"])


@router.get(
    "/db",
    summary="Export database (gzipped SQL dump)",
    response_description="A gzipped SQL dump streamed as application/gzip",
)
def export_db(
    request: Request,
    dump_type: str = Query(
        "both",
        regex="^(schema|data|both)$",
        description="Which part to dump: 'schema', 'data', or 'both'",
        examples={
            "full": {"summary": "Full dump (schema + data)", "value": "both"},
            "schema_only": {"summary": "Schema only", "value": "schema"},
            "data_only": {"summary": "Data only", "value": "data"},
        },
    ),
    force_inserts: bool = Query(
        False,
        description="When true, add --inserts and --column-inserts to pg_dump for any dump that includes data (data or both).",
        examples={"true": {"summary": "Force INSERT-style output", "value": True}},
    ),
):
    logger.info("start export_db: dump_type=%s force_inserts=%s", dump_type, force_inserts)
    """Stream a gzipped pg_dump of the Postgres database on-the-fly.

    Query parameter:
    - dump_type: 'schema' (schema-only), 'data' (data-only), 'both' (default full dump)

    The endpoint runs `pg_dump` and streams its stdout through an in-memory gzip compressor
    to the client, avoiding temporary files.
    """
    if dump_type not in ("schema", "data", "both"):
        raise HTTPException(status_code=400, detail="dump_type must be one of: schema, data, both")

    params = get_conn_params()
    host = params.get('host')
    port = str(params.get('port'))
    dbname = params.get('dbname')
    user = params.get('user')
    password = params.get('password')

    # Log that the endpoint was accessed (include client IP when available)
    client = getattr(request, 'client', None)
    client_addr = getattr(client, 'host', 'unknown') if client else 'unknown'
    logger.info("Export requested by %s: db=%s dump_type=%s", client_addr, dbname, dump_type)

    # track total gzipped bytes streamed (will be logged on completion)
    total_bytes = 0

    # helper to find pg_dump (respect PG_DUMP_PATH override)
    def _find_executable(name: str):
        path_env = os.environ.get('PATH', '')
        for d in path_env.split(os.pathsep):
            if not d:
                continue
            candidate = Path(d) / name
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
            if os.name == 'nt':
                candidate_exe = candidate.with_suffix('.exe')
                if candidate_exe.exists() and os.access(candidate_exe, os.X_OK):
                    return str(candidate_exe)
        return None

    env_pg_dump = os.environ.get('PG_DUMP_PATH')
    if env_pg_dump and Path(env_pg_dump).exists() and os.access(env_pg_dump, os.X_OK):
        pg_dump_path = str(Path(env_pg_dump))
    else:
        pg_dump_path = _find_executable('pg_dump')

    # fallback common locations inside container
    if not pg_dump_path:
        # include common Linux, Debian/Ubuntu locations and common macOS Homebrew locations
        common_candidates = [
            '/usr/bin/pg_dump',
            '/usr/local/bin/pg_dump',
            '/bin/pg_dump',
            '/opt/homebrew/bin/pg_dump',
            '/usr/local/pgsql/bin/pg_dump',
        ]
        import glob
        pg_lib_globs = glob.glob('/usr/lib/postgresql/*/bin/pg_dump')
        for p in pg_lib_globs:
            common_candidates.append(p)
        for cand in common_candidates:
            try:
                cpath = Path(cand)
                if cpath.exists() and os.access(str(cpath), os.X_OK):
                    pg_dump_path = str(cpath)
                    break
            except Exception:
                continue

    if not pg_dump_path:
        raise HTTPException(status_code=500, detail='pg_dump not found on PATH or common locations; install postgresql-client in the container or set PG_DUMP_PATH')

    # Build connection args and pg_dump command
    # If password is available, use a connection URI so pg_dump won't prompt for password.
    # Percent-encode username/password to safely include characters like '@' or '#'
    user_enc = ''
    password_enc = ''
    connection_args = []
    if password:
        user_enc = quote(user, safe='') if user is not None else ''
        password_enc = quote(password, safe='') if password is not None else ''
        # Use the postgresql URI form; note that embedding passwords in args is generally fine inside containers
        conn_uri = f"postgresql://{user_enc}:{password_enc}@{host}:{port}/{dbname}"
        connection_args = ['-d', conn_uri]
    else:
        connection_args = ['-h', host, '-p', port, '-U', user, '-d', dbname]

    # base command
    cmd = [pg_dump_path] + connection_args + ['-F', 'p']

    # Log the chosen pg_dump and its version for diagnostics
    try:
        ver_proc = subprocess.run([pg_dump_path, '--version'], capture_output=True, text=True, check=False)
        logger.info("Using pg_dump at %s; version: %s", pg_dump_path, ver_proc.stdout.strip() or ver_proc.stderr.strip())
    except Exception as e:
        logger.warning("Failed to run pg_dump --version: %s", e)

    # Log command (mask password in logs)
    try:
        logged_cmd = list(cmd)
        if password and logged_cmd:
            # mask both raw password and percent-encoded password if present in the command
            try:
                logged_cmd = [s.replace(password, '*****').replace(password_enc, '*****') for s in logged_cmd]
            except Exception:
                logged_cmd = [s.replace(password, '*****') for s in logged_cmd]
        logger.debug("pg_dump command: %s", ' '.join(logged_cmd))
    except Exception:
        pass

    # set dump mode flags on the main command
    if dump_type == 'schema':
        cmd.append('-s')
    elif dump_type == 'data':
        # data-only dump: request only data (no schema)
        cmd.append('-a')
        # Use INSERT-style output instead of COPY for data-only dumps
        cmd.extend(['--inserts', '--column-inserts'])
    else:  # both
        # full dump: include schema and data; prefer INSERT-style output instead of COPY
        # (this makes restore more portable when COPY isn't desired)
        cmd.extend(['--inserts', '--column-inserts'])

    # Add --inserts/--column-inserts only if explicitly requested by the caller
    # (force_inserts) and the requested dump includes data (data or both).
    if force_inserts and dump_type in ('data', 'both'):
        # avoid duplicating flags if already present (data-only already adds them)
        if '--inserts' not in cmd:
            cmd.extend(['--inserts', '--column-inserts'])

    # --- PRE-FLIGHT CHECK -------------------------------------------------
    # Run a short, bounded pg_dump to validate connectivity and auth before
    # returning a StreamingResponse. Doing this avoids raising an HTTPException
    # after the response has started (which leads to ASGI "response already started" errors).
    env = os.environ.copy()
    if password:
        env['PGPASSWORD'] = password

    # Construct a safe preflight command that uses only the connection args and a schema-only flag.
    # This avoids appending -s to a command that already has -a (--data-only), which caused
    # the error "options -s/--schema-only and -a/--data-only cannot be used together".
    preflight_cmd = [pg_dump_path] + connection_args + ['-s']

    try:
        pre = subprocess.run(preflight_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, timeout=8)
        if pre.returncode != 0:
            stderr = (pre.stderr or '').strip()
            logger.error("pg_dump preflight stderr: %s", stderr)
            # Return a clear 500 before starting streaming
            raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr}")
    except subprocess.TimeoutExpired:
        logger.error("pg_dump preflight timed out (host reachable but dump slow?)")
        raise HTTPException(status_code=500, detail="pg_dump preflight timed out")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during pg_dump preflight: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    # ---------------------------------------------------------------------

    # streaming generator: run pg_dump and gzip-compress on the fly
    def stream_generator():
        logger.debug("force_inserts=%s included in pg_dump flags", force_inserts)
        logger.info("Starting export for db=%s dump_type=%s; client=%s", dbname, dump_type, client_addr)
        nonlocal total_bytes

        def _safe_read(f):
            try:
                if f is None:
                    return b''
                return f.read()
            except Exception:
                return b''

        proc = None
        started = False
        comp = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

            # start compressor
            comp = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=16 + zlib.MAX_WBITS)

            # read and stream
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                # mark started when first actual compressed chunk will be yielded
                if not started:
                    started = True
                out = comp.compress(chunk)
                if out:
                    total_bytes += len(out)
                    yield out

            # flush compressor
            tail = comp.flush()
            if tail:
                total_bytes += len(tail)
                yield tail

            # wait for process
            returncode = proc.wait()
            if returncode != 0:
                stderr = _safe_read(proc.stderr).decode(errors='ignore') if proc.stderr else '(no stderr)'
                logger.error("pg_dump exited with code %s; stderr=%s", returncode, stderr)
                # cannot change response after started; just log and return
                logger.info("Export ended with non-zero returncode; bytes_streamed=%d; client=%s", total_bytes, client_addr)
                return

        except HTTPException as he:
            # If we've already started streaming, we must not raise an HTTPException (response already started).
            # Instead, attempt to flush the gzip footer, log and return.
            logger.warning("pg_dump HTTPException during streaming: %s", he.detail if hasattr(he, 'detail') else str(he))
            if started:
                try:
                    if comp is not None:
                        tail = comp.flush()
                        if tail:
                            total_bytes += len(tail)
                            try:
                                yield tail
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    if proc is not None:
                        proc.kill()
                except Exception:
                    pass
                logger.info("Export aborted after HTTPException; bytes_streamed=%d; client=%s", total_bytes, client_addr)
                return
            # If streaming hasn't started yet, re-raise so FastAPI can return the HTTP error
            raise
        except BaseException as exc:
            # never allow exceptions to escape after streaming started
            logger.exception("Export stream error: %s", exc)
            if started and comp is not None:
                try:
                    tail = comp.flush()
                    if tail:
                        total_bytes += len(tail)
                        try:
                            yield tail
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    if proc is not None:
                        proc.kill()
                except Exception:
                    pass
                logger.info("Export aborted after streaming started; bytes_streamed=%d", total_bytes)
                return
            # streaming not started: re-raise as HTTPException so FastAPI returns error
            try:
                if proc is not None:
                    proc.kill()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=str(exc))
        finally:
            try:
                if proc is not None:
                    if getattr(proc, 'stdout', None) is not None and hasattr(proc.stdout, 'close'):
                        try:
                            proc.stdout.close()
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                if proc is not None:
                    if getattr(proc, 'stderr', None) is not None and hasattr(proc.stderr, 'close'):
                        try:
                            proc.stderr.close()
                        except Exception:
                            pass
            except Exception:
                pass
            logger.debug("Export generator finalised for db=%s dump_type=%s; client=%s; bytes_streamed=%d", dbname, dump_type, client_addr, total_bytes)

    filename = f"{dbname}.sql.gz" if dump_type == 'both' else f"{dbname}_{dump_type}.sql.gz"
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return StreamingResponse(stream_generator(), media_type='application/gzip', headers=headers)
