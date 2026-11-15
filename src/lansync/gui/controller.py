"""One owned background task; Qt objects never perform network I/O on the UI thread."""
from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..config import load_project
from ..diagnostics import diagnose
from ..network import make_client, read_token
from ..project_inspection import inspect_project
from ..sync import synchronize
from ..sync_loop import continuous_sync


def client_project(path):
    project = load_project(path)
    if project.command != 'sync':
        raise ValueError('Выберите клиентский проект: command = "sync".')
    return project.namespace()


class Task(QThread):
    event = Signal(object)
    progress = Signal(object)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, config, action, parent=None):
        super().__init__(parent)
        self.config, self.action = Path(config), action
        self.stop = Event()
        self._last_progress = 0.0
        self._secret = None

    def safe_text(self, text):
        return str(text).replace(self._secret, '[скрыто]') if self._secret else str(text)

    def report_event(self, event):
        event = dict(event)
        if event.get('error'):
            event['error'] = self.safe_text(event['error'])
        self.event.emit(event)

    def report_progress(self, event):
        # Bound queued GUI events even for many small files or very fast transfers.
        now = monotonic()
        if now - self._last_progress >= .15:
            self._last_progress = now
            self.progress.emit(dict(event))

    def run(self):
        try:
            args = client_project(self.config)
            if args.dry_run and self.action in {'start', 'once'}:
                raise ValueError('В конфигурации включён dry_run: доступен только план изменений.')
            if args.log_file and self.action in {'start', 'once'}:
                raise ValueError('GUI пока не поддерживает log_file. Используйте CLI или отдельную конфигурацию без log_file.')
            self._secret = read_token(args.token_file)
            if self.stop.is_set():
                return
            if self.action == 'doctor':
                self.result.emit(diagnose(args.url, args.token_file, args.ca_file,
                                          args.allow_http, args.request_timeout))
                return
            checks = inspect_project(self.config)
            failed = [item for item in checks['checks'] if not item['ok']]
            if failed:
                raise ValueError('\n'.join(f"{item['check']}: {item['detail']}" for item in failed))
            if self.stop.is_set():
                return
            with make_client(args.url, self._secret, args.ca_file, args.allow_http,
                             args.request_timeout) as connection:
                if self.action == 'start':
                    continuous_sync(connection, args.root, args.state_dir,
                                    interval=args.interval, retry_max=args.retry_max,
                                    stop=self.stop, on_event=self.report_event,
                                    on_progress=self.report_progress,
                                    local_events=args.local_events,
                                    event_debounce=args.event_debounce)
                else:
                    self.result.emit(synchronize(connection, args.root, args.state_dir,
                                                 dry_run=self.action == 'plan',
                                                 on_progress=self.report_progress))
        except Exception as exc:
            self.error.emit(self.safe_text(exc))
        finally:
            self._secret = None


class Controller(QObject):
    busy_changed = Signal(bool)
    event = Signal(object)
    progress = Signal(object)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.task = None

    @property
    def busy(self):
        return self.task is not None

    def start(self, config, action):
        if self.busy:
            raise RuntimeError('Операция уже выполняется. Дождитесь её завершения.')
        if action not in {'start', 'once', 'plan', 'doctor'}:
            raise ValueError('Неизвестная операция')
        task = Task(config, action, self)
        task.event.connect(self.event.emit)
        task.progress.connect(self.progress.emit)
        task.result.connect(self.result.emit)
        task.error.connect(self.error.emit)
        task.finished.connect(self._finished)
        self.task = task
        self.busy_changed.emit(True)
        task.start()

    def stop(self):
        if self.task:
            # Direct thread-safe flag: a queued worker slot cannot interrupt its loop.
            self.task.stop.set()

    @Slot()
    def _finished(self):
        task, self.task = self.task, None
        task.deleteLater()
        self.busy_changed.emit(False)
        self.finished.emit()
