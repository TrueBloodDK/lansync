"""Check ENOSPC recovery using an explicitly mounted, empty, small Linux tmpfs."""
import argparse
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import httpx
from lansync.credentials import create_credentials, export_client_credentials
from lansync.manifest import export_manifest
from lansync.network import make_client, read_token
from lansync.project_init import initialize
from lansync.sync import synchronize
from acceptance import check, stop_process, wait_until


def run(volume, output, report, side='server'):
    check(volume.is_dir() and os.path.ismount(volume), 'Volume must be a dedicated mount point')
    kind = subprocess.check_output(['stat', '-f', '-c', '%T', str(volume)], text=True).strip()
    check(kind == 'tmpfs', 'Only tmpfs is permitted; ordinary disks are never filled')
    info = os.statvfs(volume)
    check(8 * 1024 * 1024 <= info.f_blocks * info.f_frsize <= 256 * 1024 * 1024,
          'Use a tmpfs between 8 and 256 MiB')
    check(not any(volume.iterdir()), 'Test volume must be empty')
    server = (volume if side == 'server' else output) / 'server'
    client = (output if side == 'server' else volume) / 'client'
    config = initialize(server, 'server')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config.write_text(config.read_text().replace('host = "0.0.0.0"', 'host = "127.0.0.1"')
                      .replace('port = 8765', f'port = {port}'))
    initialize(client, 'client', f'https://127.0.0.1:{port}')
    create_credentials(server / 'credentials', ['127.0.0.1'])
    export_client_credentials(server / 'credentials', client / 'credentials')
    original = b'old version before full disk' * 1024
    target = server / 'shared/data.bin'
    target.write_bytes(original)
    filler = volume / 'filler.bin'
    process = None
    with (output / 'server.log').open('wb') as log:
        try:
            process = subprocess.Popen([sys.executable, '-m', 'lansync', 'run', str(config)], stdout=log, stderr=log)
            with make_client(f'https://127.0.0.1:{port}', read_token(client / 'credentials/token.txt'),
                             client / 'credentials/ca.pem', request_timeout=15) as connection:
                def ready():
                    try:
                        return connection.get('/v1/manifest').status_code == 200
                    except httpx.HTTPError:
                        return False
                wait_until(ready, [process], 'tmpfs server ready')
                root, state = client / 'shared', client / 'state'
                check(not synchronize(connection, root, state)['conflicts'], 'Initial conflict')
                baseline = (state / 'baseline.json').read_bytes()
                proposal = b'x' * (4 * 1024 * 1024)
                source = root / 'data.bin' if side == 'server' else target
                affected = target if side == 'server' else root / 'data.bin'
                source.write_bytes(proposal)
                available = os.statvfs(volume).f_bavail * info.f_frsize
                remaining = available - 1024 * 1024
                check(remaining > 0, 'No room to prepare bounded filler')
                with filler.open('xb') as stream:
                    while remaining:
                        count = min(remaining, 1024 * 1024)
                        stream.write(b'f' * count)
                        remaining -= count
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    synchronize(connection, root, state)
                except (httpx.HTTPError, OSError) as exc:
                    report['interrupted_transfer_error'] = type(exc).__name__
                    if side == 'client':
                        check(isinstance(exc, OSError) and exc.errno == errno.ENOSPC, 'Expected local ENOSPC')
                    else:
                        wait_until(lambda: '[Errno 28]' in (output / 'server.log').read_text(),
                                   [process], 'server ENOSPC evidence', timeout=5)
                else:
                    raise RuntimeError('Transfer unexpectedly succeeded on a full volume')
                check(affected.read_bytes() == original, 'Destination original changed after ENOSPC')
                check(source.read_bytes() == proposal, 'Source proposal changed after ENOSPC')
                check((state / 'baseline.json').read_bytes() == baseline, 'Failed transfer advanced baseline')
                check(not (state / 'sync.lock').exists(), 'Failed transfer left a lock')
                check(not list((server / 'state').glob('upload-*')), 'Failed upload temporary file remains')
                check(not list(root.glob('.lansync-*.tmp')), 'Failed download temporary file remains')
                check(not list((server / 'state/recovery').glob('*.pending.json')), 'Unexpected pending replacement')
                report.update(original_preserved=True, baseline_preserved=True)
                filler.unlink()
                # An HTTP 500 during a streamed body may invalidate keep-alive.
                # A new one-shot CLI invocation likewise creates a new client.
                connection.close()
                with make_client(f'https://127.0.0.1:{port}', read_token(client / 'credentials/token.txt'),
                                 client / 'credentials/ca.pem', request_timeout=15) as retry_connection:
                    result = synchronize(retry_connection, root, state)
                counter = 'uploaded' if side == 'server' else 'downloaded'
                check(not result['conflicts'] and result[counter] == 1, 'Retry did not transfer the file')
                check(affected.read_bytes() == proposal, 'Retry hash/content mismatch')
                report.update(original_preserved=True, baseline_preserved=True, retry_passed=True,
                              tmpfs_bytes=info.f_blocks * info.f_frsize, transfer_bytes=len(proposal))
        finally:
            stop_process(process)
            filler.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--volume', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--side', choices=['server', 'client'], default='server')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Linux tmpfs is required')
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'result': 'failed', 'full_side': args.side,
              'method': 'Actual ENOSPC on a dedicated bounded tmpfs; no physical disk filling'}
    try:
        run(args.volume.expanduser().resolve(strict=True), output, report, args.side)
        report['result'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        export_manifest(report, output / 'verification.json')


if __name__ == '__main__':
    main()
