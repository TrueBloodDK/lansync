"""Repeatable Linux loopback HTTPS soak test using disposable projects."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
from threading import Event, Thread
import time

from acceptance import check, stop_process, wait_until


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree(root):
    return {p.relative_to(root).as_posix(): digest(p) if p.is_file() else None
            for p in root.rglob('*')}


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    return None


def run(args, report):
    base, cli = args.output, args.cli
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)

    def command(*values):
        result = subprocess.run([str(cli), *map(str, values)], cwd=base, env=env,
                                capture_output=True, text=True, timeout=120)
        check(result.returncode == 0, f'{values[0]} failed: {result.stdout} {result.stderr}')
        return result.stdout

    report['cli_version'] = command('--version').strip()
    server, client = base / 'server', base / 'client'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command('init', server, '--mode', 'server')
    command('init', client, '--mode', 'client', '--url', f'https://127.0.0.1:{port}')
    server_config, client_config = server / 'project.toml', client / 'project.toml'
    text = server_config.read_text().replace('host = "0.0.0.0"', 'host = "127.0.0.1"').replace('port = 8765', f'port = {port}')
    server_config.write_text(text)
    text = client_config.read_text().replace('interval = 30\n', 'interval = 1\n').replace('retry_max = 300\n', 'retry_max = 2\n')
    client_config.write_text(text)
    command('credentials', 'create', '--host', '127.0.0.1', '--output-dir', server / 'credentials')
    command('credentials', 'export-client', '--source-dir', server / 'credentials', '--output-dir', client / 'credentials')
    a, b = server / 'shared', client / 'shared'
    for number in range(args.files):
        (a / f'seed-{number:06}.bin').write_bytes(number.to_bytes(8, 'big') * 512)

    processes, samples = [], []
    sampler_stop = Event()
    started = time.monotonic()

    def sample():
        while not sampler_stop.is_set():
            samples.append({'elapsed_seconds': time.monotonic() - started,
                            'server_rss_bytes': rss(processes[0].pid),
                            'client_rss_bytes': rss(processes[1].pid)})
            sampler_stop.wait(.5)

    def status():
        path = client / 'state/loop-status.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def completed(previous, matches):
        snapshot = status()
        check(snapshot.get('status') not in {'failed', 'conflicts'}, 'Unexpected failure or conflict')
        return (snapshot.get('status') == 'synced' and
                snapshot.get('last_success_utc') != previous and matches())

    thread = None
    with ExitStack() as stack:
        try:
            for name, config in (('server', server_config), ('client', client_config)):
                log = stack.enter_context((base / f'{name}.log').open('wb'))
                processes.append(subprocess.Popen([str(cli), 'run', str(config)], cwd=base, env=env,
                                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
            thread = Thread(target=sample, daemon=True)
            thread.start()
            wait_until(lambda: completed(None, lambda: len(list(b.iterdir())) == args.files),
                       processes, 'initial tree transfer', args.timeout)
            check(tree(a) == tree(b), 'Initial tree differs')
            report['initial_tree_seconds'] = time.monotonic() - started
            report['large_transfers'] = []
            for direction, source, target, byte in (('download', a, b, b'd'), ('upload', b, a, b'u')):
                previous = status().get('last_success_utc')
                key = f'large-{direction}.bin'
                # Publish a complete file atomically, avoiding intentional mid-write races.
                staging = source / (key + '.tmp')
                with staging.open('wb') as stream:
                    for _ in range(args.large_mib):
                        stream.write(byte * (1024 * 1024))
                expected = digest(staging)
                begin = time.monotonic()
                staging.replace(source / key)
                wait_until(lambda: completed(previous, lambda: (target / key).is_file()),
                           processes, f'large {direction}', args.timeout)
                elapsed = time.monotonic() - begin
                check(digest(target / key) == expected, 'Large file SHA-256 mismatch')
                report['large_transfers'].append({'direction': direction, 'bytes': args.large_mib * 1024 * 1024,
                                                 'convergence_seconds': elapsed,
                                                 'effective_mib_per_second': args.large_mib / elapsed})
            print('Initial tree and large transfers passed; starting repeated changes', flush=True)
            begin = time.monotonic()
            operations = []
            report['operations'] = operations
            while not operations or time.monotonic() - begin < args.duration:
                index = len(operations)
                source, target = (a, b) if index % 2 == 0 else (b, a)
                key = f'cycle-{index % 20:02}.txt'
                previous = status().get('last_success_utc')
                operation_start = time.monotonic()
                deleting = index % 5 == 0 and (source / key).exists()
                if deleting:
                    (source / key).unlink()
                    matches = lambda: not (target / key).exists()
                else:
                    payload = f'iteration {index}\n'.encode() * 100
                    staging = source / (key + '.tmp')
                    staging.write_bytes(payload)
                    staging.replace(source / key)
                    matches = lambda: (target / key).is_file() and (target / key).read_bytes() == payload
                wait_until(lambda: completed(previous, matches), processes, f'iteration {index}', args.timeout)
                operations.append({'index': index, 'direction': 'download' if index % 2 == 0 else 'upload',
                                   'operation': 'delete' if deleting else 'write',
                                   'seconds': time.monotonic() - operation_start})
                save(base / 'progress.json', {'completed_operations': len(operations),
                                             'elapsed_seconds': time.monotonic() - begin, 'last_sample': samples[-1]})
                if len(operations) % 10 == 0:
                    print(f'{len(operations)} operations, {time.monotonic() - begin:.1f} seconds', flush=True)
            report['change_phase_seconds'] = time.monotonic() - begin
            # Stop the writer before the independent final tree comparison.
            stop_process(processes[1])
            check(processes[1].returncode == 0, 'Client did not exit cleanly')
            check(not (client / 'state/sync.lock').exists(), 'Client lock remains')
            check(status().get('status') == 'stopped', 'Stopped status missing')
            check(tree(a) == tree(b), 'Final hashes or directories differ')
            report['recovery'] = {}
            for name, project in (('server', server), ('client', client)):
                result = json.loads(command('recovery', 'verify', '--state-dir', project / 'state', '--json'))
                check(result['problems'] == 0, 'Recovery integrity failure')
                report['recovery'][name] = result
            report['final_trees_match'] = True
            durations = [entry['seconds'] for entry in operations]
            report['operation_summary'] = {'count': len(operations), 'median_seconds': statistics.median(durations),
                                           'max_seconds': max(durations),
                                           'deletions': sum(entry['operation'] == 'delete' for entry in operations)}
        finally:
            sampler_stop.set()
            if thread is not None:
                thread.join(5)
            report['memory_samples'] = samples
            report['sampled_peak_rss_bytes'] = {
                role: max((s[f'{role}_rss_bytes'] or 0 for s in samples), default=0)
                for role in ('server', 'client')}
            try:
                if len(processes) > 1:
                    stop_process(processes[1])
            finally:
                if processes:
                    stop_process(processes[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; test data and credentials remain here')
    parser.add_argument('--duration', type=float, default=600, help='Seconds of repeated changes, excluding setup')
    parser.add_argument('--files', type=int, default=500)
    parser.add_argument('--large-mib', type=int, default=64)
    parser.add_argument('--timeout', type=float, default=180, help='Maximum seconds for each convergence')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Requires Linux with /proc')
    if not all(math.isfinite(v) and v > 0 for v in (args.duration, args.files, args.large_mib, args.timeout)):
        parser.error('Counts, sizes and durations must be positive finite values')
    args.cli = args.cli.expanduser().resolve(strict=True)
    args.output = args.output.expanduser().absolute()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'python': platform.python_version(), 'result': 'failed',
              'parameters': {'duration': args.duration, 'files': args.files, 'large_mib': args.large_mib},
              'method': 'Linux loopback HTTPS; sequential edits; full scans, polling and hashing included in latency. RSS sampled every 0.5s, not exact peak. No network speed or leak guarantee.'}
    started = time.monotonic()
    try:
        run(args, report)
        report['result'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['total_seconds'] = time.monotonic() - started
        save(args.output / 'verification.json', report)
    print(f'Passed; report: {args.output / "verification.json"}', flush=True)


if __name__ == '__main__':
    main()
