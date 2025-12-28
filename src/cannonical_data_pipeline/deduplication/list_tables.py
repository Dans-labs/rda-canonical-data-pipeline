import json
import psycopg2

from src.cannonical_data_pipeline.infra.db import get_conn_params


def list_tables(conn_params=None):
    """Return a dict report with a list of public base table names in the DB."""
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
        return {"tables": tables, "error": None}
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

