import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest

from lansync.shutdown import termination_event


class ShutdownTests(unittest.TestCase):
    def test_worker_thread_does_not_change_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        errors = []
        def worker():
            try:
                with termination_event() as stop:
                    self.assertFalse(stop.is_set())
            except BaseException as exc:
                errors.append(exc)
        thread = Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signal handlers')
    def test_handler_restored_after_error_and_repeated_signal(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(ValueError):
            with termination_event() as stop:
                signal.raise_signal(signal.SIGTERM)
                signal.raise_signal(signal.SIGTERM)
                self.assertTrue(stop.is_set())
                raise ValueError('test')
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    @unittest.skipUnless(os.name == 'posix', 'POSIX process termination')
    def test_cli_loops_exit_on_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'root'
            root.mkdir()
            (base / 'token').write_text('a' * 64)
            cases = [
                (['watch', str(root), '--state-dir', str(base / 'watch'), '--interval', '300'],
                 lambda: (base / 'watch/manifest.json').exists()),
                (['status', '--state-dir', str(base / 'missing'), '--watch', '--interval', '300'],
                 lambda: 'Status unavailable' in log.read_text()),
                (['sync', 'http://127.0.0.1:1', str(root), '--state-dir', str(base / 'sync'),
                  '--allow-http', '--token-file', str(base / 'token'), '--watch', '--local-events',
                  '--interval', '300', '--retry-max', '300'],
                 lambda: 'retrying' in log.read_text()),
            ]
            for index, (args, ready) in enumerate(cases):
                with self.subTest(command=args[0]):
                    log = base / f'{index}.log'
                    with log.open('w') as output:
                        process = subprocess.Popen([sys.executable, '-m', 'lansync', *args],
                                                   stdout=output, stderr=subprocess.STDOUT)
                        try:
                            deadline = time.monotonic() + 10
                            while not ready() and process.poll() is None and time.monotonic() < deadline:
                                time.sleep(.02)
                            self.assertTrue(ready(), log.read_text())
                            process.send_signal(signal.SIGTERM)
                            self.assertEqual(process.wait(timeout=5), 0, log.read_text())
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait(timeout=5)
            self.assertFalse((base / 'sync/sync.lock').exists())
            self.assertEqual(json.loads((base / 'sync/loop-status.json').read_text())['status'], 'stopped')

    @unittest.skipUnless(os.name == 'posix', 'POSIX signal handlers')
    def test_active_pass_finishes_before_unlock(self):
        from unittest.mock import patch
        from lansync.sync_loop import continuous_sync
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            root.mkdir()
            state = Path(tmp) / 'state'
            completed = []
            def synchronize(*args):
                signal.raise_signal(signal.SIGTERM)
                self.assertTrue((state / 'sync.lock').exists())
                completed.append(True)
                return {'conflicts': [], 'uploaded': 1}
            with termination_event() as stop, patch('lansync.sync_loop._synchronize', side_effect=synchronize) as operation:
                continuous_sync(None, root, state, stop=stop, interval=300, retry_max=300)
            operation.assert_called_once()
            self.assertEqual(completed, [True])
            self.assertFalse((state / 'sync.lock').exists())
            status = json.loads((state / 'loop-status.json').read_text())
            self.assertEqual(status['status'], 'stopped')
            self.assertEqual(status['report']['uploaded'], 1)
