"""Measure real LANSync transfers between a dedicated Linux server and a client."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
import uuid

from lansync import __version__
from lansync.credentials import create_credentials, export_client_credentials
from lansync.manifest import export_manifest
from lansync.network import make_client, read_token
from lansync.project_init import initialize
from lansync.sync import fetch_manifest, synchronize


def check(condition, message):
    if not condition:
        raise ValueError(message)


def write_payload(path, size, seed):
    block = hashlib.sha256(seed.encode()).digest() * 32768
    digest = hashlib.sha256()
    with path.open('xb') as stream:
        remaining = size
        while remaining:
            chunk = block[:min(len(block), remaining)]
            stream.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def prepare(args):
    base = args.output
    project = base / 'server'
    config = initialize(project, 'server')
    text = config.read_text().replace('host = "0.0.0.0"', 'host = ' + json.dumps(args.host))
    config.write_text(text.replace('port = 8765', f'port = {args.port}'))
    create_credentials(project / 'credentials', [args.host])
    export_client_credentials(project / 'credentials', base / 'client-bundle')
    fixture = {'schema': 1, 'id': uuid.uuid4().hex, 'files': []}
    for index, mib in enumerate(args.sizes_mib):
        name = f'download-{index:03}.bin'
        size = mib * 1024 * 1024
        sha = write_payload(project / 'shared' / name, size, f"{fixture['id']}/{name}")
        fixture['files'].append({'path': name, 'size': size, 'sha256': sha})
    export_manifest(fixture, base / 'fixture.json')
    print(f'Server ready to start: lansync run "{config}"')
    print('Transfer client-bundle and fixture.json to the client; keep server keys here.')


def validate_fixture(fixture):
    check(isinstance(fixture, dict) and fixture.get('schema') == 1, 'Unsupported fixture')
    check(isinstance(fixture.get('id'), str) and bool(fixture['id']), 'Missing fixture ID')
    files = fixture.get('files')
    check(isinstance(files, list) and bool(files), 'Empty fixture')
    for index, entry in enumerate(files):
        check(isinstance(entry, dict) and entry.get('path') == f'download-{index:03}.bin', 'Invalid fixture path')
        check(type(entry.get('size')) is int and entry['size'] > 0, 'Invalid fixture size')
        sha = entry.get('sha256')
        check(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha), 'Invalid fixture hash')
    return {e['path']: (e['size'], e['sha256']) for e in files}


def remote_files(connection):
    manifest = fetch_manifest(connection)
    check(manifest.get('writable') is True, 'Benchmark server must be writable')
    check(all(e['kind'] == 'file' and not e['deleted'] for e in manifest['entries']),
          'Unexpected directories or deleted entries on benchmark server')
    return {e['path']: (e['size'], e['sha256']) for e in manifest['entries']}


def local_files(root):
    result = {}
    for path in root.iterdir():
        check(path.is_file() and not path.is_symlink(), 'Unexpected local object')
        with path.open('rb') as stream:
            result[path.name] = (path.stat().st_size, hashlib.file_digest(stream, 'sha256').hexdigest())
    return result


def run_client(args, report):
    fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
    expected = validate_fixture(fixture)
    report['fixture_id'] = fixture['id']
    project = args.output / 'client'
    initialize(project, 'client', args.url)
    export_client_credentials(args.bundle, project / 'credentials')
    root, state = project / 'shared', project / 'state'
    with make_client(args.url, read_token(project / 'credentials/token.txt'),
                     project / 'credentials/ca.pem', request_timeout=args.timeout) as connection:
        check(remote_files(connection) == expected, 'Server does not match the pristine fixture; no sync started')
        report['passes'] = []

        def measure(direction, count, size):
            starts, transfers = {}, []
            def progress(event):
                key = (event['direction'], event['path'])
                if event['phase'] == 'started':
                    starts[key] = time.monotonic()
                elif event['phase'] == 'completed':
                    transfers.append({'path': event['path'], 'direction': event['direction'],
                                      'bytes': event['bytes'], 'seconds': time.monotonic() - starts[key]})
            begin = time.monotonic()
            result = synchronize(connection, root, state, on_progress=progress)
            elapsed = time.monotonic() - begin
            check(not result['conflicts'], 'Unexpected benchmark conflict')
            check(result[direction + 'ed'] == count and result[direction + 'ed_bytes'] == size,
                  'Unexpected transferred counts or bytes')
            other = 'downloaded' if direction == 'upload' else 'uploaded'
            check(result[other] == 0 and result['deleted_local'] == 0 and result['deleted_remote'] == 0,
                  'Unexpected reverse transfer or deletion')
            report['passes'].append({'direction': direction, 'files': count, 'bytes': size,
                                     'seconds': elapsed, 'effective_mib_per_second': size / 1048576 / elapsed,
                                     'transfers': transfers})
            print(f'{direction}: {size / 1048576:.0f} MiB in {elapsed:.3f}s', flush=True)

        total = sum(size for size, sha in expected.values())
        measure('download', len(expected), total)
        check(local_files(root) == expected, 'Downloaded files failed independent SHA-256 verification')
        uploads = {}
        for index, entry in enumerate(fixture['files']):
            name = f'upload-{index:03}.bin'
            sha = write_payload(root / name, entry['size'], f"{fixture['id']}/{name}")
            uploads[name] = (entry['size'], sha)
        measure('upload', len(uploads), total)
        check(remote_files(connection) == expected | uploads, 'Server hashes differ after upload')
        check(local_files(root) == expected | uploads, 'Final local hashes differ')
        check(not (state / 'sync.lock').exists(), 'Client lock remains')
        report['final_hashes_match'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    server = modes.add_parser('prepare-server')
    server.add_argument('--host', required=True, help='Concrete server IP, also used for TLS certificate')
    server.add_argument('--port', type=int, default=8877)
    server.add_argument('--sizes-mib', type=int, nargs='+', default=[64, 256])
    client = modes.add_parser('run-client')
    client.add_argument('--url', required=True)
    client.add_argument('--bundle', type=Path, required=True)
    client.add_argument('--fixture', type=Path, required=True)
    client.add_argument('--timeout', type=float, default=120)
    for subparser in (server, client):
        subparser.add_argument('--output', type=Path, required=True, help='New isolated directory')
    args = parser.parse_args()
    if args.mode == 'prepare-server':
        if not sys.platform.startswith('linux'):
            parser.error('Server preparation requires Linux')
        if not 1 <= args.port <= 65535 or any(n <= 0 for n in args.sizes_mib):
            parser.error('Invalid port or sizes')
    elif not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('Timeout must be positive and finite')
    args.output = args.output.expanduser().absolute()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'created_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'python': platform.python_version(), 'lansync_version': __version__, 'result': 'failed',
              'mode': args.mode, 'method': 'Deterministic generated files; sync time includes scans, TLS, hashing and disk I/O. No raw network throughput claim.'}
    try:
        if args.mode == 'prepare-server':
            prepare(args)
        else:
            report['server_url'] = args.url
            run_client(args, report)
        report['result'] = 'passed'
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        export_manifest(report, args.output / 'verification.json')


if __name__ == '__main__':
    main()
