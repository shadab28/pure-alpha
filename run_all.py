#!/usr/bin/env python3
"""Run multiple project scripts in parallel as separate processes.

Usage:
  python run_all.py         # runs default set
  python run_all.py --scripts fetch,fetch_upsert
  python run_all.py --dry-run

The script spawns each target using the current Python interpreter with
PYTHONPATH set to the repo root so local imports work. Stdout/stderr for
each process is written to `logs/{name}.log` and basic status is printed
to the console. Ctrl-C will terminate all children.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from typing import List, Dict


REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
LOG_DIR = os.path.join(REPO_ROOT, 'logs')


# Define candidate processes. Keys:
# - name: short identifier
# - path: relative path to script from repo root
# - args: additional argv list
DEFAULT_PROCESSES: List[Dict] = [
    {
        'name': 'fetch_upsert',
        'path': os.path.join('Core_files', 'fetch_and_upsert_ohlcv.py'),
        'args': ['--days', '200'],
    },
    {
        'name': 'fetch_csv',
        'path': os.path.join('Core_files', 'fetch_ohlcv.py'),
        'args': ['--days', '20'],
    },
    {
        'name': 'auth',
        'path': os.path.join('Core_files', 'auth.py'),
        'args': [],
    },
    {
        'name': 'db_test',
        'path': os.path.join('pgAdmin_database', 'db_connection.py'),
        'args': [],
    },
    {
        'name': 'sample_insert',
        'path': os.path.join('pgAdmin_database', 'sample_insert_ohlcv.py'),
        'args': [],
    },
]


def ensure_log_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def build_command(p: Dict, python_exe: str) -> List[str]:
    script = os.path.join(REPO_ROOT, p['path'])
    return [python_exe, script] + list(p.get('args', []))


def spawn_process(cmd: List[str], env: Dict[str, str], log_path: str) -> subprocess.Popen:
    # Open log file in append mode so multiple runs don't clobber previous logs
    lf = open(log_path, 'a', encoding='utf-8')
    # Use text mode for stdout/stderr redirection
    proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env)
    return proc


def sigterm_handler(procs: Dict[str, subprocess.Popen]):
    def _handle(signum, frame):
        print('\nReceived signal', signum, '- terminating child processes...')
        for name, p in procs.items():
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        # give them a moment, then kill if still alive
        for name, p in procs.items():
            try:
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        sys.exit(0)

    return _handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scripts', type=str, default=None,
                        help='Comma-separated list of process names to run (e.g. fetch,auth).')
    parser.add_argument('--list', action='store_true', help='List available process names')
    parser.add_argument('--dry-run', action='store_true', help='Only print the commands that would be run')
    parser.add_argument('--log-dir', type=str, default=LOG_DIR, help='Directory to store logs')
    parser.add_argument('--python', type=str, default=sys.executable, help='Python interpreter to use')
    args = parser.parse_args()

    procs_by_name = {p['name']: p for p in DEFAULT_PROCESSES}

    if args.list:
        print('Available processes:')
        for n in procs_by_name:
            print('  ', n)
        return

    if args.scripts:
        wanted = [s.strip() for s in args.scripts.split(',') if s.strip()]
        to_run = [procs_by_name[s] for s in wanted if s in procs_by_name]
        missing = [s for s in wanted if s not in procs_by_name]
        if missing:
            print('Warning: unknown scripts requested:', missing)
    else:
        to_run = DEFAULT_PROCESSES

    ensure_log_dir(args.log_dir)

    env = os.environ.copy()
    # ensure local imports work
    env['PYTHONPATH'] = REPO_ROOT + (os.pathsep + env.get('PYTHONPATH', ''))

    procs: Dict[str, subprocess.Popen] = {}

    # Prepare signal handlers
    handler = sigterm_handler(procs)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    # Launch processes
    for p in to_run:
        name = p['name']
        cmd = build_command(p, args.python)
        log_path = os.path.join(args.log_dir, f'{name}.log')
        if args.dry_run:
            print('DRY RUN:', name, '->', ' '.join(cmd), 'log:', log_path)
            continue
        print('Starting', name, '->', ' '.join(cmd))
        try:
            proc = spawn_process(cmd, env, log_path)
        except FileNotFoundError as e:
            print('Failed to start', name, ':', e)
            continue
        procs[name] = proc

    if args.dry_run:
        return

    # Monitor processes in background threads to report exit statuses
    def monitor(name: str, p: subprocess.Popen):
        rc = p.wait()
        print(f'Process {name} exited with return code {rc}. See logs/{name}.log for output')

    for name, p in list(procs.items()):
        t = threading.Thread(target=monitor, args=(name, p), daemon=True)
        t.start()

    # Wait until all children exit
    try:
        while any(p.poll() is None for p in procs.values()):
            for name, p in list(procs.items()):
                if p.poll() is not None:
                    # already handled by monitor thread
                    pass
            # sleep in small increments
            import time

            time.sleep(0.5)
    except KeyboardInterrupt:
        handler(signal.SIGINT, None)

    print('All child processes completed.')


if __name__ == '__main__':
    main()
