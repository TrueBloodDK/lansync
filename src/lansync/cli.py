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


def default_state(root):
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return base / "lansync" / key


def outside_root(path, root):
    if path == root or root in path.parents:
        raise ValueError("State and manifest must be outside the scanned folder")


def main(argv=None):
    parser = argparse.ArgumentParser(description="LANSync filesystem index")
    parser.add_argument("--version", action="version", version="LANSync 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    recovery = commands.add_parser('recovery', help='List or export recovery copies')
    recovery_commands = recovery.add_subparsers(dest='recovery_command', required=True)
    listing = recovery_commands.add_parser('list')
    listing.add_argument('--state-dir', type=Path, required=True)
    listing.add_argument('--json', action='store_true')
    restore = recovery_commands.add_parser('restore')
    restore.add_argument('id')
    restore.add_argument('--state-dir', type=Path, required=True)
    restore.add_argument('--output', type=Path, required=True)
    status = commands.add_parser('status', help='Inspect saved synchronization reports')
    status.add_argument('--state-dir', type=Path, required=True)
    status.add_argument('--json', action='store_true', help='Print machine-readable JSON')
    run = commands.add_parser('run', help='Run a command from a TOML configuration')
    run.add_argument('config', type=Path)
    serve = commands.add_parser('serve', help='Serve a read-only manifest and files')
    serve.add_argument('root', type=Path)
    serve.add_argument('--state-dir', type=Path)
    serve.add_argument('--token-file', type=Path, required=True)
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--cert-file', type=Path)
    serve.add_argument('--key-file', type=Path)
    serve.add_argument('--allow-http', action='store_true', help='Local loopback testing only')
    serve.add_argument('--exclude', action='append', default=[])
    serve.add_argument('--allow-write', action='store_true', help='Allow revision-checked uploads and deletions')
    download = commands.add_parser('pull', help='Download absent files; stop on local conflicts')
    download.add_argument('url')
    download.add_argument('root', type=Path)
    download.add_argument('--token-file', type=Path, required=True)
    download.add_argument('--ca-file', type=Path)
    download.add_argument('--allow-http', action='store_true', help='Local loopback testing only')
    sync = commands.add_parser('sync', help='Reconcile local and server changes using a persistent baseline')
    sync.add_argument('url')
    sync.add_argument('root', type=Path)
    sync.add_argument('--state-dir', type=Path, required=True)
    sync.add_argument('--token-file', type=Path, required=True)
    sync.add_argument('--ca-file', type=Path)
    sync.add_argument('--allow-http', action='store_true')
    sync.add_argument('--watch', action='store_true', help='Repeat synchronization until Ctrl+C')
    sync.add_argument('--dry-run', action='store_true', help='Preview changes without transferring files or saving client state')
    sync.add_argument('--interval', type=float, default=30, help='Seconds between completed passes (default: 30)')
    sync.add_argument('--retry-max', type=float, default=300, help='Maximum retry delay (default: 300)')
    resolve = commands.add_parser('resolve', help='Resolve one recorded file conflict')
    resolve.add_argument('url')
    resolve.add_argument('root', type=Path)
    resolve.add_argument('path', help='Relative conflicted file path')
    resolve.add_argument('--choose', choices=['local', 'remote'], required=True)
    resolve.add_argument('--state-dir', type=Path, required=True)
    resolve.add_argument('--token-file', type=Path, required=True)
    resolve.add_argument('--ca-file', type=Path)
    resolve.add_argument('--allow-http', action='store_true')
    for name, help_text in (("scan", "Scan a folder and export its manifest"),
                            ("watch", "Watch a folder and keep its manifest updated")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("root", type=Path)
        command.add_argument("--state-dir", type=Path)
        command.add_argument("--manifest", type=Path)
        command.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                             help="Exclude matching names or relative paths; repeatable")
        if name == "watch":
            command.add_argument("--debounce", type=float, default=0.5, help="Quiet period in seconds (default: 0.5)")
            command.add_argument("--interval", type=float, default=60, help="Periodic scan interval (default: 60)")
            command.add_argument("--max-delay", type=float, default=5, help="Maximum event batching time (default: 5)")
    for name in ('scan', 'watch', 'serve', 'pull', 'sync', 'resolve'):
        commands.choices[name].add_argument('--log-file', type=Path, help='New log file outside synchronized and state folders')
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    if args.command == 'run':
        from .config import load_arguments
        try:
            return main(load_arguments(args.config, commands))
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
            cleaned = []
            iterator = iter(raw_args)
            for item in iterator:
                if item == '--':
                    cleaned.extend([item, *iterator])
                    break
                if item == '--log-file':
                    next(iterator)
                elif not item.startswith('--log-file='):
                    cleaned.append(item)
            with transcript(path, secrets):
                code = main(cleaned)
                print(f'LANSync exit code: {code}')
                return code
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'recovery':
        from .recovery import list_copies, restore_copy
        import json
        try:
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
        from .status import show_status
        try:
            return show_status(args.state_dir, as_json=args.json)
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command == 'run':
        from .config import load_arguments
        try:
            args = parser.parse_args(load_arguments(args.config, commands))
        except (OSError, ValueError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
    if args.command in {'serve', 'pull', 'sync', 'resolve'}:
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
                watch(root, database, destination, debounce=args.debounce,
                      interval=args.interval, max_delay=args.max_delay, exclude_patterns=patterns)
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
            with make_client(args.url, token, args.ca_file, args.allow_http) as client:
                result = resolve_conflict(client, root, args.state_dir, args.path, args.choose)
            print(result)
            return 2 if result['conflicts'] else 0
        if args.command == 'sync':
            from .sync import synchronize
            if args.dry_run and args.watch:
                raise ValueError('--dry-run cannot be combined with --watch')
            with make_client(args.url, token, args.ca_file, args.allow_http) as client:
                if args.watch:
                    from .sync_loop import continuous_sync
                    def show(event):
                        print(f"Sync: {event['status']}", flush=True)
                        if event.get('error'):
                            print(event['error'], file=sys.stderr, flush=True)
                        if event.get('report'):
                            print(event['report'], flush=True)
                    continuous_sync(client, root, args.state_dir, interval=args.interval,
                                    retry_max=args.retry_max, on_event=show)
                    return 0
                result = synchronize(client, root, args.state_dir, dry_run=args.dry_run)
            if args.dry_run:
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
            with make_client(args.url, token, args.ca_file, args.allow_http) as client:
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
