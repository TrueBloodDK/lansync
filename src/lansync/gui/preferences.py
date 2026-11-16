"""Edit scheduling options without changing a project's synchronization identity."""
import json
import os
from pathlib import Path
import tempfile
import tomllib

from ..config import load_project
from ..durability import sync_directory
from ..sync import locked_sync_state

EDITABLE = {'interval', 'retry_max', 'request_timeout', 'event_debounce', 'local_events'}


def save_preferences(config, expected, changes):
    path = Path(config).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('Нельзя изменять конфигурацию через символическую ссылку.')
    path = path.resolve(strict=True)
    project = load_project(path)
    if project.command != 'sync' or not changes.keys() <= EDITABLE:
        raise ValueError('Можно изменять только параметры ожидания и наблюдения клиентского проекта.')
    args = project.namespace()
    if path == args.root or args.root in path.parents:
        raise ValueError('Конфигурация должна находиться вне синхронизируемой папки.')
    if path.read_bytes() != expected:
        raise ValueError('Конфигурация изменилась. Закройте настройки и откройте их заново.')
    data = tomllib.loads(expected.decode('utf-8'))
    data.update(changes)
    text = ''.join(json.dumps(k, ensure_ascii=False) + ' = ' + json.dumps(v, ensure_ascii=False) + '\n'
                   for k, v in data.items())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.lansync-config-', suffix='.toml', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        load_project(temporary)  # Same parent preserves all relative paths; validates types/ranges.
        with locked_sync_state(args.root, args.state_dir):
            if path.read_bytes() != expected:
                raise ValueError('Конфигурация изменилась во время сохранения. Откройте настройки заново.')
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.backup-', suffix='.toml', delete=False) as backup:
                backup.write(expected)
                backup.flush()
                os.fsync(backup.fileno())
                backup_path = Path(backup.name)
            sync_directory(path.parent)
            os.replace(temporary, path)
            sync_directory(path.parent)
        return backup_path
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
