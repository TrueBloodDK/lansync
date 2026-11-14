"""Linux process-crash checks in isolated projects; not a power-loss simulator."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from lansync.config import load_project
from lansync.credentials import create_credentials, export_client_credentials
from lansync.manifest import export_manifest
from lansync.network import make_client, read_token
from lansync.project_init import initialize
from lansync.recovery import restore_copy
from lansync import sync

from acceptance import check, stop_process, wait_until


def child(config, point, marker):
    values = load_project(config).values
    def pause():
        marker.write_text(point)
        os.kill(os.getpid(), signal.SIGSTOP)
        # A signal sent from a worker thread can stop another thread first.
        # Never advance the commit even if this thread briefly keeps running.
        while True:
            signal.pause()
    if point in {'publish', 'server-publish'}:
        original = sync.publish_new
        def publish(source, target):
            if target.name == 'data.bin':
                pause()
            return original(source, target)
        sync.publish_new = publish
    elif point == 'baseline':
        original_export = sync.export_manifest
        def export(value, destination):
            if destination.name == 'baseline.json':
                pause()
            return original_export(value, destination)
        sync.export_manifest = export
    if point == 'download':
        import httpx
        original_chunks = httpx.Response.iter_bytes
        def chunks(response, *args, **kwargs):
            for chunk in original_chunks(response, *args, **kwargs):
                yield chunk
                if response.request.url.path == '/v1/file':
                    pause()
        httpx.Response.iter_bytes = chunks
    if point == 'server-publish':
        import uvicorn
        from lansync.network import create_app
        app = create_app(values['root'], values['state_dir'] / 'index.sqlite3',
                         read_token(values['token_file']), values['exclude'], allow_write=True)
        uvicorn.run(app, host=values['host'], port=values['port'],
                    ssl_certfile=str(values['cert_file']), ssl_keyfile=str(values['key_file']))
        return
    with make_client(values['url'], read_token(values['token_file']), values['ca_file']) as connection:
        sync.synchronize(connection, values['root'], values['state_dir'])


def run(base, report):
    server, client = base / 'server', base / 'client'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = initialize(server, 'server')
    config.write_text(config.read_text().replace('host = "0.0.0.0"', 'host = "127.0.0.1"')
                      .replace('port = 8765', f'port = {port}'))
    project = initialize(client, 'client', f'https://127.0.0.1:{port}')
    create_credentials(server / 'credentials', ['127.0.0.1'])
    export_client_credentials(server / 'credentials', client / 'credentials')
    a, b, state = server / 'shared', client / 'shared', client / 'state'
    process = victim = requester = None
    report['scenarios'] = []
    with (base / 'server.log').open('wb') as log, (base / 'victim.log').open('wb') as victim_log:
        try:
            process = subprocess.Popen([sys.executable, '-m', 'lansync', 'run', str(config)], stdout=log, stderr=log)
            with ExitStack() as connections:
                def connect():
                    return connections.enter_context(make_client(f'https://127.0.0.1:{port}',
                        read_token(client / 'credentials/token.txt'), client / 'credentials/ca.pem'))
                connection = connect()
                def ready():
                    try:
                        return connection.get('/v1/manifest').status_code == 200
                    except Exception:
                        return False
                wait_until(ready, [process], 'server ready')
                for point in ('download', 'publish', 'baseline'):
                    old = f'old {point}'.encode() * 100
                    new = f'new {point}'.encode() * (1024 * 1024)
                    (a / 'data.bin').write_bytes(old)
                    result = sync.synchronize(connection, b, state)
                    check(not result['conflicts'], 'Setup conflict')
                    baseline_before = (state / 'baseline.json').read_bytes()
                    (a / 'data.bin').write_bytes(new)
                    marker = base / f'{point}.ready'
                    victim = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'child',
                                               '--config', str(project), '--point', point, '--marker', str(marker)],
                                              stdout=victim_log, stderr=victim_log)
                    wait_until(marker.exists, [process, victim], f'pause at {point}')
                    victim.kill()
                    check(victim.wait(timeout=10) == -signal.SIGKILL, 'Victim was not killed by SIGKILL')
                    check((state / 'sync.lock').exists(), 'Expected stale lock is absent')
                    check((state / 'baseline.json').read_bytes() == baseline_before, 'Baseline advanced before acknowledgment')
                    try:
                        sync.synchronize(connection, b, state)
                    except FileExistsError:
                        pass
                    else:
                        raise RuntimeError('Stale lock was silently bypassed')
                    # Only this harness-owned process is dead, confirmed by wait().
                    (state / 'sync.lock').unlink()
                    pending = list((state / 'recovery').glob('*.pending.json'))
                    if point == 'publish':
                        check(len(pending) == 1 and not (b / 'data.bin').exists(), 'Unexpected pending replacement state')
                        try:
                            sync.synchronize(connection, b, state)
                        except ValueError as exc:
                            check('Unfinished' in str(exc), 'Wrong pending error')
                        else:
                            raise RuntimeError('Pending replacement was not blocked')
                        data = json.loads(pending[0].read_text())
                        backup = state / 'recovery' / data['backup']
                        check(backup.read_bytes() == old, 'Old version missing from recovery')
                        restore_copy(state, backup.stem, b / 'data.bin')
                        check((b / 'data.bin').read_bytes() == old, 'Recovery restore failed')
                        pending[0].unlink()
                    elif point == 'download':
                        check((b / 'data.bin').read_bytes() == old and not pending, 'Interrupted download damaged old file')
                    else:
                        check((b / 'data.bin').read_bytes() == new and not pending, 'Published version missing after baseline interruption')
                    result = sync.synchronize(connection, b, state)
                    check(not result['conflicts'] and (b / 'data.bin').read_bytes() == new, 'Recovery did not converge')
                    check((a / 'data.bin').read_bytes() == new, 'Client propagated accidental deletion')
                    # Interrupted downloads leave ignored temporary files; keep evidence.
                    report['scenarios'].append({'point': point, 'result': 'passed',
                                                'ignored_temporary_files': len(list(b.glob('.lansync-*.tmp'))),
                                                'sha256': hashlib.sha256(new).hexdigest()})
                    print(f'{point}: passed', flush=True)
                # Kill a real server during an upload commit, before its response.
                connection.close()
                stop_process(process)
                old_server = (a / 'data.bin').read_bytes()
                proposal = b'client proposal after server crash' * 65536
                (b / 'data.bin').write_bytes(proposal)
                baseline_before = (state / 'baseline.json').read_bytes()
                marker = base / 'server-publish.ready'
                victim = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'child',
                                           '--config', str(config), '--point', 'server-publish', '--marker', str(marker)],
                                          stdout=victim_log, stderr=victim_log)
                connection = connect()
                wait_until(ready, [victim], 'instrumented server ready')
                requester = subprocess.Popen([sys.executable, '-m', 'lansync', 'run', str(project), '--action', 'once'],
                                             stdout=victim_log, stderr=victim_log)
                wait_until(marker.exists, [victim, requester], 'server replacement pause')
                victim.kill()
                check(victim.wait(timeout=10) == -signal.SIGKILL, 'Server was not killed')
                check(requester.wait(timeout=15) == 1, 'Client did not report the interrupted upload')
                check((state / 'baseline.json').read_bytes() == baseline_before, 'Unacknowledged upload advanced baseline')
                check((b / 'data.bin').read_bytes() == proposal, 'Client proposal was lost')
                process = subprocess.Popen([sys.executable, '-m', 'lansync', 'run', str(config)], stdout=log, stderr=log)
                def pending_ready():
                    try:
                        return connection.get('/v1/manifest').status_code == 409
                    except Exception:
                        return False
                wait_until(pending_ready, [process], 'server refuses pending replacement')
                server_state = server / 'state'
                pending = list((server_state / 'recovery').glob('*.pending.json'))
                check(len(pending) == 1 and not (a / 'data.bin').exists(), 'Server pending marker missing')
                data = json.loads(pending[0].read_text())
                backup = server_state / 'recovery' / data['backup']
                check(backup.read_bytes() == old_server, 'Server old version missing')
                connection.close()
                stop_process(process)
                restore_copy(server_state, backup.stem, a / 'data.bin')
                pending[0].unlink()
                process = subprocess.Popen([sys.executable, '-m', 'lansync', 'run', str(config)], stdout=log, stderr=log)
                connection = connect()
                wait_until(ready, [process], 'recovered server ready')
                result = sync.synchronize(connection, b, state)
                check(not result['conflicts'] and (a / 'data.bin').read_bytes() == proposal, 'Upload retry did not converge')
                report['scenarios'].append({'point': 'server-publish', 'result': 'passed',
                                            'sha256': hashlib.sha256(proposal).hexdigest()})
                print('server-publish: passed', flush=True)
        finally:
            if requester is not None and requester.poll() is None:
                requester.kill()
                requester.wait(timeout=10)
            if victim is not None and victim.poll() is None:
                victim.kill()
                victim.wait(timeout=10)
            stop_process(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    public = modes.add_parser('run')
    public.add_argument('--output', type=Path, required=True)
    worker = modes.add_parser('child')
    worker.add_argument('--config', type=Path, required=True)
    worker.add_argument('--marker', type=Path, required=True)
    worker.add_argument('--point', choices=['download', 'publish', 'baseline', 'server-publish'], required=True)
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Crash harness requires Linux')
    if args.mode == 'child':
        child(args.config, args.point, args.marker)
        return
    base = args.output.expanduser().absolute()
    base.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'result': 'failed', 'method': 'Real SIGKILL at deterministic pause points; manual verified recovery; no power-loss claim'}
    try:
        run(base, report)
        report['result'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        export_manifest(report, base / 'verification.json')


if __name__ == '__main__':
    main()
