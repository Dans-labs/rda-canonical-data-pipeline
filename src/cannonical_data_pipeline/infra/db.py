import os
import sys
import logging
try:
    from src.cannonical_data_pipeline.infra.commons import app_settings, configure_logging
except Exception:
    # best-effort: add repo root and try again
    import os, sys
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from src.cannonical_data_pipeline.infra.commons import app_settings, configure_logging
    except Exception:
        app_settings = None
        configure_logging = None

if configure_logging:
    try:
        configure_logging(app_settings)
    except Exception:
        pass


def get_conn_params():
    """Read Postgres connection parameters using app_settings from infra.commons.

    This prefers values from environment variables first and falls back to app_settings (Dynaconf) and
    finally to defaults. It returns a dict suitable for psycopg.connect() and prints a small debug map to stderr
    (password masked).
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
        """Resolve a setting value giving precedence to environment variables, then app_settings, then default.

        Returns (value, source) where source is one of 'env', 'dynaconf', or 'default'.
        """
        # 1) check environment
        if env_name:
            val = os.environ.get(env_name)
            if val is not None:
                return val, 'env'
        # 2) dynaconf
        if app_settings is not None:
            try:
                val = app_settings.get(name)
            except Exception:
                val = None
            if val is not None:
                return val, 'dynaconf'
        # 3) default
        return default, 'default'

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
        if app_settings:
            try:
                src_map = src_map  # existing code context
            except Exception:
                src_map = None
        masked = masked if 'masked' in locals() else None
        logging.debug("[debug] config sources: %s (secrets_path=%s)", src_map, secrets_path)
        logging.debug("[debug] conn params: %s", masked)
    except Exception:
        pass

    return {
        'host': host,
        'port': port,
        'dbname': dbname,
        'user': user,
        'password': password,
    }
