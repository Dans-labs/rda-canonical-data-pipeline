import os
import sys


def get_conn_params():
    """Read Postgres connection parameters using app_settings from infra.commons.

    This prefers values from `app_settings` (Dynaconf wrapper) and falls back to
    environment variables. It returns a dict suitable for psycopg2.connect()
    and prints a small debug map to stderr (password masked).
    """
    # locate repo and secrets path (for debug only)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    repo_root = os.path.abspath(os.path.join(repo_root, '..'))
    secrets_path = os.path.join(repo_root, 'conf', '.secrets.toml')

    # Import app_settings (SettingsWrapper) from infra.commons; if not available, app_settings=None
    try:
        from src.cannonical_data_pipeline.infra.commons import app_settings
    except Exception:
        app_settings = None

    def _get_setting(name, env_name=None, default=None):
        val = None
        src = None
        # prefer app_settings
        if app_settings is not None:
            try:
                val = app_settings.get(name)
            except Exception:
                val = None
            if val is not None:
                src = 'dynaconf'
        # fallback to env
        if val is None and env_name:
            val = os.environ.get(env_name)
            if val is not None:
                src = 'env'
        # final fallback to provided default
        if val is None:
            val = default
            src = src or 'default'
        return val, src

    host, s1 = _get_setting('db_host', 'DB_HOST', 'localhost')
    port, s2 = _get_setting('db_port', 'DB_PORT', 5432)
    dbname, s3 = _get_setting('db_name', 'DB_NAME', 'rda')
    user, s4 = _get_setting('db_user', 'DB_USER', None)
    password, s5 = _get_setting('db_password', 'DB_PASSWORD', None)

    # normalize port
    try:
        port = int(port)
    except Exception:
        port = 5432

    # mask for debug
    masked = {
        'host': host,
        'port': port,
        'dbname': dbname,
        'user': user,
        'password': '***' if password else None,
    }

    # print per-key source debug
    try:
        src_map = {'host': s1, 'port': s2, 'dbname': s3, 'user': s4, 'password': s5}
        print(f"[debug] config sources: {src_map} (secrets_path={secrets_path})", file=sys.stderr)
        print(f"[debug] conn params: {masked}", file=sys.stderr)
    except Exception:
        pass

    return {
        'host': host,
        'port': port,
        'dbname': dbname,
        'user': user,
        'password': password,
    }
