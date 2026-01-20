import json
import sys

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


SQL_UPDATE = """
WITH normalized_uuids AS (
    SELECT
        MIN(uuid_institution) AS normalized_uuid,
        institution,
        uuid_country
    FROM deduplicated_institutions_kb
    GROUP BY institution, uuid_country
),
records_to_update AS (
    SELECT
        inst.id,
        inst.uuid_institution AS old_uuid,
        norm.normalized_uuid
    FROM deduplicated_institutions_kb inst
    JOIN normalized_uuids norm
        ON inst.institution = norm.institution
        AND (
            (inst.uuid_country IS NULL AND norm.uuid_country IS NULL)
            OR (inst.uuid_country = norm.uuid_country)
        )
    WHERE 
        inst.was_deduplicated = TRUE
        AND (inst.uuid_institution IS DISTINCT FROM norm.normalized_uuid)
)

UPDATE deduplicated_institutions_kb inst
SET 
    uuid_deprecated = r.old_uuid,
    uuid_institution = r.normalized_uuid
FROM records_to_update r
WHERE inst.id = r.id;
"""


def table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s);", (f"{schema}.{table}",))
    return cur.fetchone()[0] is not None


def column_exists(cur, schema: str, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s;",
        (schema, table, column),
    )
    return cur.fetchone() is not None


def apply_update_uuids(conn_params=None, schema: str = "public"):
    """Run the UUID normalization/update process and return a JSON-serializable report.

    The function verifies the existence of `deduplicated_institutions_kb` and the `id` column
    (required to match rows), then executes the CTE + UPDATE. It returns a dict with keys:
      - success: bool
      - updated: number of rows updated (int) if successful
      - executed: list of statements or descriptions
      - skipped: list of skipped checks
      - errors: list of error messages

    This function does not attempt to create missing columns or tables; it will fail fast with
    a helpful error message so the caller can prepare the schema first.
    """
    params = conn_params or get_conn_params()
    report = {"success": False, "updated": 0, "executed": [], "skipped": [], "errors": []}

    conn = None
    try:
        conn = psycopg2.connect(**params)
        with conn.cursor() as cur:
            tbl = "deduplicated_institutions_kb"

            # Check table exists
            if not table_exists(cur, schema, tbl):
                report["errors"].append(f"table {schema}.{tbl} does not exist")
                return report

            # Ensure id primary key/column exists
            if not column_exists(cur, schema, tbl, "id"):
                report["errors"].append(f"table {schema}.{tbl} does not have an 'id' column; aborting")
                return report

            # Optionally check uuid_institution column exists
            if not column_exists(cur, schema, tbl, "uuid_institution"):
                report["errors"].append(f"table {schema}.{tbl} does not have 'uuid_institution' column; aborting")
                return report

            # Execute the UPDATE statement
            try:
                cur.execute(SQL_UPDATE)
                updated = cur.rowcount if cur.rowcount is not None else 0
                report["executed"].append("CTE_UPDATE")
                report["updated"] = updated
            except Exception as e:
                # capture DB error (e.g., transaction aborted etc.) and return
                report["errors"].append(f"failed to execute update: {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                return report

        # commit and finalise
        try:
            conn.commit()
            report["success"] = len(report["errors"]) == 0
        except Exception as e:
            report["errors"].append(f"commit failed: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    except Exception as exc:
        report["errors"].append(str(exc))
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    return report


if __name__ == '__main__':
    res = apply_update_uuids()
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
    sys.stdout.flush()

