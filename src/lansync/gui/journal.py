"""Bounded local GUI journal. Never read credentials or synchronized files."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from .. import __version__


def redact(value):
    text = str(value)
    text = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1[скрыто]@', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s\"\']+', r'\1[скрыто]', text)
    # Valid LANSync tokens are 32..256 ASCII letters/digits/_/-. Redact the
    # whole matching run, including hashes, rather than risk saving a token.
    return re.sub(r'[A-Za-z0-9_-]{32,}', '[скрыто]', text)


class Journal:
    def __init__(self, directory, max_bytes=1024 * 1024, backups=3):
        if max_bytes < 512 or backups < 1:
            raise ValueError('Journal requires at least 512 bytes and one backup')
        self.directory = Path(directory)
        self.path = self.directory / 'desktop.jsonl'
        self.max_bytes, self.backups = max_bytes, backups
        self.error = None

    def append(self, message):
        message = redact(message)
        encoded = message.encode('utf-8')
        # JSON can expand a control character to six ASCII bytes.
        limit = min(16384, self.max_bytes // 16)
        stored = message if len(encoded) <= limit else encoded[:limit].decode('utf-8', errors='ignore') + ' [сокращено]'
        entry = json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),
                                pid=os.getpid(), version=__version__, message=stored),
                           ensure_ascii=False) + '\n'
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.exists() and self.path.stat().st_size + len(entry.encode('utf-8')) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = self.path if index == 1 else self.path.with_suffix(f'.jsonl.{index-1}')
                    target = self.path.with_suffix(f'.jsonl.{index}')
                    if source.exists():
                        os.replace(source, target)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(entry)
            self.error = None
        except OSError as exc:
            self.error = redact(exc)
        return message

    def export(self, destination):
        # A new file only: never overwrite a user's file or the journal itself.
        lines = [f'LANSync {__version__} — журнал GUI\n',
                 'Время UTC. Может содержать имена файлов и локальные пути.\n']
        for index in range(self.backups, -1, -1):
            source = self.path if index == 0 else self.path.with_suffix(f'.jsonl.{index}')
            if source.exists():
                lines.append(redact(source.read_text(encoding='utf-8')))
        with Path(destination).open('x', encoding='utf-8') as stream:
            stream.writelines(lines)
