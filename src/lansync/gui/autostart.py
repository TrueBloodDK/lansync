"""Per-user desktop login registration; no service and no administrator rights."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'LANSyncDesktop'


def launch_command(config, start_sync=False, minimized=False):
    config = Path(config).expanduser().resolve(strict=True)
    executable = Path(sys.executable)
    if not getattr(sys, 'frozen', False) and sys.platform == 'win32':
        executable = executable.with_name('pythonw.exe')
    if not executable.is_file():
        raise ValueError('Не найден исполняемый файл приложения.')
    command = [str(executable)]
    if not getattr(sys, 'frozen', False):
        command += ['-m', 'lansync.gui']
    command += ['--config', str(config)]
    if start_sync:
        command.append('--start-sync')
    if minimized:
        command.append('--minimized')
    if any(any(c in arg for c in '\r\n\x00') for arg in command):
        raise ValueError('Пути автозапуска не должны содержать переводы строк.')
    return command


def desktop_entry(command):
    # Desktop Entry Exec has two escaping layers, unlike shell quoting.
    if any('%' in arg or any(c in arg for c in '\r\n\x00') for arg in command) or '=' in command[0]:
        raise ValueError('Автозапуск не поддерживает %/переводы строк в путях или = в пути программы.')
    def quote(arg):
        escaped = ''.join('\\' + c if c in '\\"`$' else c for c in arg)
        return '"' + escaped.replace('\\', '\\\\') + '"'
    return ('[Desktop Entry]\nType=Application\nName=LANSync\nTerminal=false\n'
            'X-LANSync-Managed=true\nExec=' + ' '.join(map(quote, command)) + '\n')


class Registration:
    def __init__(self, platform=None, directory=None):
        self.platform = platform or sys.platform
        if self.platform not in {'win32', 'linux'}:
            raise ValueError('Автозапуск поддерживается на Windows и Linux.')
        base = Path(directory) if directory else Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        if self.platform == 'linux' and not base.is_absolute():
            raise ValueError('XDG_CONFIG_HOME должен быть абсолютным путём.')
        self.path = base / 'autostart/lansync-desktop.desktop'

    def read(self):
        if self.platform == 'win32':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                    return winreg.QueryValueEx(key, RUN_NAME)[0]
            except FileNotFoundError:
                return None
        if self.path.is_symlink():
            raise ValueError('Файл автозапуска не должен быть символической ссылкой.')
        return self.path.read_text(encoding='utf-8') if self.path.exists() else None

    def render(self, command):
        if self.platform == 'linux':
            return desktop_entry(command)
        value = subprocess.list2cmdline(command)
        if len(value) > 260:
            raise ValueError('Команда автозапуска Windows длиннее 260 символов. Используйте более короткий путь к приложению или проекту.')
        return value

    def replace(self, expected, value):
        if self.read() != expected:
            raise ValueError('Запись автозапуска изменилась. Откройте диалог заново.')
        if self.platform == 'win32':
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                if value is None:
                    if expected is not None:
                        winreg.DeleteValue(key, RUN_NAME)
                else:
                    winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, value)
            return
        if value is None:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.lansync-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            if self.read() != expected:
                raise ValueError('Запись автозапуска изменилась во время сохранения.')
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
