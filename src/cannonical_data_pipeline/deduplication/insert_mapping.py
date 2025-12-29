import csv
import json
import os
import sys
import traceback
from pathlib import Path

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


def insert_mapping_csv(csv_path=None, dry_run=False):
    """Read CSV and insert into institution_mapping(original, normalized).

    If dry_run=True the function only parses the CSV and returns a count without connecting to DB.

    Returns a dict report: {inserted: int, errors: [str], error: None or msg, details: optional}
    """
    report = {'inserted': 0, 'errors': [], 'error': None}

    # Resolve csv_file from provided CLI path or from app_settings.data_institution_mapping
    csv_file = None
    mapping_cfg = None
    # If CLI override provided, prefer that
    if csv_path:
        csv_file = Path(csv_path)
    else:
        # Prefer app_settings.data_institution_mapping
        try:
            from src.cannonical_data_pipeline.infra.commons import app_settings
            mapping_cfg = app_settings.get('data_institution_mapping')
        except Exception:
            mapping_cfg = None

        if mapping_cfg:
            # mapping_cfg may contain Dynaconf @format/template markers like '@format {env[BASE_DIR]}/path'
            try:
                mc = mapping_cfg
                if isinstance(mc, str) and mc.startswith('@format '):
                    mc = mc[len('@format '):]
                # substitute {env[BASE_DIR]} if present
                if isinstance(mc, str) and '{env[BASE_DIR]}' in mc:
                    base = os.environ.get('BASE_DIR')
                    if not base:
                        # repo root as fallback
                        base = str(Path(__file__).resolve().parents[3])
                    mc = mc.replace('{env[BASE_DIR]}', base)
                csv_candidate = Path(mc)
                # If relative path, resolve against repo root
                if not csv_candidate.is_absolute():
                    repo_root = Path(__file__).resolve().parents[3]
                    csv_candidate = (repo_root / csv_candidate).resolve()
                if csv_candidate.exists():
                    csv_file = csv_candidate
            except Exception:
                csv_file = None

    # If still not found, fall back to repo default path
    if csv_file is None:
        repo_root = Path(__file__).resolve().parents[3]
        default_candidate = repo_root / 'resources' / 'data' / 'mapping' / 'institution_mapping.csv'
        if default_candidate.exists():
            csv_file = default_candidate

    # Debug info: show resolved mapping_cfg and csv_file
    try:
        import sys as _sys
        _mapping_cfg_repr = repr(mapping_cfg)
        _resolved = str(csv_file) if csv_file is not None else None
        _exists = csv_file.exists() if csv_file is not None else False
        print(f"[debug] mapping_cfg={_mapping_cfg_repr} resolved={_resolved} exists={_exists}", file=_sys.stderr)
    except Exception:
        pass

    # Final existence check
    if csv_file is None or not csv_file.exists():
        tried = []
        if csv_path:
            tried.append(str(csv_path))
        if mapping_cfg:
            tried.append(str(mapping_cfg))
        tried.append(str(default_candidate if 'default_candidate' in locals() else 'resources/data/mapping/institution_mapping.csv'))
        report['error'] = f"CSV file not found. Tried paths: {tried}"
        return report

    # If dry_run, just parse and count rows to validate CSV
    if dry_run:
        try:
            count = 0
            with csv_file.open(newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter=',', quotechar='"')
                for _ in reader:
                    count += 1
            report['inserted'] = 0
            report['found_rows'] = count
            return report
        except Exception as exc:
            report['error'] = f"Failed to read CSV: {exc}"
            report['details'] = traceback.format_exc()
            return report

    # Real run: connect to DB and insert
    params = get_conn_params()
    conn = None
    try:
        conn = psycopg2.connect(**params)
        cur = conn.cursor()
        # Ensure the institution_mapping table exists and create a unique index on original
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS institution_mapping (
                    id SERIAL PRIMARY KEY,
                    original TEXT,
                    normalized TEXT
                )
                """
            )
            # Create a unique index to allow ON CONFLICT upsert on original
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS institution_mapping_original_idx ON institution_mapping (original)"
            )
            conn.commit()
        except Exception:
            # If table creation fails, rollback and continue; inserts will likely fail later and be reported
            try:
                conn.rollback()
            except Exception:
                pass

        with csv_file.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=',', quotechar='"')
            for row in reader:
                try:
                    orig = row.get('original')
                    norm = row.get('normalized')
                    # Skip rows without required data
                    if orig is None or norm is None:
                        report['errors'].append(f"missing columns in row: {row}")
                        continue
                    # Upsert: update normalized when original already exists
                    cur.execute(
                        """
                        INSERT INTO institution_mapping ("original", "normalized")
                        VALUES (%s, %s)
                        ON CONFLICT (original) DO UPDATE SET normalized = EXCLUDED.normalized
                        """,
                        (orig, norm)
                    )
                    report['inserted'] += 1
                except Exception as exc_row:
                    # record the error and continue with next row
                    report['errors'].append(f"row error {row}: {exc_row}")
                    try:
                        conn.rollback()
                        cur = conn.cursor()
                    except Exception:
                        pass
        # commit all successful inserts (those not rolled back)
        try:
            conn.commit()
        except Exception as exc_commit:
            report['error'] = f"failed to commit: {exc_commit}"
            try:
                conn.rollback()
            except Exception:
                pass
    except Exception as exc:
        report['error'] = f"DB connection or processing failed: {exc}"
        report['details'] = traceback.format_exc()
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    return report


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Insert mapping CSV into institution_mapping (or dry-run)')
    parser.add_argument('--path', help='Path to CSV file (optional)')
    parser.add_argument('--dry-run', dest='dry_run', action='store_true', help='Only parse CSV and count rows')
    args = parser.parse_args()

    res = insert_mapping_csv(csv_path=args.path, dry_run=args.dry_run)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    # exit non-zero on error
    if res.get('error'):
        sys.exit(1)
    sys.exit(0)
