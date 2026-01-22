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

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'src' / 'cannonical_data_pipeline' / 'deduplication'
SCRIPTS = [
    ('insert_mapping', SCRIPT_DIR / 'insert_mapping.py'),
    ('apply_deduplication', SCRIPT_DIR / 'apply_deduplication.py'),
    ('add_columns', SCRIPT_DIR / 'add_columns.py'),
    ('update_uuids', SCRIPT_DIR / 'update_uuids.py'),
]


def run_script(path: Path, noop: bool) -> dict:
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

    cmd = [sys.executable, str(path)]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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
    for name, path in SCRIPTS:
        print(f"\n--- Running step: {name} ({path}) ---")
        result = run_script(path, noop=False)
        overall['steps'].append(result)

        # Print outputs for visibility
        if result['stdout']:
            print(f"[stdout]\n{result['stdout']}")
        if result['stderr']:
            print(f"[stderr]\n{result['stderr']}", file=sys.stderr)

        if result.get('error'):
            print(f"[error] Step {name} failed: {result['error']}", file=sys.stderr)
            overall['success'] = False
        else:
            print(f"[ok] Step {name} completed successfully")

    # Summarize and exit with non-zero on failure
    print('\n=== Pipeline summary ===')
    print(json.dumps(overall, indent=2, ensure_ascii=False))
    if not overall['success']:
        sys.exit(2)


if __name__ == '__main__':
    main()
