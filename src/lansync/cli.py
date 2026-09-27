import argparse
import hashlib
import os
from pathlib import Path
import sys
import sqlite3

from .manifest import export_manifest
from .scanner import scan_tree
from .storage import Store


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
    scan = commands.add_parser("scan", help="Scan a folder and export its manifest")
    scan.add_argument("root", type=Path)
    scan.add_argument("--state-dir", type=Path)
    scan.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
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
        store = Store(database, root)
        try:
            result = scan_tree(root)
            manifest = store.update(result)
            export_manifest(manifest, destination)
        finally:
            store.close()
        files = sum(entry["kind"] == "file" for entry in result.entries.values())
        print(f"Files: {files}; directories: {len(result.entries) - files}; errors: {len(result.errors)}; skipped: {len(result.skipped)}")
        print(f"Manifest: {destination}")
        for error in result.errors:
            print(f"{error['path']}: {error['message']}", file=sys.stderr)
        return 0 if manifest["complete"] else 2
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
