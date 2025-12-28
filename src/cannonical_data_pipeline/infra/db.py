import os
import sys
import tomli


def _read_secrets_toml(path):
    try:
        with open(path, 'rb') as f:
            return tomli.load(f)
    except Exception:
        return {}


def get_conn_params():
    """Read Postgres connection parameters using app_settings, env or conf/.secrets.toml as fallback.

    Returns a dict for psycopg2.connect(). Also prints a debug line to stderr indicating per-key sources (password masked).
    """
    # locate repo and secrets path
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    repo_root = os.path.abspath(os.path.join(repo_root, '..'))
    secrets_path = os.path.join(repo_root, 'conf', '.secrets.toml')

    # Try import app_settings
    try:
        from src.cannonical_data_pipeline.infra.commons import app_settings
    except Exception:
        app_settings = None

    # load toml file as fallback
    toml_data = _read_secrets_toml(secrets_path)

    def _get_setting(name, env_name=None, default=None):
        # name: key in dynaconf/app_settings (lowercase)
        # env_name: env var name to check
        val = None
        src = None
        if app_settings is not None:
            try:
                val = app_settings.get(name)
            except Exception:
                val = None
            if val is not None:
                src = 'dynaconf'
        if val is None and env_name:
            val = os.environ.get(env_name)
            if val is not None:
                src = 'env'
        if val is None:
            # try toml
            if toml_data:
                # toml file may have sections (e.g., [default]) — check both top-level and default section
                val = toml_data.get(name)
                if val is None:
                    try:
                        val = toml_data.get('default', {}).get(name)
                    except Exception:
                        val = None
                if val is not None:
                    src = 'secrets_toml'
        if val is None:
            val = default
            src = src or 'default'
        return val, src

    host, s1 = _get_setting('db_host', 'DB_HOST', 'localhost')
    port, s2 = _get_setting('db_port', 'DB_PORT', 5433)
    dbname, s3 = _get_setting('db_name', 'DB_NAME', 'rda')
    user, s4 = _get_setting('db_user', 'DB_USER', None)
    password, s5 = _get_setting('db_password', 'DB_PASSWORD', None)

    # normalize
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
        src_map = { 'host': s1, 'port': s2, 'dbname': s3, 'user': s4, 'password': s5 }
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
