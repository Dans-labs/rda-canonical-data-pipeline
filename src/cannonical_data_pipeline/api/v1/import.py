from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from pathlib import Path
import tempfile
import zipfile
import subprocess
import os
import shutil
import logging as logger
from urllib.parse import quote
import time
import errno
import uuid

from src.cannonical_data_pipeline.infra.db import get_conn_params

router = APIRouter(prefix="", tags=["import"])

# File-based lock (POSIX atomic create) to prevent concurrent imports across processes
_repo_root = Path(__file__).resolve().parents[3]
_import_lock_path = _repo_root / ".import.lock"
_import_lock_fd = None


def _acquire_import_lock(timeout: float = 0):
    """Try to atomically create the lock file and write our pid.

    Returns the opened file descriptor if successful, or None if the lock is held.
    If a stale lock file is found (PID not alive), it will be removed and acquisition retried.
    timeout=0 => try once, >0 will retry until timeout seconds.
    """
    start = time.time()
    lock_path_str = str(_import_lock_path)
    while True:
        try:
            fd = os.open(lock_path_str, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}\n".encode())
                os.fsync(fd)
            except Exception:
                pass
            return fd
        except FileExistsError:
            # check if stale
            try:
                with open(lock_path_str, 'r') as f:
                    content = f.read().strip()
                    pid = int(content.splitlines()[0]) if content else None
            except Exception:
                pid = None

            if pid:
                try:
                    os.kill(pid, 0)
                    # process exists -> lock held
                    return None
                except OSError as e:
                    # ESRCH means no such process -> stale
                    if getattr(e, 'errno', None) == errno.ESRCH:
                        try:
                            os.unlink(lock_path_str)
                        except Exception:
                            pass
                        continue
                    else:
                        return None
            else:
                try:
                    os.unlink(lock_path_str)
                except Exception:
                    pass
                continue
        except Exception:
            return None

        if timeout > 0 and (time.time() - start) >= timeout:
            return None
        time.sleep(0.1)


def _release_import_lock(fd: int | None):
    try:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            if os.path.exists(str(_import_lock_path)):
                os.unlink(str(_import_lock_path))
        except Exception:
            pass
    finally:
        return



def _find_executable(name: str):
    # respect override
    env_path = os.environ.get(name.upper() + '_PATH')
    if env_path and Path(env_path).exists() and os.access(env_path, os.X_OK):
        return str(Path(env_path))
    # try PATH
    from shutil import which

    p = which(name)
    if p:
        return p
    # common unix locations
    common = [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/bin/{name}", f"/usr/lib/postgresql/*/bin/{name}"]
    import glob
    for cand in common:
        for p in glob.glob(cand):
            if Path(p).exists() and os.access(p, os.X_OK):
                return p
    return None


@router.post("/upload-data")
def upload_data(request: Request, file: UploadFile = File(...)):
    """Upload a ZIP archive containing SQL files and apply them to the database.

    Requirements and behavior:
    - The uploaded file must be a ZIP archive. The ZIP should contain one or more
      `.sql` files (plain text) that will be executed against the database.
    - The endpoint first creates a pre-apply database dump so the operation can be
      rolled back if applying the SQL fails.
    - Before applying to production, the dump is restored into a temporary
      validation database and all SQL files are executed against that temporary
      database (using `psql -v ON_ERROR_STOP=1`) to ensure they succeed. If any
      SQL fails during validation, the import is aborted and the real database is
      not modified.
    - Execution order: SQL files are executed in filename order (sorted by name).
      To avoid surprising lexicographic ordering, name your files with explicit
      numeric prefixes that reflect the intended execution order, for example:

        01-create-tables.sql
        02-insert-data.sql
        03-finalize.sql

      Using numeric prefixes is the simplest and most reliable way to ensure the
      files run in the correct sequence.

    Returns a JSON report with per-file results and a short note about naming.
    """
    logger.info("start import.upload_data: filename=%s client=%s", getattr(file, 'filename', None), getattr(request.client, 'host', 'unknown') if getattr(request, 'client', None) else 'unknown')

    # Acquire import lock to ensure only one import runs at a time
    fd = _acquire_import_lock(timeout=0)
    if fd is None:
        logger.warning("import.upload_zip: concurrent import in progress; rejecting request")
        raise HTTPException(status_code=429, detail="Another import is already in progress")

    try:
        # validate zip
        try:
            tmpdir = tempfile.mkdtemp(prefix='rda_import_')
            upload_path = Path(tmpdir) / (file.filename or 'upload.zip')
            with open(upload_path, 'wb') as f:
                shutil.copyfileobj(file.file, f)
        except Exception as exc:
            logger.exception("failed to save uploaded file: %s", exc)
            raise HTTPException(status_code=500, detail=f"failed to save upload: {exc}")

        if not zipfile.is_zipfile(upload_path):
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="uploaded file is not a valid zip archive")

        # extract
        try:
            with zipfile.ZipFile(upload_path, 'r') as z:
                z.extractall(tmpdir)
        except Exception as exc:
            shutil.rmtree(tmpdir, ignore_errors=True)
            logger.exception("failed to extract zip: %s", exc)
            raise HTTPException(status_code=500, detail=f"failed to extract zip: {exc}")

        # find sql files
        sql_files = []
        for p in Path(tmpdir).rglob('*.sql'):
            # ignore files inside __MACOSX etc.
            if '__MACOSX' in str(p):
                continue
            sql_files.append(p)

        if not sql_files:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="no .sql files found in archive")

        # sort by filename
        sql_files.sort(key=lambda p: p.name)
        logger.info("found %d sql files: %s", len(sql_files), [p.name for p in sql_files])

        # connection params
        params = get_conn_params()
        host = params.get('host')
        port = str(params.get('port'))
        dbname = params.get('dbname')
        user = params.get('user')
        password = params.get('password')

        # build conn uri and env
        if password:
            user_enc = quote(user, safe='') if user is not None else ''
            password_enc = quote(password, safe='') if password is not None else ''
            conn_uri = f"postgresql://{user_enc}:{password_enc}@{host}:{port}/{dbname}"
            env = os.environ.copy()
            env['PGPASSWORD'] = password
        else:
            conn_uri = None
            env = os.environ.copy()

        # find pg_dump and psql
        pg_dump = _find_executable('pg_dump')
        psql = _find_executable('psql')
        pg_restore = _find_executable('pg_restore')
        if not pg_dump or not psql:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail='pg_dump or psql not available in PATH or common locations')

        dump_file = Path(tmpdir) / 'pre_apply.dump'

        # create dump (custom format)
        try:
            dump_cmd = [pg_dump, '-F', 'c', '-f', str(dump_file)]
            if conn_uri:
                dump_cmd.extend(['-d', conn_uri])
            else:
                dump_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', dbname])

            logger.info("running pg_dump to create rollback dump: %s", ' '.join(str(x) for x in dump_cmd))
            proc = subprocess.run(dump_cmd, env=env, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                stderr = (proc.stderr or '').strip()
                logger.error("pg_dump failed: %s", stderr)
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"pg_dump failed: {stderr}")
        except subprocess.TimeoutExpired:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail="pg_dump timed out")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("unexpected error during pg_dump: %s", exc)
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(exc))

        # --- VALIDATION PHASE -------------------------------------------------
        # Create a temporary database, restore the dump into it and run each SQL
        # file against the temp DB to ensure they succeed before applying to
        # the real database. If validation fails, we abort and do not touch
        # production.
        temp_db = f"{dbname}_import_test_{uuid.uuid4().hex[:8]}"
        logger.info("creating temporary validation database: %s", temp_db)

        # connection target for admin commands (use 'postgres' database)
        if conn_uri:
            # build admin conn targeting 'postgres'
            admin_conn = f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/postgres"
            temp_conn = f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{temp_db}"
        else:
            admin_conn = None
            temp_conn = None

        # create temporary database
        try:
            create_cmd = [psql]
            if admin_conn:
                create_cmd.extend(['-d', admin_conn, '-c', f'CREATE DATABASE "{temp_db}"'])
            else:
                create_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', 'postgres', '-c', f'CREATE DATABASE "{temp_db}"'])

            logger.info("running create DB command: %s", ' '.join(create_cmd))
            proc = subprocess.run(create_cmd, env=env, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                stderr = (proc.stderr or '').strip()
                logger.error("failed to create temp db: %s", stderr)
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"failed to create temp db: {stderr}")

            # restore dump into temp DB
            restore_cmd = [pg_restore]
            if temp_conn:
                restore_cmd.extend(['-d', temp_conn, str(dump_file)])
            else:
                restore_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', temp_db, str(dump_file)])

            logger.info("restoring dump into temp db: %s", ' '.join(restore_cmd))
            proc = subprocess.run(restore_cmd, env=env, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                stderr = (proc.stderr or '').strip()
                logger.error("pg_restore into temp db failed: %s", stderr)
                # attempt to drop temp db
                try:
                    drop_cmd = [psql]
                    if admin_conn:
                        drop_cmd.extend(['-d', admin_conn, '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                    else:
                        drop_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', 'postgres', '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                    subprocess.run(drop_cmd, env=env, capture_output=True, text=True, timeout=30)
                except Exception:
                    pass
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"pg_restore into temp db failed: {stderr}")

            # run each SQL against temp DB with ON_ERROR_STOP to validate
            logger.info("validating sql files against temp db: %s", temp_db)
            for sql in sql_files:
                logger.info("validating sql file: %s", sql.name)
                validate_cmd = [psql, '-v', 'ON_ERROR_STOP=1']
                if temp_conn:
                    validate_cmd.extend(['-d', temp_conn, '-f', str(sql)])
                else:
                    validate_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', temp_db, '-f', str(sql)])

                proc = subprocess.run(validate_cmd, env=env, capture_output=True, text=True, timeout=300)
                if proc.returncode != 0:
                    stderr = (proc.stderr or '').strip()
                    logger.error("validation failed for %s: %s", sql.name, stderr)
                    # drop temp DB
                    try:
                        drop_cmd = [psql]
                        if admin_conn:
                            drop_cmd.extend(['-d', admin_conn, '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                        else:
                            drop_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', 'postgres', '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                        subprocess.run(drop_cmd, env=env, capture_output=True, text=True, timeout=30)
                    except Exception:
                        pass
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise HTTPException(status_code=400, detail=f"validation failed for {sql.name}: {stderr}")

            # validation succeeded; drop temp DB
            try:
                drop_cmd = [psql]
                if admin_conn:
                    drop_cmd.extend(['-d', admin_conn, '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                else:
                    drop_cmd.extend(['-h', host, '-p', port, '-U', user, '-d', 'postgres', '-c', f'DROP DATABASE IF EXISTS "{temp_db}"'])
                subprocess.run(drop_cmd, env=env, capture_output=True, text=True, timeout=30)
            except Exception:
                logger.warning("failed to drop temp db %s; it may need manual cleanup", temp_db)

            logger.info("validation passed for all sql files")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("unexpected error during validation phase: %s", exc)
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(exc))
        # ---------------------------------------------------------------------

        # apply SQL files one by one
        results = []
        failed = False
        failed_info = None
        try:
            for sql in sql_files:
                logger.info("executing sql file: %s", sql.name)

                # read and log the SQL text that will be executed (truncate if very large)
                sql_text = None
                try:
                    sql_text = sql.read_text()
                except Exception:
                    try:
                        with open(sql, 'r', encoding='utf-8', errors='replace') as _f:
                            sql_text = _f.read()
                    except Exception as _e:
                        logger.warning("could not read sql file %s for logging: %s", sql.name, _e)
                        sql_text = None

                if sql_text is not None:
                    MAX_SQL_LOG = 10000
                    if len(sql_text) > MAX_SQL_LOG:
                        logger.info("sql content for %s (truncated to %d chars):\n%s", sql.name, MAX_SQL_LOG, sql_text[:MAX_SQL_LOG])
                    else:
                        logger.info("sql content for %s:\n%s", sql.name, sql_text)

                cmd = [psql]
                if conn_uri:
                    cmd.extend(['-d', conn_uri, '-f', str(sql)])
                else:
                    cmd.extend(['-h', host, '-p', port, '-U', user, '-d', dbname, '-f', str(sql)])

                proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
                out = (proc.stdout or '').strip()
                err = (proc.stderr or '').strip()
                ok = proc.returncode == 0
                results.append({'file': sql.name, 'returncode': proc.returncode, 'stdout': out, 'stderr': err, 'ok': ok})
                if not ok:
                    failed = True
                    failed_info = {'file': sql.name, 'returncode': proc.returncode, 'stderr': err}
                    logger.error("sql failed: %s rc=%s stderr=%s", sql.name, proc.returncode, err)
                    break

        except subprocess.TimeoutExpired as exc:
            failed = True
            failed_info = {'error': 'timeout', 'details': str(exc)}
            logger.exception("psql timed out: %s", exc)
        except Exception as exc:
            failed = True
            failed_info = {'error': 'exception', 'details': str(exc)}
            logger.exception("unexpected error while executing sql files: %s", exc)

        # rollback on failure
        if failed:
            logger.info("attempting rollback from dump: %s", dump_file)
            if not pg_restore:
                logger.error("pg_restore not available; cannot rollback automatically")
                # return failure report; leave dump in tmpdir for manual recovery
                resp = {'success': False, 'error': failed_info, 'results': results, 'rollback': 'pg_restore not available', 'dump_path': str(dump_file)}
                return resp

            try:
                restore_cmd = [pg_restore, '--clean', '--if-exists', '-d']
                if conn_uri:
                    restore_cmd.append(conn_uri)
                else:
                    restore_cmd.extend([f"postgresql://{user}@{host}:{port}/{dbname}"])
                restore_cmd.append(str(dump_file))
                logger.info("running pg_restore: %s", ' '.join(restore_cmd))
                proc = subprocess.run(restore_cmd, env=env, capture_output=True, text=True, timeout=600)
                if proc.returncode != 0:
                    logger.error("pg_restore failed: %s", proc.stderr)
                    resp = {'success': False, 'error': failed_info, 'results': results, 'rollback': 'failed', 'rollback_stderr': proc.stderr, 'dump_path': str(dump_file)}
                    return resp
                else:
                    logger.info("rollback successful")
                    resp = {'success': False, 'error': failed_info, 'results': results, 'rollback': 'restored', 'dump_path': str(dump_file)}
                    return resp
            except subprocess.TimeoutExpired:
                logger.exception("pg_restore timed out")
                return {'success': False, 'error': failed_info, 'results': results, 'rollback': 'timeout', 'dump_path': str(dump_file)}
            except Exception as exc:
                logger.exception("unexpected error during rollback: %s", exc)
                return {'success': False, 'error': failed_info, 'results': results, 'rollback': 'exception', 'details': str(exc), 'dump_path': str(dump_file)}

        # success
        # cleanup dump and temp files
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

        note = (
            "Files are executed in filename order. "
            "Use explicit numeric prefixes (e.g. 01-, 02-) to guarantee ordering."
        )
        return {'success': True, 'results': results, 'note': note}
    finally:
        # always release import lock and close uploaded file
        try:
            _release_import_lock(fd)
        except Exception:
            pass
        try:
            file.file.close()
        except Exception:
            pass

