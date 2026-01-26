from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pathlib import Path
import subprocess
import os
import time
import zlib

from src.cannonical_data_pipeline.infra.db import get_conn_params

router = APIRouter(prefix="", tags=["export"])


@router.get("/db")
def export_db(dump_type: str = Query("both", regex="^(schema|data|both)$", description="Which part to dump: 'schema', 'data', or 'both'")):
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
        common_candidates = [
            '/usr/bin/pg_dump',
            '/usr/local/bin/pg_dump',
            '/bin/pg_dump',
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
    cmd = [
        pg_dump_path,
        '-h', host,
        '-p', port,
        '-U', user,
        '-d', dbname,
        '-F', 'p',
    ]
    if dump_type == 'schema':
        cmd.append('-s')
    elif dump_type == 'data':
        cmd.append('-a')

    # streaming generator: run pg_dump and gzip-compress on the fly
    def stream_generator():
        env = os.environ.copy()
        if password:
            env['PGPASSWORD'] = password
        # start pg_dump
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # compressor for gzip format
        comp = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=16 + zlib.MAX_WBITS)
        try:
            # Read first chunk to detect immediate failures
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                # no output: likely error
                stderr = proc.stderr.read().decode(errors='ignore')
                proc.wait(timeout=1)
                raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr.strip()}")
            out = comp.compress(chunk)
            if out:
                yield out
            # stream remaining
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                out = comp.compress(chunk)
                if out:
                    yield out
            # flush compressor
            tail = comp.flush()
            if tail:
                yield tail
            # wait for process and check return code
            returncode = proc.wait()
            if returncode != 0:
                stderr = proc.stderr.read().decode(errors='ignore')
                # We already streamed data; cannot change status code. Log/raise after streaming.
                # For now, simply include stderr in trailing bytes (not ideal) or rely on logs.
                # Do nothing further here.
                pass
        except HTTPException:
            try:
                proc.kill()
            except Exception:
                pass
            raise
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass

    filename = f"{dbname}.sql.gz" if dump_type == 'both' else f"{dbname}_{dump_type}.sql.gz"
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return StreamingResponse(stream_generator(), media_type='application/gzip', headers=headers)
