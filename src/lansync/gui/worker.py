"""Headless operation owner; GUI pipe EOF means stop after the current operation."""
import os
import signal
from threading import Thread


def run_worker(connection, config, action, parameters):
    # Linux workers have no QApplication or X connection. Windows workers
    # instead need a hidden native window to receive session notifications.
    if os.name == 'posix':
        os.setsid()
    from PySide6.QtCore import Qt
    from .controller import Task
    task = Task(config, action, parameters=parameters)
    for number in (signal.SIGTERM, signal.SIGINT, getattr(signal, 'SIGHUP', None)):
        if number is not None:
            signal.signal(number, lambda *_: task.stop.set())

    def control():
        try:
            connection.recv()
        except (EOFError, OSError):
            pass
        task.stop.set()

    def send(kind, value):
        try:
            connection.send((kind, value))
        except (BrokenPipeError, EOFError, OSError):
            task.stop.set()

    for kind in ('event', 'progress', 'result', 'error'):
        getattr(task, kind).connect(lambda value, k=kind: send(k, value), Qt.DirectConnection)
    Thread(target=control, daemon=True).start()
    try:
        if os.name == 'nt':
            from .windows_worker import run_windows_task
            run_windows_task(task)
        else:
            task.run()
    finally:
        connection.close()
