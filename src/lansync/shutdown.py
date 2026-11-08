"""Cooperative POSIX termination for CLI loops, without changing library signals."""

from contextlib import contextmanager
import os
import signal
from threading import Event, current_thread, main_thread


@contextmanager
def termination_event():
    """Finish current work on SIGTERM; restore the caller's handler on exit.

    Windows process termination is not a catchable POSIX signal. Embedded CLI
    calls in worker threads also leave process-wide signal handling untouched.
    Ctrl+C keeps its usual KeyboardInterrupt behavior.
    """
    stop = Event()
    installed = os.name == 'posix' and current_thread() is main_thread()
    if installed:
        previous = signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    try:
        yield stop
    finally:
        if installed:
            signal.signal(signal.SIGTERM, previous)
