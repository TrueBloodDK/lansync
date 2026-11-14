"""Persist file contents and POSIX directory entries at commit boundaries."""
import os
from pathlib import Path


def sync_directory(path):
    # Windows does not expose directory FlushFileBuffers through os.fsync.
    if os.name != 'posix':
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def make_directory(path, *, mode=0o777, exist_ok=True):
    path = Path(path)
    missing = []
    current = path
    while not current.exists():
        if current.parent == current:
            raise FileNotFoundError(f'Filesystem root is unavailable: {current}')
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, mode=mode, exist_ok=exist_ok)
    for directory in reversed(missing):
        sync_directory(directory.parent)


def sync_existing_file(path):
    if os.name == 'posix':
        with Path(path).open('rb') as stream:
            os.fsync(stream.fileno())
