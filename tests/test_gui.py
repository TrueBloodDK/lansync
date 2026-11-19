import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None
else:
    from lansync.gui.controller import Controller
    from lansync.gui.window import MainWindow

from lansync.project_init import initialize


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # These tests inject core responses; process lifetime has separate tests.
        from lansync.gui.controller import Task
        inline = patch('lansync.gui.controller.ProcessTask.run', Task.run)
        inline.start()
        self.addCleanup(inline.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = initialize(self.base / 'project', 'client', 'https://localhost:8765')
        (self.config.parent / 'credentials/token.txt').write_text('a' * 40)
        self.controller = Controller()
        self.addCleanup(self.finish_task)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), 'GUI task did not finish in time')

    def finish_task(self):
        self.controller.stop()
        self.wait_for(lambda: not self.controller.busy)

    def test_diagnostics_off_main_thread_and_duplicate_rejected(self):
        entered, release = Event(), Event()
        results = []
        self.controller.result.connect(results.append)
        def diagnose(*args):
            entered.set()
            release.wait(3)
            return {'ok': True}
        with patch('lansync.gui.controller.diagnose', side_effect=diagnose):
            self.controller.start(self.config, 'doctor')
            self.wait_for(entered.is_set)
            with self.assertRaises(RuntimeError):
                self.controller.start(self.config, 'once')
            release.set()
            self.wait_for(lambda: not self.controller.busy)
        self.assertEqual(results, [{'ok': True}])

    def test_stop_is_cooperative_and_client_closed(self):
        entered, closed = Event(), Event()
        @contextmanager
        def client(*args):
            try:
                yield object()
            finally:
                closed.set()
        def loop(*args, stop, **kwargs):
            entered.set()
            if not stop.wait(3):
                raise RuntimeError('Stop flag was not delivered')
        with patch('lansync.gui.controller.inspect_project', return_value={'checks': []}), \
             patch('lansync.gui.controller.make_client', side_effect=client), \
             patch('lansync.gui.controller.continuous_sync', side_effect=loop):
            self.controller.start(self.config, 'start')
            self.wait_for(entered.is_set)
            self.controller.stop()
            self.wait_for(lambda: not self.controller.busy)
        self.assertTrue(closed.is_set())

    def test_failure_redacts_token_and_allows_retry(self):
        errors = []
        self.controller.error.connect(errors.append)
        with patch('lansync.gui.controller.diagnose', side_effect=ValueError('token=' + 'a' * 40)):
            self.controller.start(self.config, 'doctor')
            self.wait_for(lambda: not self.controller.busy)
        self.assertEqual(errors, ['token=[скрыто]'])
        with patch('lansync.gui.controller.diagnose', return_value={'ok': True}):
            self.controller.start(self.config, 'doctor')
            self.wait_for(lambda: not self.controller.busy)

    def test_window_close_waits_and_does_not_switch_project(self):
        settings = QSettings(str(self.base / 'ui.ini'), QSettings.IniFormat)
        window = MainWindow(settings)
        self.controller = window.controller
        self.addCleanup(window.deleteLater)
        self.assertTrue(window.open_project(self.config))
        entered = Event()
        def diagnose(*args):
            entered.set()
            self.controller.task.stop.wait(3)
            return {'ok': True}
        with patch('lansync.gui.controller.diagnose', side_effect=diagnose), \
             patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            window.show()
            window.start_action('doctor')
            self.wait_for(entered.is_set)
            self.assertFalse(window.open_project(self.config))
            self.assertFalse(window.actions['start'].isEnabled())
            window.close()
            self.wait_for(lambda: not self.controller.busy)
            self.wait_for(lambda: not window.isVisible())
        self.assertEqual(window.state_label.text(), 'Остановлено')

    def test_invalid_project_does_not_replace_existing(self):
        window = MainWindow(QSettings(str(self.base / 'ui.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        self.assertTrue(window.open_project(self.config))
        server = initialize(self.base / 'server', 'server')
        self.assertFalse(window.open_project(server))
        self.assertEqual(window.config, self.config.resolve())

    def test_dry_run_configuration_cannot_start_writes(self):
        self.config.write_text(self.config.read_text().replace('watch = true', 'watch = false')
                               .replace('local_events = true', 'local_events = false') + 'dry_run = true\n')
        errors = []
        self.controller.error.connect(errors.append)
        with patch('lansync.gui.controller.continuous_sync') as sync:
            self.controller.start(self.config, 'start')
            self.wait_for(lambda: not self.controller.busy)
        sync.assert_not_called()
        self.assertIn('dry_run', errors[0])

    def test_progress_replaces_previous_success_but_not_stop_status(self):
        window = MainWindow(QSettings(str(self.base / 'ui.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        window.state_label.setText('Синхронизировано')
        event = {'path': 'file', 'bytes': 1, 'total': 10}
        window.on_progress(event)
        self.assertEqual(window.state_label.text(), 'Передача файла…')
        window.request_stop()
        window.on_progress(event)
        self.assertTrue(window.state_label.text().startswith('Остановка:'))
