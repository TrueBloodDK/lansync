import os
import json
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import signal
import tempfile
import time
from threading import Event
import unittest
from unittest.mock import Mock, patch

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings
    from lansync.gui.window import MainWindow
    from lansync.gui.shutdown import Shutdown
except ImportError:
    QApplication = None
from lansync.project_init import initialize
from lansync.sync import locked_sync_state


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class ShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_session_commit_waits_for_worker_and_releases_real_state_lock(self):
        from lansync.gui.controller import Task
        inline = patch('lansync.gui.controller.ProcessTask.run', Task.run)
        inline.start()
        self.addCleanup(inline.stop)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config = initialize(base/'project', 'client', 'https://localhost:8765')
            (config.parent/'credentials/token.txt').write_text('a'*40)
            window = MainWindow(QSettings(str(base/'ui.ini'), QSettings.IniFormat))
            shutdown = Shutdown(Mock(), window, base/'diagnostics')
            self.addCleanup(shutdown.restore)
            window.open_project(config)
            entered = Event()
            def diagnose(*_):
                with locked_sync_state(config.parent/'shared', config.parent/'state'):
                    entered.set()
                    assert window.controller.task.stop.wait(5)
                    time.sleep(.05)
                return {'ok': True}
            with patch('lansync.gui.controller.diagnose', side_effect=diagnose):
                window.start_action('doctor')
                self.assertTrue(entered.wait(3))
                manager = Mock()
                shutdown.commit(manager)
                self.assertFalse((config.parent/'state/sync.lock').exists())
                self.assertFalse(window.controller.task.isRunning())
                report = json.loads((base/'diagnostics/last-shutdown.json').read_text())
                self.assertEqual((report['reason'], report['status']), ('session-commit', 'completed'))
                with patch('lansync.gui.window.QMessageBox.question', side_effect=AssertionError('Logout must not prompt after drain')):
                    self.assertTrue(window.close())
                self.app.processEvents()
                self.assertFalse(window.controller.busy)
                manager.setRestartHint.assert_called_once()
            window.close()
            shutdown.restore()

    def test_signal_requests_cooperative_close_and_restores_handlers(self):
        from PySide6.QtWidgets import QWidget
        window = QWidget()
        window.controller = Mock()
        window.request_stop = Mock()
        previous = signal.getsignal(signal.SIGTERM)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        shutdown = Shutdown(Mock(), window, temp.name)
        try:
            shutdown.request(signal.SIGTERM, None)
            self.assertTrue(window.closing)
            window.request_stop.assert_called_once()
        finally:
            shutdown.restore()
            window.close()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_real_application_supports_session_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            window = MainWindow(QSettings(str(Path(temp)/'ui.ini'), QSettings.IniFormat))
            shutdown = Shutdown(self.app, window, Path(temp)/'diagnostics')
            try:
                shutdown.save(Mock())
            finally:
                shutdown.restore()
                self.app.commitDataRequest.disconnect(shutdown.commit)
                self.app.saveStateRequest.disconnect(shutdown.save)
                self.app.aboutToQuit.disconnect(shutdown.drain)
                window.close()

    @unittest.skipUnless(hasattr(signal, 'SIGHUP'), 'POSIX SIGHUP')
    def test_hangup_uses_cooperative_shutdown_and_restores_handler(self):
        from PySide6.QtWidgets import QWidget
        with tempfile.TemporaryDirectory() as temp:
            window = QWidget()
            window.controller = Mock()
            window.request_stop = Mock()
            before = signal.getsignal(signal.SIGHUP)
            shutdown = Shutdown(Mock(), window, temp)
            try:
                handler = signal.getsignal(signal.SIGHUP)
                handler(signal.SIGHUP, None)
                window.request_stop.assert_called_once()
                self.assertTrue(window.closing)
                report = json.loads((Path(temp)/'last-shutdown.json').read_text())
                self.assertEqual((report['reason'], report['status']), ('SIGHUP', 'requested'))
            finally:
                shutdown.restore()
                window.close()
            self.assertEqual(signal.getsignal(signal.SIGHUP), before)

    def test_diagnostic_write_failure_does_not_prevent_stop(self):
        from PySide6.QtWidgets import QWidget
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)/'file'
            destination.write_text('not a directory')
            window = QWidget()
            window.controller = Mock()
            window.request_stop = Mock()
            shutdown = Shutdown(Mock(), window, destination)
            try:
                shutdown.request(signal.SIGTERM)
                window.request_stop.assert_called_once()
                self.assertEqual(destination.read_text(), 'not a directory')
            finally:
                shutdown.restore()
                window.close()
