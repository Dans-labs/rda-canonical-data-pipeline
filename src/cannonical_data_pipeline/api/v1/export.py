from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
import subprocess
import os
import time
import zlib
import logging as logger

from src.cannonical_data_pipeline.infra.db import get_conn_params



router = APIRouter(prefix="", tags=["export"])


@router.get("/db")
def export_db(request: Request, dump_type: str = Query("both", regex="^(schema|data|both)$", description="Which part to dump: 'schema', 'data', or 'both'")):
    logger.info("Export requested by %s", dump_type)
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

    # Build pg_dump command
    # If password is available, use a connection URI so pg_dump won't prompt for password.
    if password:
        # Use the postgresql URI form; note that embedding passwords in args is generally fine inside containers
        conn_uri = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        cmd = [pg_dump_path, '-d', conn_uri, '-F', 'p']
    else:
        cmd = [pg_dump_path, '-h', host, '-p', port, '-U', user, '-d', dbname, '-F', 'p']

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
            # mask password if present in a URI
            logged_cmd = [s.replace(password, '*****') for s in logged_cmd]
        logger.debug("pg_dump command: %s", ' '.join(logged_cmd))
    except Exception:
        pass

    if dump_type == 'schema':
        cmd.append('-s')
    elif dump_type == 'data':
        # data-only dump: use --inserts and --column-inserts so INSERT statements are generated
        # This produces SQL that uses explicit INSERT ... (column list) VALUES (...) statements.
        # Keep -a (data-only) for compatibility, but add the more explicit insert options.
        cmd.append('-a')
        # Use long-form flags for clarity; these flags only affect data output and are safe
        # when streaming to stdout (we don't pass -f here to avoid writing files).
        cmd.extend(['--inserts', '--column-inserts'])

    # streaming generator: run pg_dump and gzip-compress on the fly
    def stream_generator():
         logger.info("Starting export for db=%s dump_type=%s; client=%s", dbname, dump_type, client_addr)
         nonlocal total_bytes
         import select
         # helper to safely read from a file-like object (some linters may not infer type)
         def _safe_read(f):
             try:
                 if f is None:
                     return b''
                 # read may return bytes
                 return f.read()
             except Exception:
                 return b''
         env = os.environ.copy()
         if password:
             env['PGPASSWORD'] = password
         proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

        # Preflight: read stderr non-blocking for a short time to detect immediate failures
         stderr_parts = []
         fd_err = proc.stderr.fileno()
         start = time.time()
         preflight_timeout = 1.0  # seconds to wait for immediate errors
         try:
             while True:
                 # check if there's stderr available
                 rlist, _, _ = select.select([fd_err], [], [], 0.1)
                 if rlist:
                     try:
                         chunk = os.read(fd_err, 64 * 1024)
                     except OSError:
                         break
                     if not chunk:
                         break
                     stderr_parts.append(chunk.decode(errors='ignore'))
                     joined = ''.join(stderr_parts).lower()
                     # look for immediate fatal signals
                     if any(k in joined for k in ('password', 'fe_sendauth', 'no password supplied',
                                                  'aborting because of server version mismatch', 'error')):
                         # drain remaining stderr
                         try:
                             while True:
                                 chunk = os.read(fd_err, 64 * 1024)
                                 if not chunk:
                                     break
                                 stderr_parts.append(chunk.decode(errors='ignore'))
                         except Exception:
                             pass
                         stderr = ''.join(stderr_parts).strip()
                         logger.error("pg_dump preflight stderr: %s", stderr)
                         try:
                             proc.kill()
                         except Exception:
                             pass
                         raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr}")
                 # if process exited during preflight, collect stderr and fail if non-zero
                 ret = proc.poll()
                 if ret is not None:
                     # drain remaining stderr
                     try:
                         while True:
                             chunk = os.read(fd_err, 64 * 1024)
                             if not chunk:
                                 break
                             stderr_parts.append(chunk.decode(errors='ignore'))
                     except Exception:
                         pass
                     stderr = ''.join(stderr_parts).strip()
                     if ret != 0 or stderr:
                         try:
                             proc.kill()
                         except Exception:
                             pass
                         logger.error("pg_dump exited during preflight with code=%s stderr=%s", ret, stderr)
                         raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr or f'return code {ret}'}")
                     break
                 # timeout if nothing decisive happens
                 if time.time() - start > preflight_timeout:
                     break

             # Now it's safe to start streaming stdout (no immediate fatal error)
             comp = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=16 + zlib.MAX_WBITS)

             # Read first chunk to detect "no output" case
             chunk = proc.stdout.read(64 * 1024)
             if not chunk:
                 try:
                     # try to collect any stderr for a helpful message
                     err_bytes = _safe_read(proc.stderr)
                     stderr = err_bytes.decode(errors='ignore') if err_bytes else "(no stderr available)"
                 except Exception:
                     stderr = "(no stderr available)"
                 logger.error("pg_dump produced no stdout; stderr=%s", stderr)
                 try:
                     proc.wait(timeout=1)
                 except Exception:
                     pass
                 raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr.strip()}")

             out = comp.compress(chunk)
             if out:
                 total_bytes += len(out)
                 yield out

             # stream remaining stdout
             while True:
                 chunk = proc.stdout.read(64 * 1024)
                 if not chunk:
                     break
                 out = comp.compress(chunk)
                 if out:
                     total_bytes += len(out)
                     yield out

             # flush compressor
             tail = comp.flush()
             if tail:
                 total_bytes += len(tail)
                 yield tail

             # log completion (number of gzipped bytes streamed)
             logger.info("Export completed for db=%s dump_type=%s; bytes_streamed=%d; client=%s", dbname, dump_type, total_bytes, client_addr)

             # wait and check return code (cannot change status if we've already sent data)
             returncode = proc.wait()
             if returncode != 0:
                 # log or include stderr in logs; do not raise here as response started
                 try:
                     err_bytes = _safe_read(proc.stderr)
                     stderr = err_bytes.decode(errors='ignore') if err_bytes else "(no stderr available)"
                 except Exception:
                     stderr = "(no stderr available)"
                 logger.error("pg_dump finished with non-zero exit code %s; stderr=%s", returncode, stderr)
                 # We cannot change the response status now; just return (stream will be possibly truncated).
                 logger.info("Export ended with non-zero exit; db=%s dump_type=%s; bytes_streamed=%d; client=%s", dbname, dump_type, total_bytes, client_addr)
                 return

         except HTTPException:
             try:
                 proc.kill()
             except Exception:
                 pass
             logger.info("Export failed for db=%s dump_type=%s; client=%s; bytes_streamed=%d", dbname, dump_type, client_addr, total_bytes)
             raise
         except Exception:
             try:
                 proc.kill()
             except Exception:
                 pass
             raise
         finally:
             try:
                 close_fn = getattr(proc, 'stdout', None)
                 if close_fn is not None:
                     close_method = getattr(close_fn, 'close', None)
                     if callable(close_method):
                         try:
                             close_method()
                         except Exception:
                             pass
             except Exception:
                 pass
             try:
                 close_fn = getattr(proc, 'stderr', None)
                 if close_fn is not None:
                     close_method = getattr(close_fn, 'close', None)
                     if callable(close_method):
                         try:
                             close_method()
                         except Exception:
                             pass
             except Exception:
                 pass
             # final log in case generator exits unexpectedly
             logger.debug("Export generator finalised for db=%s dump_type=%s; client=%s; bytes_streamed=%d", dbname, dump_type, client_addr, total_bytes)

    filename = f"{dbname}.sql.gz" if dump_type == 'both' else f"{dbname}_{dump_type}.sql.gz"
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return StreamingResponse(stream_generator(), media_type='application/gzip', headers=headers)
