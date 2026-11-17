import os
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
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config = initialize(base/'project', 'client', 'https://localhost:8765')
            (config.parent/'credentials/token.txt').write_text('a'*40)
            window = MainWindow(QSettings(str(base/'ui.ini'), QSettings.IniFormat))
            shutdown = Shutdown(Mock(), window)
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
        shutdown = Shutdown(Mock(), window)
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
            shutdown = Shutdown(self.app, window)
            try:
                shutdown.save(Mock())
            finally:
                shutdown.restore()
                self.app.commitDataRequest.disconnect(shutdown.commit)
                self.app.saveStateRequest.disconnect(shutdown.save)
                self.app.aboutToQuit.disconnect(shutdown.drain)
                window.close()
