"""Project-specific supervisor using the UniqueOS bounded shift workflow."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup-only', action='store_true')
    parser.add_argument('--batches', type=int, default=3)
    parser.add_argument('--minutes', type=int, default=60)
    args = parser.parse_args()
    if args.batches < 1 or args.minutes < 1:
        parser.error('Batches and minutes must be positive')
    for command in ('git', 'codex'):
        if not shutil.which(command):
            raise SystemExit(f'Missing executable: {command}')
    subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'], check=True)
    for name in ('AGENTS.md', 'docs/development/ROADMAP.md', 'docs/ai/HANDOFF.md'):
        if not (ROOT / name).is_file():
            raise SystemExit(f'Missing project instructions: {name}')
    if args.setup_only:
        print('Shift prerequisites passed. Model execution starts with an explicit run.')
        return
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain']).strip():
        raise SystemExit('Commit or stash checkout changes before creating a shift worktree.')
    state = ROOT / '.state' / 'shifts'
    state.mkdir(parents=True, exist_ok=True)
    lock = state / 'supervisor.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SystemExit('A supervisor lock already exists. Inspect the active shift first.')
    with os.fdopen(fd, 'w') as handle:
        handle.write(str(os.getpid()))
    child = None
    try:
        if (state / 'STOP').exists():
            raise SystemExit('STOP marker exists. Remove it to start a new shift.')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        directory = state / stamp
        directory.mkdir()
        worktree = ROOT.parent / (ROOT.name + '-shift-' + stamp)
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '-b', 'shift/' + stamp, str(worktree)], check=True)
        (worktree / '.state').mkdir(exist_ok=True)
        print(f'Worktree {worktree}\nLogs {directory}', flush=True)
        deadline = time.monotonic() + args.minutes * 60
        prompt = ('Read AGENTS.md and the development roadmap and handoff. Complete one eligible task, '
                  'run its checks, update the roadmap and handoff with evidence, and commit the task branch. '
                  'Keep external publication and deployment outside this local development shift. '
                  'If blocked, document the blocker and finish. If no tasks remain, report completion.')
        for batch in range(1, args.batches + 1):
            if time.monotonic() >= deadline or (state / 'STOP').exists():
                break
            with (directory / 'timeline.jsonl').open('a') as timeline:
                timeline.write(json.dumps({'batch': batch, 'started': time.time()}) + '\n')
            with (directory / f'{batch}.jsonl').open('wb') as log:
                child = subprocess.Popen([shutil.which('codex'), 'exec', '-C', str(worktree),
                    '-s', 'workspace-write', '--json', '-o', str(worktree / '.state' / 'shift-last.md'), prompt],
                    stdout=log, stderr=subprocess.STDOUT)
                while child.poll() is None:
                    if time.monotonic() >= deadline or (state / 'STOP').exists():
                        stop(child)
                        return
                    time.sleep(1)
                if child.returncode:
                    raise SystemExit(f'Batch failed with exit {child.returncode}. Inspect {directory}.')
                if subprocess.check_output(['git', '-C', str(worktree), 'status', '--porcelain']).strip():
                    raise SystemExit('Batch left uncommitted work. Inspect the handoff before restarting.')
    finally:
        if child is not None and child.poll() is None:
            stop(child)
        lock.unlink(missing_ok=True)


def stop(child):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
    else:
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


if __name__ == '__main__':
    run()
