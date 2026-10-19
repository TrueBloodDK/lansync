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
    args = parser.parse_args(argv)
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
