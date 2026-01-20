import json
import sys

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


CREATE_SQL = """
DROP TABLE IF EXISTS deduplicated_institutions_kb;

CREATE TABLE deduplicated_institutions_kb AS
SELECT
    COALESCE(m."normalized", i.institution) AS institution,
    i.institution AS original_institution,
    CASE WHEN m."normalized" IS NOT NULL THEN TRUE ELSE FALSE END AS was_deduplicated,
    CASE WHEN m."normalized" IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END AS deduplication_timestamp,
    i.uuid_institution,
    i.english_name,
    i.parent_institution
FROM
    institution i
LEFT JOIN
    institution_mapping m
ON
    i.institution = m."original"
WHERE
    i.institution IS NOT NULL AND LENGTH(TRIM(i.institution)) > 0;
"""


def apply_deduplication(conn_params=None):
    """Connect to Postgres and execute the CREATE TABLE AS SELECT statement.

    Returns a dict with keys: success (bool), table (str), message (str), error (optional).
    """
    params = conn_params or get_conn_params()
    result = {"success": False, "table": "deduplicated_institutions_kb", "message": None, "error": None}

    conn = None
    try:
        conn = psycopg2.connect(**params)
        with conn.cursor() as cur:
            cur.execute(CREATE_SQL)
        conn.commit()
        result["success"] = True
        result["message"] = "Table 'deduplicated_institutions_kb' created/updated successfully."
    except Exception as exc:
        # try to include exception message
        result["error"] = str(exc)
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

    return result


if __name__ == '__main__':
    # run from CLI and print JSON result to stdout
    res = apply_deduplication()
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
    sys.stdout.flush()

