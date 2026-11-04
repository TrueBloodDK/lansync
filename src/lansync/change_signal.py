"""Coalesce native filesystem changes into an early sync wake-up."""
from contextlib import contextmanager
from threading import Event
from time import monotonic
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class ChangeSignal(FileSystemEventHandler):
    def __init__(self):
        self.changed = Event()

    def on_any_event(self, event):
        if event.event_type not in {'created', 'modified', 'deleted', 'moved', 'closed'}:
            return
        if event.is_directory and event.event_type == 'modified':
            return
        self.changed.set()

    def wait(self, stop, interval, debounce):
        deadline = monotonic() + interval
        event_deadline = None
        while not stop.is_set():
            now = monotonic()
            if self.changed.is_set() and event_deadline is None:
                # Fixed batching window: sustained activity cannot postpone forever.
                event_deadline = now + debounce
            remaining = min(deadline, event_deadline or deadline) - now
            if remaining <= 0:
                return
            stop.wait(min(remaining, .1))


@contextmanager
def observe_changes(root, enabled):
    if not enabled:
        yield None
        return
    signal = ChangeSignal()
    observer = Observer()
    observer.schedule(signal, str(root), recursive=True)
    try:
        observer.start()
        yield signal
    finally:
        observer.stop()
        if observer.is_alive():
            observer.join()
