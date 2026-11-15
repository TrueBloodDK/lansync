"""Project setup without Qt; never overwrite projects or credential bundles."""
import json
from pathlib import Path
import ssl

from ..credentials import export_client_credentials
from ..network import read_token
from ..project_init import initialize
from ..durability import sync_directory
from ..config import load_project


def validate_bundle(source):
    source = Path(source).expanduser().resolve(strict=True)
    for name in ('ca.pem', 'token.txt'):
        if (source / name).is_symlink():
            raise ValueError('Файлы реквизитов не должны быть символическими ссылками.')
    read_token(source / 'token.txt')
    try:
        ssl.create_default_context(cadata=(source / 'ca.pem').read_text(encoding='ascii'))
    except (ssl.SSLError, UnicodeError) as exc:
        raise ValueError('В ca.pem должен находиться корректный PEM-сертификат.') from exc
    return source


def create_client(directory, url, bundle, root=None):
    directory = Path(directory).expanduser().absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError('Для проекта выберите новую, ещё не существующую папку.')
    parent = directory.parent.resolve(strict=True)
    source = validate_bundle(bundle)
    root = Path(root).expanduser().resolve(strict=True) if root else None
    if root:
        if not root.is_dir():
            raise ValueError('Локальная папка должна существовать.')
        if root == parent or root in parent.parents:
            raise ValueError('Проект и его реквизиты должны находиться вне синхронизируемой папки.')
        if source == root or root in source.parents:
            raise ValueError('Комплект реквизитов нельзя хранить в синхронизируемой папке.')
        if root.stat().st_dev != parent.stat().st_dev:
            raise ValueError('Папка файлов и проект должны находиться на одном томе для recovery.')
    config = initialize(directory, 'client', url.strip())
    try:
        if root:
            import os
            text = config.read_text(encoding='utf-8').replace('root = "./shared"', 'root = ' + json.dumps(str(root)))
            with config.open('w', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(config.parent)
        export_client_credentials(source, directory / 'credentials')
    except Exception as exc:
        raise ValueError(f'Создание не завершено. Проверьте оставленный проект {directory}; '
                         'его файлы автоматически не удалены.') from exc
    return config


def import_bundle(config, bundle):
    project = load_project(config)
    if project.command != 'sync':
        raise ValueError('Импорт доступен только для клиентского проекта.')
    args = project.namespace()
    source = validate_bundle(bundle)
    destination = args.token_file.parent
    if (args.token_file.name != 'token.txt' or args.ca_file is None or
            args.ca_file.name != 'ca.pem' or args.ca_file.parent != destination):
        raise ValueError('Импорт требует token.txt и ca.pem в одном каталоге реквизитов проекта.')
    if destination == args.root or args.root in destination.parents:
        raise ValueError('Реквизиты должны находиться вне синхронизируемой папки.')
    if (args.state_dir / 'sync.lock').exists():
        raise ValueError('Есть sync.lock. Сначала проверьте и остановите работающий клиент.')
    return export_client_credentials(source, destination)
