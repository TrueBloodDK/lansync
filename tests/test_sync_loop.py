import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from lansync.sync_loop import continuous_sync


def report(**values):
    return {'uploaded': 0, 'downloaded': 0, 'deleted_local': 0, 'deleted_remote': 0, 'conflicts': [], **values}


class BoundedStop:
    def __init__(self, passes):
        self.passes = passes
        self.delays = []

    def is_set(self):
        return len(self.delays) >= self.passes

    def wait(self, delay):
        self.delays.append(delay)


class SyncLoopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.state = self.base / 'state'

    def run_loop(self, results, passes, **kwargs):
        stop = BoundedStop(passes)
        events = []
        with patch('lansync.sync_loop._synchronize', side_effect=results):
            continuous_sync(None, self.root, self.state, interval=1, retry_max=4,
                            stop=stop, on_event=events.append, **kwargs)
        return stop, events

    def test_reconnect_backoff_reset_and_release_lock(self):
        results = [httpx.ConnectError('offline')] * 4 + [report(), httpx.ConnectError('offline'), report()]
        stop, events = self.run_loop(results, 7)
        self.assertEqual(stop.delays, [1, 2, 4, 4, 1, 1, 1])
        self.assertTrue(any(e['status'] == 'retrying' for e in events))
        status = json.loads((self.state / 'loop-status.json').read_text())
        self.assertEqual(status['status'], 'stopped')
        self.assertIsNotNone(status['last_success_utc'])
        self.assertFalse((self.state / 'sync.lock').exists())

    def test_idle_is_quiet_but_transfers_are_reported(self):
        _, events = self.run_loop([report(), report(), report(uploaded=1), report(uploaded=1)], 4)
        self.assertEqual([e['status'] for e in events], ['starting', 'synced', 'synced', 'synced', 'stopped'])

    def test_conflicts_are_not_repeated_until_changed(self):
        _, events = self.run_loop([report(conflicts=['a']), report(conflicts=['a']), report()], 3)
        self.assertEqual([e['status'] for e in events], ['starting', 'conflicts', 'synced', 'stopped'])

    def test_auth_and_configuration_errors_stop(self):
        request = httpx.Request('GET', 'https://test/v1/manifest')
        errors = [httpx.HTTPStatusError('unauthorized', request=request, response=httpx.Response(401, request=request)),
                  httpx.ConnectError('CERTIFICATE_VERIFY_FAILED'), ValueError('wrong binding')]
        for error in errors:
            with self.subTest(error=error), patch('lansync.sync_loop._synchronize', side_effect=error):
                with self.assertRaises(type(error)):
                    continuous_sync(None, self.root, self.state, stop=BoundedStop(1))
                self.assertFalse((self.state / 'sync.lock').exists())
                self.assertEqual(json.loads((self.state / 'loop-status.json').read_text())['status'], 'failed')

    def test_lock_held_between_passes_and_ctrl_c(self):
        def operation(*args):
            self.assertTrue((self.state / 'sync.lock').exists())
            raise KeyboardInterrupt
        with patch('lansync.sync_loop._synchronize', side_effect=operation):
            continuous_sync(None, self.root, self.state)
        self.assertFalse((self.state / 'sync.lock').exists())

    def test_invalid_intervals_do_not_create_state(self):
        for interval in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                continuous_sync(None, self.root, self.state, interval=interval)
        self.assertFalse(self.state.exists())

    def test_native_change_triggers_early_pass(self):
        from threading import Event, Thread
        stop=Event(); first=Event(); finished=Event(); errors=[]; calls=[]
        def operation(*args):
            calls.append(1)
            if len(calls)==1: first.set()
            else: stop.set()
            return report()
        def worker():
            try:
                continuous_sync(None,self.root,self.state,interval=30,retry_max=30,stop=stop,local_events=True,event_debounce=.05)
            except BaseException as exc: errors.append(exc)
            finally: finished.set()
        with patch('lansync.sync_loop._synchronize',side_effect=operation):
            thread=Thread(target=worker); thread.start()
            try:
                self.assertTrue(first.wait(5))
                (self.root/'new.txt').write_text('change')
                self.assertTrue(finished.wait(5),'Local event did not trigger an early pass')
            finally:
                stop.set(); thread.join(5)
        self.assertFalse(errors)
        self.assertGreaterEqual(len(calls),2)
        self.assertFalse((self.state/'sync.lock').exists())

    def test_read_events_do_not_trigger_sync(self):
        from lansync.change_signal import ChangeSignal
        from watchdog.events import FileOpenedEvent, FileClosedNoWriteEvent, FileModifiedEvent
        signal=ChangeSignal()
        signal.on_any_event(FileOpenedEvent('file'))
        signal.on_any_event(FileClosedNoWriteEvent('file'))
        self.assertFalse(signal.changed.is_set())
        signal.on_any_event(FileModifiedEvent('file'))
        self.assertTrue(signal.changed.is_set())
