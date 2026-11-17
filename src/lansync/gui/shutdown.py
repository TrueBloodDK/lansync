"""Cooperative desktop-session and process-signal shutdown."""
import signal
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSessionManager


class Shutdown:
    def __init__(self, app, window):
        self.window = window
        self.previous = {}
        app.commitDataRequest.connect(self.commit)
        app.saveStateRequest.connect(self.save)
        app.aboutToQuit.connect(self.drain)
        for number in (signal.SIGTERM, signal.SIGINT):
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.request)
        # Let Python dispatch signals even while the Qt event loop is idle.
        self.timer = QTimer(window)
        self.timer.setInterval(100)
        self.timer.timeout.connect(lambda: None)
        self.timer.start()

    def request(self, *_):
        self.window.closing = True
        if self.window.controller.busy:
            self.window.request_stop()
        else:
            self.window.close()

    def drain(self):
        controller = self.window.controller
        controller.stop()
        task = controller.task
        if task is not None and task.isRunning():
            # Worker code never waits for GUI slots: it can finish and release
            # sync.lock while the main thread waits for the current operation.
            task.wait()
        self.window.settings.sync()

    def save(self, manager):
        manager.setRestartHint(QSessionManager.RestartNever)
        self.window.settings.sync()

    def commit(self, manager):
        self.save(manager)
        self.drain()

    def restore(self):
        self.timer.stop()
        for number, handler in self.previous.items():
            signal.signal(number, handler)
