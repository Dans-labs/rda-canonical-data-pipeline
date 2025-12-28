import csv
import json
import sys
import traceback

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


def insert_mapping_csv(csv_path='resources/data/mapping/mapping.csv'):
    """Read CSV and insert into institution_mapping(original, normalized).

    Returns a dict report: {inserted: int, errors: [str], error: None or msg}
    """
    report = {'inserted': 0, 'errors': [], 'error': None}

    params = get_conn_params()
    conn = None
    try:
        conn = psycopg2.connect(**params)
        cur = conn.cursor()

        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=',', quotechar='"')
            for row in reader:
                try:
                    orig = row.get('original')
                    norm = row.get('normalized')
                    # Skip rows without required data
                    if orig is None or norm is None:
                        report['errors'].append(f"missing columns in row: {row}")
                        continue
                    cur.execute(
                        """
                        INSERT INTO institution_mapping ("original", "normalized")
                        VALUES (%s, %s)
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
    path = 'resources/data/mapping/mapping.csv'
    if len(sys.argv) > 1:
        path = sys.argv[1]

    res = insert_mapping_csv(path)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    # exit non-zero on error
    if res.get('error'):
        sys.exit(1)
    sys.exit(0)

