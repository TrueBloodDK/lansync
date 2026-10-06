import hashlib
from pathlib import Path
from queue import Queue
from threading import Event, Thread
import tempfile
import time
from types import SimpleNamespace
import unittest

from lansync.watcher import ChangeHandler, positive_seconds, watch


class SilentObserver:
    """No event delivery: exercise the recovery scan independently."""
    ident = None

    def schedule(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def is_alive(self):
        return True


class WatchTests(unittest.TestCase):
    def test_event_coalescing_and_read_filter(self):
        pending = Queue(maxsize=1)
        handler = ChangeHandler(pending)
        for kind in ("opened", "closed_no_write"):
            handler.on_any_event(SimpleNamespace(event_type=kind))
        self.assertTrue(pending.empty())
        for _ in range(1000):
            handler.on_any_event(SimpleNamespace(event_type="modified"))
        self.assertEqual(pending.qsize(), 1)

    def test_invalid_intervals(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_seconds(value)

    def run_scenario(self, periodic=False):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            state = base / "state"
            stop = Event()
            manifests = Queue()
            failures = []

            def run():
                try:
                    options = {"observer_factory": SilentObserver} if periodic else {}
                    watch(root, state / "index.sqlite3", state / "manifest.json",
                          debounce=0.03, max_delay=0.15,
                          interval=0.1 if periodic else 30, stop=stop,
                          on_scan=manifests.put, **options)
                except BaseException as exc:
                    failures.append(exc)

            thread = Thread(target=run, daemon=True)
            thread.start()

            def until(predicate):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    snapshot = manifests.get(timeout=max(0.01, deadline - time.monotonic()))
                    entries = {entry["path"]: entry for entry in snapshot["entries"]}
                    if predicate(entries):
                        return snapshot
                self.fail("Expected manifest was not produced")

            try:
                until(lambda entries: not entries)
                folder = root / "nested"
                folder.mkdir()
                file = folder / "one.txt"
                file.write_bytes(b"first")
                until(lambda entries: entries.get("nested/one.txt", {}).get("sha256") == hashlib.sha256(b"first").hexdigest())
                file.write_bytes(b"second")
                until(lambda entries: entries.get("nested/one.txt", {}).get("sha256") == hashlib.sha256(b"second").hexdigest())
                file.rename(folder / "two.txt")
                until(lambda entries: entries.get("nested/one.txt", {}).get("deleted") and
                      "nested/two.txt" in entries and not entries["nested/two.txt"]["deleted"])
                (folder / "two.txt").unlink()
                folder.rmdir()
                until(lambda entries: entries and all(e["deleted"] for e in entries.values()))
            finally:
                stop.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "Observer did not shut down")
            self.assertEqual(failures, [])

    def test_native_events_update_manifest(self):
        self.run_scenario()

    def test_periodic_scan_recovers_without_events(self):
        self.run_scenario(periodic=True)

    def test_events_during_initial_scan_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            stop = Event()

            class InitialObserver(SilentObserver):
                def schedule(self, handler, *args, **kwargs):
                    self.handler = handler

            observer = InitialObserver()
            snapshots = []

            def after_scan(manifest):
                snapshots.append(manifest)
                if len(snapshots) == 1:
                    (root / "late").write_text("new")
                    observer.handler.on_any_event(SimpleNamespace(event_type="created"))
                else:
                    stop.set()

            # Bound failure duration even if the queue handling regresses.
            from threading import Timer
            timer = Timer(3, stop.set)
            timer.start()
            try:
                watch(root, base / "state" / "index.sqlite3", base / "manifest.json",
                      debounce=0.01, interval=60, stop=stop,
                      observer_factory=lambda: observer, on_scan=after_scan)
            finally:
                timer.cancel()
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[-1]["entries"][0]["path"], "late")
