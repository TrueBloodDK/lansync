"""Inspect and export recovery copies without overwriting user files."""
import json
import re
import shutil
import tempfile
from pathlib import Path

from .scanner import hash_file
from .sync import publish_new


def read_copy(directory, identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('Invalid recovery identifier')
    metadata = directory / (identifier + '.json')
    backup = directory / (identifier + '.bak')
    if metadata.is_symlink() or backup.is_symlink():
        raise ValueError('Recovery symlinks are not supported')
    data = json.loads(metadata.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('path'), str):
        raise ValueError('Invalid recovery metadata')
    previous = data.get('previous')
    if not isinstance(previous, dict) or previous.get('kind') != 'file':
        raise ValueError('Invalid recovery file metadata')
    if not isinstance(previous.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', previous['sha256']):
        raise ValueError('Invalid recovery hash')
    if type(previous.get('size')) is not int or previous['size'] < 0:
        raise ValueError('Invalid recovery size')
    return backup, data


def recovery_directory(state):
    state = Path(state).expanduser().resolve(strict=True)
    if not state.is_dir():
        raise ValueError('State must be a directory')
    directory = state / 'recovery'
    if directory.is_symlink():
        raise ValueError('Recovery directory must not be a symlink')
    return directory


def list_copies(state):
    directory = recovery_directory(state)
    result = []
    if not directory.exists():
        return result
    for metadata in sorted(directory.glob('*.json')):
        item = {'id': metadata.stem}
        try:
            backup, data = read_copy(directory, metadata.stem)
            item.update(path=data['path'], size=data['previous']['size'], sha256=data['previous']['sha256'])
            item['available'] = backup.is_file()
        except (OSError, ValueError) as exc:
            item['error'] = str(exc)
        result.append(item)
    return result


def restore_copy(state, identifier, destination):
    directory = recovery_directory(state)
    backup, data = read_copy(directory, identifier)
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; choose a new file')
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    state_root = directory.parent.resolve()
    if destination == state_root or state_root in destination.parents:
        raise ValueError('Restore destination must be outside the state directory')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix='.lansync-restore-', delete=False) as stream:
            temporary = Path(stream.name)
            with backup.open('rb') as source:
                shutil.copyfileobj(source, stream, length=1024 * 1024)
        info, digest = hash_file(temporary)
        if info.st_size != data['previous']['size'] or digest != data['previous']['sha256']:
            raise ValueError('Recovery copy hash or size mismatch')
        publish_new(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
