import argparse
import hashlib
import os
from pathlib import Path
import sys
import sqlite3
import logging

from .manifest import export_manifest
from .scanner import scan_tree
from .storage import Store
from .policy import validate_patterns
from .shutdown import termination_event


from .locations import default_state


def outside_root(path, root):
    if path == root or root in path.parents:
        raise ValueError("State and manifest must be outside the scanned folder")


def main(argv=None):
    parser = argparse.ArgumentParser(description="LANSync filesystem index")
    from . import __version__
    parser.add_argument("--version", action="version", version=f"LANSync {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser('inspect', help='Check local project paths and credentials without connecting')
    inspect.add_argument('config', type=Path)
    inspect.add_argument('--json', action='store_true')
    credentials = commands.add_parser('credentials', help='Create LAN TLS credentials or export a client bundle')
    credential_commands = credentials.add_subparsers(dest='credential_command', required=True)
    create = credential_commands.add_parser('create')
    create.add_argument('--host', action='append', required=True)
    create.add_argument('--output-dir', type=Path, required=True)
    export = credential_commands.add_parser('export-client')
    export.add_argument('--source-dir', type=Path, required=True)
    export.add_argument('--output-dir', type=Path, required=True)
    init = commands.add_parser('init', help='Create a new client or server project directory')
    init.add_argument('directory', type=Path)
    init.add_argument('--mode', choices=['client', 'server'], required=True)
    init.add_argument('--url', help='HTTPS server URL for a client project')
    doctor = commands.add_parser('doctor', help='Diagnose server access without transferring files')
    doctor.add_argument('url')
    doctor.add_argument('--token-file', type=Path, required=True)
    doctor.add_argument('--ca-file', type=Path)
    doctor.add_argument('--allow-http', action='store_true')
    doctor.add_argument('--request-timeout', type=float, default=60)
    doctor.add_argument('--json', action='store_true')
    recovery = commands.add_parser('recovery', help='List or export recovery copies')
    recovery_commands = recovery.add_subparsers(dest='recovery_command', required=True)
    listing = recovery_commands.add_parser('list')
    listing.add_argument('--state-dir', type=Path, required=True)
    listing.add_argument('--json', action='store_true')
    verify = recovery_commands.add_parser('verify', help='Verify hashes and detect incomplete recovery pairs')
    verify.add_argument('--state-dir', type=Path, required=True)
    verify.add_argument('--json', action='store_true')
    restore = recovery_commands.add_parser('restore')
    restore.add_argument('id')
    restore.add_argument('--state-dir', type=Path, required=True)
    restore.add_argument('--output', type=Path, required=True)
    status = commands.add_parser('status', help='Inspect saved synchronization reports')
    status.add_argument('--state-dir', type=Path, required=True)
    status.add_argument('--watch', action='store_true', help='Monitor saved reports until Ctrl+C')
    status.add_argument('--interval', type=float, default=1.0)
    status.add_argument('--json', action='store_true', help='Print machine-readable JSON')
    run = commands.add_parser('run', help='Run a command from a TOML configuration')
    run.add_argument('config', type=Path)
    run.add_argument('--check', action='store_true', help='Validate configuration syntax and argument combinations without running')
    run.add_argument('--action', choices=['start', 'once', 'plan', 'status', 'monitor', 'doctor', 'resolve', 'recovery-list', 'recovery-verify', 'recovery-restore'], default='start')
    run.add_argument('--id', help='Recovery copy ID for recovery-restore')
    run.add_argument('--output', type=Path, help='New destination file for recovery-restore')
    run.add_argument('--path', help='Relative conflict path for resolve')
    run.add_argument('--copy-path', help='New relative path for the local copy when choosing both')
    run.add_argument('--choose', choices=['local', 'remote', 'both'])
    run.add_argument('--json', action='store_true', help='Structured output for plan, status, doctor or recovery')
    from .config import SCHEMAS, REQUIRED
    for name, schema in SCHEMAS.items():
        command = commands.add_parser(name, allow_abbrev=False)
        positional = ('url', 'root') if name in {'sync', 'pull'} else ('root',)
        for key in positional:
            command.add_argument(key, type=schema[key][0])
        for key, (kind, default) in schema.items():
            if key in positional:
                continue
            flag = '--' + key.replace('_', '-')
            kwargs = {'default': list(default) if kind is list else default}
            if key in REQUIRED[name]:
                kwargs['required'] = True
            if kind is bool:
                kwargs['action'] = 'store_true'
            elif kind is list:
                kwargs['action'] = 'append'
            else:
                kwargs['type'] = kind
            command.add_argument(flag, **kwargs)
    resolve = commands.add_parser('resolve', help='Resolve one recorded file conflict')
    resolve.add_argument('url')
    resolve.add_argument('root', type=Path)
    resolve.add_argument('path', help='Relative conflicted file path')
    resolve.add_argument('--copy-path', help='New relative path for the local copy when choosing both')
    resolve.add_argument('--choose', choices=['local', 'remote', 'both'], required=True)
    resolve.add_argument('--state-dir', type=Path, required=True)
    resolve.add_argument('--token-file', type=Path, required=True)
    resolve.add_argument('--ca-file', type=Path)
    resolve.add_argument('--allow-http', action='store_true')
    resolve.add_argument('--request-timeout', type=float, default=60)
    resolve.add_argument('--log-file', type=Path)
    resolve.allow_abbrev = False
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    return execute(args)


def execute(args):
    if args.command == 'inspect':
        import json
        from .project_inspection import inspect_project
        try:
            report = inspect_project(args.config)
            if args.json:
                print(json.dumps(report, ensure_ascii=True, indent=2))
            else:
                for check in report['checks']:
                    print(f"{'OK' if check['ok'] else 'FAIL'} {check['check']}: {check['detail']}")
                print('Local checks only; no network request or write test was performed.')
            return 0 if report['ok'] else 1
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'credentials':
        from .credentials import create_credentials, export_client_credentials
        try:
            destination = (create_credentials(args.output_dir, args.host) if args.credential_command == 'create'
                           else export_client_credentials(args.source_dir, args.output_dir))
            print(f'Credentials written to: {destination}')
            print('Transfer only ca.pem and token.txt to clients; keep server-key.pem on the server.')
            return 0
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'init':
        from .project_init import initialize
        try:
            config = initialize(args.directory, args.mode, args.url)
            print(f'Created: {config}')
            print('Place credentials in the credentials directory before starting. See INSTALL.md.')
            return 0
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'doctor':
        import json
        from .diagnostics import diagnose
        report = diagnose(args.url, args.token_file, args.ca_file, args.allow_http, args.request_timeout)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            for check in report['checks']:
                print(f"{check['check']}: {'OK' if check['ok'] else check['reason']}")
            if report['server']:
                for key, value in report['server'].items():
                    print(f'{key}: {value}')
                if not report['server']['writable']:
                    print('Read-only server: pull is available; sync requires --allow-write on the server.')
            print(f"Duration: {report['duration_seconds']} seconds")
        return 0 if report['ok'] else 1
    if args.command == 'run':
        from .config import load_project
        try:
            project = load_project(args.config)
            configured = project.namespace()
            from .options import validate_options
            validate_options(configured)
            from .workflow import prepare_action
            configured = prepare_action(configured, args)
            if args.check:
                from .options import validate_options
                validate_options(configured)
                print(f'Configuration syntax OK: {configured.command}; paths, credentials and connectivity were not checked.')
                return 0
            return execute(configured)
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if getattr(args, 'log_file', None):
        from .logging_output import transcript
        try:
            path = args.log_file.expanduser().absolute()
            root = args.root.expanduser().resolve()
            outside_root(path.resolve(), root)
            state = (getattr(args, 'state_dir', None) or default_state(root)).expanduser().resolve()
            outside_root(path.resolve(), state)
            for suffix in ('', '.1', '.2', '.3'):
                candidate = Path(str(path) + suffix)
                if candidate.exists() or candidate.is_symlink():
                    raise ValueError('Log and rotation filenames must be new')
            secrets = []
            if getattr(args, 'token_file', None):
                from .network import read_token
                secrets.append(read_token(args.token_file))
            from types import SimpleNamespace
            unlogged = SimpleNamespace(**{**vars(args), 'log_file': None})
            with transcript(path, secrets):
                code = execute(unlogged)
                print(f'LANSync exit code: {code}', file=sys.stderr)
                return code
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'recovery':
        from .recovery import list_copies, restore_copy, verify_copies
        import json
        try:
            if args.recovery_command == 'verify':
                report = verify_copies(args.state_dir)
                if args.json:
                    print(json.dumps(report, ensure_ascii=True, indent=2))
                else:
                    for entry in report['entries']:
                        print(f"{entry['id']}: {entry['status']} — {entry.get('path', '')} {entry.get('error', '')}")
                    print(f"Verified: {report['verified']}; problems: {report['problems']}; total: {report['total']}")
                return 2 if report['problems'] else 0
            if args.recovery_command == 'restore':
                print(f'Restored: {restore_copy(args.state_dir, args.id, args.output)}')
            else:
                copies = list_copies(args.state_dir)
                if args.json:
                    print(json.dumps(copies, ensure_ascii=True, indent=2))
                else:
                    for copy in copies:
                        print(copy)
                    if not copies:
                        print('No recovery copies.')
            return 0
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'status':
        from .status import show_status, watch_status
        try:
            if args.watch:
                with termination_event() as stop:
                    return watch_status(args.state_dir, interval=args.interval, as_json=args.json, stop=stop)
            return show_status(args.state_dir, as_json=args.json)
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command in {'serve', 'pull', 'sync', 'resolve'}:
        from .options import validate_options
        try:
            validate_options(args)
        except ValueError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
        return network_command(args)
    try:
        patterns = validate_patterns(args.exclude)
        root = args.root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Root must be a directory")
        state = (args.state_dir or default_state(root)).expanduser().resolve()
        destination = (args.manifest or state / "manifest.json").expanduser().resolve()
        outside_root(state, root)
        outside_root(destination, root)
        database = state / "index.sqlite3"
        if destination == database or destination.name in {"index.sqlite3-journal", "index.sqlite3-wal", "index.sqlite3-shm"} and destination.parent == state:
            raise ValueError("Manifest cannot overwrite database files")
        if args.command == "watch":
            from .watcher import watch
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
            print(f"Watching: {root}; stop with Ctrl+C", flush=True)
            try:
                with termination_event() as stop:
                    watch(root, database, destination, debounce=args.debounce,
                          interval=args.interval, max_delay=args.max_delay, exclude_patterns=patterns, stop=stop)
            except KeyboardInterrupt:
                print("Watching stopped.")
            return 0
        store = Store(database, root)
        try:
            result = scan_tree(root, patterns)
            manifest = store.update(result)
            export_manifest(manifest, destination)
        finally:
            store.close()
        files = sum(entry["kind"] == "file" for entry in result.entries.values())
        print(f"Files: {files}; directories: {len(result.entries) - files}; errors: {len(result.errors)}; skipped: {len(result.skipped)}")
        print(f"Manifest: {destination}")
        for error in manifest["errors"] + manifest["portability_errors"]:
            print(f"{error['path']}: {error['message']}", file=sys.stderr)
        return 0 if manifest["complete"] else 2
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def network_command(args):
    import httpx
    import uvicorn
    from .network import create_app, make_client, pull, read_token
    try:
        root = args.root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Root must be a directory')
        outside_root(args.token_file.resolve(), root)
        token = read_token(args.token_file)
        if args.command == 'resolve':
            from .sync import resolve_conflict
            with make_client(args.url, token, args.ca_file, args.allow_http, args.request_timeout) as client:
                result = resolve_conflict(client, root, args.state_dir, args.path, args.choose, copy_path=args.copy_path)
            print(result)
            return 2 if result['conflicts'] else 0
        if args.command == 'sync':
            from .sync import synchronize
            def progress(event):
                print(f"{event['direction']} {event['path']}: {event['bytes']}/{event['total']} bytes ({event['phase']})", file=sys.stderr, flush=True)
            callback = progress if args.progress else None
            if args.dry_run and args.watch:
                raise ValueError('--dry-run cannot be combined with --watch')
            if args.json and not args.dry_run:
                raise ValueError('--json requires --dry-run')
            with make_client(args.url, token, args.ca_file, args.allow_http, args.request_timeout) as client:
                if args.watch:
                    from .sync_loop import continuous_sync
                    def show(event):
                        print(f"Sync: {event['status']}", flush=True)
                        if event.get('error'):
                            print(event['error'], file=sys.stderr, flush=True)
                        if event.get('report'):
                            print(event['report'], flush=True)
                    with termination_event() as stop:
                        continuous_sync(client, root, args.state_dir, interval=args.interval,
                                        retry_max=args.retry_max, on_event=show, stop=stop,
                                        local_events=args.local_events, event_debounce=args.event_debounce, on_progress=callback)
                    return 0
                result = synchronize(client, root, args.state_dir, dry_run=args.dry_run, on_progress=callback)
            if args.dry_run:
                if args.json:
                    import json
                    print(json.dumps(result, ensure_ascii=True, indent=2))
                    return 2 if result['conflicts'] else 0
                print('Preview only; files may change before the actual sync.')
                for action in result['planned']:
                    print(f"{action['operation']}: {action['path']}")
                for path in result['conflicts']:
                    print(f'Conflict: {path}', file=sys.stderr)
                print(f"Planned: {len(result['planned'])}; conflicts: {len(result['conflicts'])}")
                return 2 if result['conflicts'] else 0
            print(f"Uploaded: {result['uploaded']}; downloaded: {result['downloaded']}; deleted locally: {result['deleted_local']}; deleted remotely: {result['deleted_remote']}")
            for path in result['conflicts']:
                print(f'Conflict: {path}', file=sys.stderr)
            return 2 if result['conflicts'] else 0
        if args.command == 'pull':
            with make_client(args.url, token, args.ca_file, args.allow_http, args.request_timeout) as client:
                result = pull(client, root)
            print(f"Downloaded: {result['downloaded']}; unchanged: {result['unchanged']}")
            return 0
        state = (args.state_dir or default_state(root)).expanduser().resolve()
        outside_root(state, root)
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        if bool(args.cert_file) != bool(args.key_file):
            raise ValueError('Both --cert-file and --key-file are required')
        if not args.cert_file and not (args.allow_http and args.host in {'127.0.0.1', '::1'}):
            raise ValueError('TLS certificate required; HTTP testing must bind a literal loopback address')
        for credential in (args.cert_file, args.key_file):
            if credential:
                outside_root(credential.resolve(strict=True), root)
        app = create_app(root, state / 'index.sqlite3', token, args.exclude, allow_write=args.allow_write)
        uvicorn.run(app, host=args.host, port=args.port, access_log=False,
                    ssl_certfile=str(args.cert_file) if args.cert_file else None,
                    ssl_keyfile=str(args.key_file) if args.key_file else None)
        return 0
    except (OSError, ValueError, sqlite3.Error, httpx.HTTPError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
