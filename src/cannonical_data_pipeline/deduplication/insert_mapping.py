import csv
import json
import os
import sys
import traceback
from pathlib import Path

import re
import io

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


def insert_mapping_csv(csv_path=None, dry_run=False):
    """Read CSV and insert into institution_mapping(original, normalized).

    If dry_run=True the function only parses the CSV and returns a count without connecting to DB.

    Returns a dict report: {inserted: int, errors: [str], error: None or msg, details: optional}
    """
    report = {'inserted': 0, 'errors': [], 'error': None, 'auto_fixed': 0, 'auto_fixed_examples': []}

    def _try_split_concatenated(orig: str):
        """Heuristic: if a field contains two concatenated values (no comma), try to split where a lower->Upper transition occurs.

        Returns (orig_part, norm_part) or None if not found.
        """
        if not orig or len(orig) < 10:
            return None
        # find a position where a lowercase/number is followed by uppercase (likely concatenation boundary)
        m = re.search(r'(?<=[a-z0-9])(?=[A-Z])', orig)
        if not m:
            # try later occurrences: search for any such boundary after first 8 chars
            for i in range(8, len(orig)-8):
                if orig[i].islower() or orig[i].isdigit():
                    if orig[i+1].isupper():
                        idx = i+1
                        # ensure both parts are reasonably long
                        if idx > 5 and len(orig) - idx > 3:
                            left = orig[:idx].strip()
                            right = orig[idx:].strip()
                            return left, right
            return None
        idx = m.start()
        # Ensure sensible split (both parts have length)
        if idx < 3 or len(orig) - idx < 3:
            return None
        left = orig[:idx].strip()
        right = orig[idx:].strip()
        # Basic sanity: both parts contain letters
        if any(c.isalpha() for c in left) and any(c.isalpha() for c in right):
            return left, right
        return None
        # Fallback: detect if the start of the string repeats later (duplicated concatenation)
        try:
            compact = re.sub(r"\W+", '', orig).lower()
            if len(compact) > 20:
                # try to find a later position where a prefix of the compacted string appears
                max_prefix = min(40, len(compact)//2)
                for pref_len in range(max_prefix, 6, -1):
                    prefix = compact[:pref_len]
                    idx = compact.find(prefix, 5)
                    if idx > 5:
                        # map compact index back to original string index
                        # find the corresponding position in original by searching for the substring of prefix in orig
                        # use a heuristic search for the first occurrence after middle
                        orig_search = re.sub(r"\s+", ' ', orig)
                        # try to locate the prefix's first few chars in the original tail
                        tail_scan = re.sub(r"\W+", '', orig)
                        # find approximate split position by scanning original for a boundary where next chars match prefix start
                        for j in range(max(5, len(orig)//3), len(orig)-5):
                            # build candidate tail starting at j, compacted
                            candidate_compact = re.sub(r"\W+", '', orig[j:j+pref_len+20]).lower()
                            if candidate_compact.startswith(prefix[:min(10, len(prefix))]):
                                left = orig[:j].strip()
                                right = orig[j:].strip()
                                if len(left) > 3 and len(right) > 3:
                                    return left, right
                        # fallback if not found continue trying smaller prefix
        except Exception:
            pass
        return None

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

    # Read file content and apply safe repair for malformed lines where a closing quote is directly followed by a character
    try:
        raw_text = csv_file.read_text(encoding='utf-8')
    except Exception as exc:
        report['error'] = f"Failed to read CSV: {exc}"
        report['details'] = traceback.format_exc()
        return report

    # Repair heuristic: insert a missing comma after a closing quote if immediately followed by an alphanumeric (no comma)
    # Pattern: "\s*(?=[A-Za-z0-9])  -> replace with ",
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
