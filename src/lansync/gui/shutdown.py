"""Cooperative desktop-session and process-signal shutdown."""
import signal
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from PySide6.QtCore import QTimer, QStandardPaths
from PySide6.QtGui import QSessionManager


class Shutdown:
    def __init__(self, app, window, diagnostics_directory=None):
        self.window = window
        self.previous = {}
        self.reason = 'qt-quit'
        self.diagnostics_directory = Path(diagnostics_directory) if diagnostics_directory else Path(
            QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        app.commitDataRequest.connect(self.commit)
        app.saveStateRequest.connect(self.save)
        app.aboutToQuit.connect(self.drain)
        numbers = [signal.SIGTERM, signal.SIGINT]
        if hasattr(signal, 'SIGHUP'):
            numbers.append(signal.SIGHUP)
        for number in numbers:
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.request)
        # Let Python dispatch signals even while the Qt event loop is idle.
        self.timer = QTimer(window)
        self.timer.setInterval(100)
        self.timer.timeout.connect(lambda: None)
        self.timer.start()

    def record(self, status):
        # Diagnostic only; never modify a project's lock or recovery records.
        temporary = None
        try:
            self.diagnostics_directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.diagnostics_directory,
                                             prefix='.shutdown-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'pid': os.getpid(), 'reason': self.reason, 'status': status,
                           'utc': datetime.now(timezone.utc).isoformat()}, stream)
            os.replace(temporary, self.diagnostics_directory / 'last-shutdown.json')
        except OSError:
            pass  # A diagnostic write failure must not prevent worker cleanup.
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def request(self, number, *_):
        self.reason = signal.Signals(number).name
        self.record('requested')
        self.window.closing = True
        if self.window.controller.busy:
            self.window.request_stop()
        else:
            self.window.close()

    def drain(self):
        self.record('requested')
        controller = self.window.controller
        controller.stop()
        task = controller.task
        if task is not None and task.isRunning():
            # Worker code never waits for GUI slots: it can finish and release
            # sync.lock while the main thread waits for the current operation.
            task.wait()
        self.window.settings.sync()
        failed = self.window.failed or (task is not None and getattr(task, 'failure', None))
        self.record('completed_with_errors' if failed else 'completed')

    def save(self, manager):
        manager.setRestartHint(QSessionManager.RestartNever)
        self.window.settings.sync()

    def commit(self, manager):
        self.reason = 'session-commit'
        self.save(manager)
        self.drain()

    def restore(self):
        self.timer.stop()
        for number, handler in self.previous.items():
            signal.signal(number, handler)
