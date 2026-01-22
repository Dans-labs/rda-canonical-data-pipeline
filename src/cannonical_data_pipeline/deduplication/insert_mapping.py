import csv
import json
import os
import sys
import traceback
from pathlib import Path

import re
import io

import psycopg2

from cannonical_data_pipeline.infra.commons import app_settings
from src.cannonical_data_pipeline.infra.db import get_conn_params


def insert_mapping_csv(csv_path: str, dry_run=False):
    """Read CSV and insert into institution_mapping(original, normalized).

    If dry_run=True the function only parses the CSV and returns a count without connecting to DB.

    Returns a dict report: {inserted: int, errors: [str], error: None or msg, details: optional}
    """
    report = {'inserted': 0, 'errors': [], 'error': None, 'auto_fixed': 0, 'auto_fixed_examples': []}

    def _try_split_concatenated(orig: str):
        """Heuristic: if a field contains two concatenated values (no comma), try to split where a lower->Upper transition occurs.

        Returns (orig_part, norm_part) or None if not found.
        """


    csv_file = Path(csv_path)
    try:
        raw_text = csv_file.read_text(encoding='utf-8')
    except Exception as exc:
        report['error'] = f"Failed to read CSV: {exc}"
        report['details'] = traceback.format_exc()
        return report

    repaired_text = re.sub(r'"\s*(?=[A-Za-z0-9])', '",', raw_text)
    repair_applied = repaired_text != raw_text
    try:
        print(f"[debug] csv_repair_applied={repair_applied}", file=sys.stderr)
    except Exception:
        pass

    # Use StringIO for csv parsing
    fobj = io.StringIO(repaired_text)

    # If dry_run, just parse and count rows to validate CSV
    if dry_run:
        try:
            count = 0
            reader = csv.DictReader(fobj, delimiter=',', quotechar='"')
            for _ in reader:
                count += 1
            report['inserted'] = 0
            report['found_rows'] = count
            return report
        except Exception as exc:
            report['error'] = f"Failed to parse CSV in dry-run: {exc}"
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

        # Reset StringIO cursor to start for parsing
        fobj.seek(0)
        reader = csv.DictReader(fobj, delimiter=',', quotechar='"')

        for row in reader:
            try:
                orig = row.get('original')
                norm = row.get('normalized')
                # If normalized is missing, try heuristic split from original
                if (norm is None or str(norm).strip() == '') and isinstance(orig, str):
                    split = _try_split_concatenated(orig)
                    if split:
                        orig, norm = split
                        report['auto_fixed'] += 1
                        if len(report['auto_fixed_examples']) < 5:
                            report['auto_fixed_examples'].append({'before': row, 'after': {'original': orig, 'normalized': norm}})
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
    # Resolve mapping path from Dynaconf setting and ensure it exists before running
    try:
        mapping_cfg = app_settings.data_institution_mapping
    except Exception:
        mapping_cfg = None

    resolved_path = None
    if mapping_cfg:
        mc = mapping_cfg
        if isinstance(mc, str) and mc.startswith('@format '):
            mc = mc[len('@format '):]
        if '{env[BASE_DIR]}' in mc:
            base = os.environ.get('BASE_DIR', str(Path(__file__).resolve().parents[3]))
            mc = mc.replace('{env[BASE_DIR]}', base)
        resolved_path = Path(mc).resolve()
        if not resolved_path.exists():
            print(f"CSV file not found at resolved path: {resolved_path}", file=sys.stderr)
            sys.exit(1)
    else:
        print("app_settings.data_institution_mapping is not configured", file=sys.stderr)
        sys.exit(1)

    # Run a dry-run to validate the CSV and report
    res = insert_mapping_csv(csv_path=resolved_path, dry_run=False)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    # exit non-zero on error
    if res.get('error'):
        sys.exit(1)
    sys.exit(0)
