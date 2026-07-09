#!/usr/bin/env python3
"""Run the deduplication pipeline steps in order.

Steps executed (in order):
  1. insert_mapping.py
  2. apply_deduplication.py
  3. add_columns.py
  4. update_uuids.py

The runner captures stdout/stderr, attempts to parse JSON output from each step,
stops on error by default, and returns a combined report.

Usage:
  python3 scripts/run_pipeline.py [--noop] [--continue-on-error]

Options:
  --noop              Don't actually run the scripts; just print what would run.
  --continue-on-error Continue running subsequent steps even if a step fails.
"""
import json
import subprocess
import sys
from pathlib import Path
import logging
import os

# Try to import app_settings.configure_logging so we use the same logging config as main
try:
    from src.cannonical_data_pipeline.infra.commons import app_settings, configure_logging
except Exception:
    import os
    # best-effort: add repo root and try again
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from src.cannonical_data_pipeline.infra.commons import app_settings, configure_logging
    except Exception:
        app_settings = None
        configure_logging = None

# configure logging consistently via commons.configure_logging
if configure_logging:
    try:
        configure_logging(app_settings)
    except Exception:
        # fallback to a basic config
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s : %(message)s')
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s : %(message)s')

logger = logging.getLogger('run_pipeline')

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / 'src' / 'cannonical_data_pipeline' / 'deduplication'
SCRIPTS = [
    ('insert_mapping', SCRIPT_DIR / 'insert_mapping.py', 'src.cannonical_data_pipeline.deduplication.insert_mapping'),
    ('apply_deduplication', SCRIPT_DIR / 'apply_deduplication.py', 'src.cannonical_data_pipeline.deduplication.apply_deduplication'),
    ('add_columns', SCRIPT_DIR / 'add_columns.py', 'src.cannonical_data_pipeline.deduplication.add_columns'),
    ('update_uuids', SCRIPT_DIR / 'update_uuids.py', 'src.cannonical_data_pipeline.deduplication.update_uuids'),
]


def run_script(path: Path, module: str, noop: bool) -> dict:
    """Run one script and return a result dict.

    Result keys:
      - name: script name
      - path: script path
      - returncode: int (None in noop)
      - stdout: str
      - stderr: str
      - json: parsed JSON from stdout if parseable else None
      - error: error message if returncode != 0 or parse flagged error
    """
    logging.info("Running script: %s", path)
    res = {
        'name': path.stem,
        'path': str(path),
        'returncode': None,
        'stdout': None,
        'stderr': None,
        'json': None,
        'error': None,
    }

    if not path.exists():
        res['error'] = f"script not found: {path}"
        return res

    if noop:
        res['stdout'] = ''
        res['stderr'] = ''
        res['returncode'] = None
        return res

    cmd = [sys.executable, '-m', module]
    env = os.environ.copy()
    repo_root_str = str(REPO_ROOT)
    env['PYTHONPATH'] = (
        repo_root_str if not env.get('PYTHONPATH')
        else f"{repo_root_str}{os.pathsep}{env['PYTHONPATH']}"
    )
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=repo_root_str,
            env=env,
        )
        res['returncode'] = completed.returncode
        res['stdout'] = completed.stdout
        res['stderr'] = completed.stderr
        # Try to parse JSON output from stdout
        try:
            res['json'] = json.loads(completed.stdout) if completed.stdout and completed.stdout.strip() else None
        except Exception:
            res['json'] = None
        if completed.returncode != 0:
            # Prefer structured error if present
            if res['json'] and isinstance(res['json'], dict) and res['json'].get('error'):
                res['error'] = res['json'].get('error')
            else:
                res['error'] = (completed.stderr.strip() or f"script exited with code {completed.returncode}")
    except subprocess.TimeoutExpired as e:
        res['returncode'] = -1
        res['stderr'] = 'timeout'
        res['error'] = 'timeout'
    except Exception as e:
        res['returncode'] = -1
        res['stderr'] = str(e)
        res['error'] = str(e)

    return res


def main():
    overall = {'steps': [], 'success': True}

    # Run all scripts sequentially (always continue to next step)
    for name, path, module in SCRIPTS:
        logger.info(f"\n--- Running step: {name} ({path}) ---")
        result = run_script(path, module=module, noop=False)
        overall['steps'].append(result)

        # Print outputs for visibility
        if result['stdout']:
            logger.info("[stdout]\n%s", result['stdout'])
        if result['stderr']:
            logger.error("[stderr]\n%s", result['stderr'])

        if result.get('error'):
            logger.error("[error] Step %s failed: %s", name, result['error'])
            overall['success'] = False
        else:
            logger.info("[ok] Step %s completed successfully", name)

    # Summarize and exit with non-zero on failure
    logger.info('\n=== Pipeline summary ===')
    logger.info(json.dumps(overall, indent=2, ensure_ascii=False))
    if not overall['success']:
        sys.exit(2)


if __name__ == '__main__':
    main()
