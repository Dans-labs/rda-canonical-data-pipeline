import json
import sys

import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


def table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s);", (f"{schema}.{table}",))
    return cur.fetchone()[0] is not None


def column_exists(cur, schema: str, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s;",
        (schema, table, column),
    )
    return cur.fetchone() is not None


def table_has_primary_key(cur, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.table_constraints WHERE table_schema=%s AND table_name=%s AND constraint_type='PRIMARY KEY';",
        (schema, table),
    )
    return cur.fetchone() is not None


def apply_add_columns(conn_params=None, schema: str = "public"):
    """Apply ALTER/UPDATE statements to deduplicated_institutions_kb.

    This function will:
      - add uuid_country VARCHAR if missing
      - add uuid_deprecated VARCHAR if missing
      - add id SERIAL PRIMARY KEY if missing and the table has no primary key
      - update uuid_country from institution_country when possible

    It runs checks so repeated invocations are safe.

    Returns a dict with details of actions, skipped items and any errors.
    """
    params = conn_params or get_conn_params()
    report = {"success": False, "executed": [], "skipped": [], "errors": []}

    conn = None
    try:
        conn = psycopg2.connect(**params)
        with conn.cursor() as cur:
            tbl = "deduplicated_institutions_kb"

            # Check table exists
            if not table_exists(cur, schema, tbl):
                report["errors"].append(f"table {schema}.{tbl} does not exist")
                return report

            # Step 1: uuid_country
            if not column_exists(cur, schema, tbl, "uuid_country"):
                try:
                    cur.execute(f"ALTER TABLE {schema}.{tbl} ADD COLUMN uuid_country VARCHAR;")
                    report["executed"].append("ADD COLUMN uuid_country")
                except Exception as e:
                    report["errors"].append(f"failed to add column uuid_country: {e}")
                    conn.rollback()
            else:
                report["skipped"].append("uuid_country already exists")

            # Step 2: uuid_deprecated
            if not column_exists(cur, schema, tbl, "uuid_deprecated"):
                try:
                    cur.execute(f"ALTER TABLE {schema}.{tbl} ADD COLUMN uuid_deprecated VARCHAR;")
                    report["executed"].append("ADD COLUMN uuid_deprecated")
                except Exception as e:
                    report["errors"].append(f"failed to add column uuid_deprecated: {e}")
                    conn.rollback()
            else:
                report["skipped"].append("uuid_deprecated already exists")

            # Step 3: id SERIAL PRIMARY KEY
            has_id_col = column_exists(cur, schema, tbl, "id")
            has_pk = table_has_primary_key(cur, schema, tbl)
            if not has_id_col and not has_pk:
                try:
                    cur.execute(f"ALTER TABLE {schema}.{tbl} ADD COLUMN id SERIAL PRIMARY KEY;")
                    report["executed"].append("ADD COLUMN id SERIAL PRIMARY KEY")
                except Exception as e:
                    report["errors"].append(f"failed to add id primary key: {e}")
                    conn.rollback()
            elif has_id_col:
                report["skipped"].append("id column already exists")
            else:
                # id missing but table already has a PK; add id column without PK to avoid conflict
                try:
                    cur.execute(f"ALTER TABLE {schema}.{tbl} ADD COLUMN id SERIAL;")
                    report["executed"].append("ADD COLUMN id SERIAL (no PK - existing PK present)")
                except Exception as e:
                    report["errors"].append(f"failed to add id column (no PK): {e}")
                    conn.rollback()

            # Step 4: update uuid_country from institution_country if that table exists
            if table_exists(cur, schema, "institution_country"):
                try:
                    update_sql = (
                        f"UPDATE {schema}.{tbl} d"
                        " SET uuid_country = ic.uuid_country"
                        " FROM {schema}.institution_country ic"
                        " WHERE d.uuid_institution = ic.uuid_institution;"
                    )
                    # fix formatting with schema
                    update_sql = update_sql.replace("{schema}", schema)
                    cur.execute(update_sql)
                    report["executed"].append("UPDATE uuid_country from institution_country")
                except Exception as e:
                    report["errors"].append(f"failed to update uuid_country: {e}")
                    conn.rollback()
            else:
                report["skipped"].append("institution_country table does not exist; skipping update")

        # commit if no fatal errors
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
    res = apply_add_columns()
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
    sys.stdout.flush()

