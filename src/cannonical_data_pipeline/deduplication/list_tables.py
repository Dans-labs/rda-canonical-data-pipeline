import json
import psycopg2
from psycopg2 import sql

from src.cannonical_data_pipeline.infra.db import get_conn_params


def list_tables(conn_params=None):
    """Return a dict report with a list of public base table names and row counts in the DB."""
    params = conn_params or get_conn_params()
    conn = None
    try:
        conn = psycopg2.connect(**params)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            rows = cur.fetchall()
            tables = [r[0] for r in rows]

            # For each table, get the row count using safe identifier handling
            tables_with_counts = []
            for t in tables:
                try:
                    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}" ).format(sql.Identifier(t)))
                    cnt = cur.fetchone()[0]
                except Exception:
                    # If counting fails for any reason, report -1 to indicate unknown/error
                    cnt = -1
                tables_with_counts.append({"name": t, "rows": cnt})
        return {"tables": tables_with_counts, "error": None}
    except Exception as exc:
        return {"tables": [], "error": str(exc)}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def main(conn_params=None):
    report = list_tables(conn_params=conn_params)
    print(json.dumps(report, default=str))
    # exit non-zero if error
    if report.get("error"):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
