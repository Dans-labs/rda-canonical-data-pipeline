try:
    import psycopg2
    from psycopg2 import sql
except Exception:
    psycopg2 = None
    sql = None


from src.cannonical_data_pipeline.infra.db import get_conn_params


def get_table_columns(conn, table_name):
    """Return list of (column_name, data_type) for the given table in public schema."""
    q = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = %s
    ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(q, (table_name,))
        return cur.fetchall()


def find_duplicates_for_column(conn, table_name, column_name, data_type, case_insensitive=True):
    """Return list of (value, ids, count) where value appears more than once in the column.

    - For text-like columns and case_insensitive=True we compare LOWER(value).
    - ids is an array of primary key ids collected for that group (if id column exists).
    """
    # Skip checking columns that are primary key or are declared UNIQUE
    try:
        with conn.cursor() as _cur:
            _cur.execute(
                """
                SELECT tc.constraint_type
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = 'public' AND tc.table_name = %s AND kcu.column_name = %s
                """,
                (table_name, column_name),
            )
            constraint_rows = _cur.fetchall()
            for (ctype,) in constraint_rows:
                if ctype in ("PRIMARY KEY", "UNIQUE"):
                    # indicate caller should skip this column
                    return None

            # Additional check: look for unique indexes in pg_catalog (covers unique indexes not exposed as table_constraints)
            try:
                _cur.execute(
                    """
                    SELECT i.indisunique
                    FROM pg_index i
                    JOIN pg_class t ON t.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)
                    WHERE n.nspname = 'public' AND t.relname = %s AND a.attname = %s
                    """,
                    (table_name, column_name),
                )
                idx_rows = _cur.fetchall()
                for (indisunique,) in idx_rows:
                    if indisunique:
                        # unique index found; skip this column
                        return None
            except Exception:
                # If pg_catalog check fails for any reason, rollback to clear the aborted transaction and continue
                try:
                    conn.rollback()
                except Exception:
                    pass
                # don't block duplicate checking
                pass
    except Exception:
        # If constraint/index inspection fails, rollback and skip this column
        try:
            conn.rollback()
        except Exception:
            pass
        return None

    # Build value expression depending on type used for grouping
    is_text_like = data_type in ("character varying", "text", "character")
    if is_text_like and case_insensitive:
        val_expr = sql.SQL('LOWER({col}::text)').format(col=sql.Identifier(column_name))
        compare_clause = sql.SQL('LOWER({col}::text) = %s').format(col=sql.Identifier(column_name))
    else:
        val_expr = sql.SQL('({col})::text').format(col=sql.Identifier(column_name))
        compare_clause = sql.SQL('({col})::text = %s').format(col=sql.Identifier(column_name))

    results = []

    try:
        with conn.cursor() as cur:
            # Check if id column exists
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name='id'",
                (table_name,)
            )
            has_id = cur.fetchone() is not None

            id_expr = sql.Identifier('id') if has_id else sql.SQL('NULL')

            # Aggregate groups
            group_query = sql.SQL(
                "SELECT {val_expr} AS val, array_agg({id_expr}) AS ids, COUNT(*) AS cnt"
                " FROM {table}"
                " WHERE {col} IS NOT NULL"
                " GROUP BY {val_expr}"
                " HAVING COUNT(*) > 1"
                " ORDER BY cnt DESC"
                " LIMIT 100"
            ).format(
                val_expr=val_expr,
                id_expr=sql.SQL('{col}').format(col=id_expr) if isinstance(id_expr, sql.Identifier) else sql.SQL('NULL'),
                table=sql.Identifier(table_name),
                col=sql.Identifier(column_name),
            )

            cur.execute(group_query)
            groups = cur.fetchall()  # list of (val, ids, cnt)

            # If no groups found, return None so callers can omit the column entirely
            if not groups:
                return None
    except Exception:
        # On failure, rollback and indicate caller should skip this column
        try:
            conn.rollback()
        except Exception:
            pass
        return None

    # For each group, fetch the actual rows that match the grouped value
    try:
        with conn.cursor() as cur2:
            for val, ids, cnt in groups:
                try:
                    # fetch full rows for this group
                    fetch_sql = sql.SQL('SELECT * FROM {table} WHERE {cond} ORDER BY id').format(
                        table=sql.Identifier(table_name),
                        cond=compare_clause,
                    )
                    cur2.execute(fetch_sql, (val,))
                    rows = cur2.fetchall()
                    desc = [d[0] for d in cur2.description] if cur2.description else []
                    records = [dict(zip(desc, row)) for row in rows]
                except Exception:
                    # rollback the transaction so subsequent column checks can proceed
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    records = []

                results.append({'value': val, 'ids': ids, 'count': int(cnt), 'records': records})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None

    return results


def generate_duplicates_report(conn_params=None, table_name='poc', case_insensitive=True, only_with_duplicates=True, columns=None):
    """Connect to Postgres and return a structured duplicates report instead of printing.

    Additional options:
      - only_with_duplicates: if True, only include columns that have duplicates in the returned report
      - columns: optional list/tuple of column names to restrict the check to those columns
    """
    report = {'table': table_name, 'columns': {}, 'error': None}

    if psycopg2 is None:
        report['error'] = 'psycopg2 is required but not installed'
        return report

    params = conn_params or get_conn_params()
    conn = None
    try:
        conn = psycopg2.connect(**params)
    except Exception as exc:
        # Mask password when returning error details
        safe_params = {k: ('***' if k == 'password' and v else v) for k, v in (params.items() if isinstance(params, dict) else [])}
        report['error'] = f"Failed to connect to Postgres. Params: {safe_params}"
        report['details'] = str(exc)
        return report

    try:
        cols = get_table_columns(conn, table_name)
        if not cols:
            report['error'] = f"Table '{table_name}' does not exist or has no columns in schema 'public'."
            return report

        # Optionally filter to provided columns
        if columns:
            cols = [c for c in cols if c[0] in set(columns)]

        for column_name, data_type in cols:
            group_entries = find_duplicates_for_column(conn, table_name, column_name, data_type, case_insensitive=case_insensitive)
            if group_entries is None:
                # Column is either PK/UNIQUE or has no duplicates, skip it
                continue

            col_list = []
            for g in group_entries:
                # g is a dict: {'value', 'ids', 'count', 'records'}
                try:
                    v = g.get('value')
                except Exception:
                    v = str(g.get('value'))
                col_list.append({'value': v, 'ids': g.get('ids'), 'count': int(g.get('count')), 'records': g.get('records', [])})

            report['columns'][column_name] = col_list

        # Optionally reduce to only columns that have duplicates
        if only_with_duplicates:
            report['columns'] = {k: v for k, v in report['columns'].items() if v}

        return report
    except Exception as exc:
        report['error'] = 'Error while checking duplicates'
        report['details'] = str(exc)
        return report
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
