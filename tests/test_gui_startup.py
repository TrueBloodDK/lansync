import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from lansync.gui.autostart_dialog import AutostartDialog
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'project.toml'
        self.config.touch()
        self.settings = QSettings(str(self.config.with_suffix('.ini')), QSettings.IniFormat)

    def test_dialog_defaults_and_cancel_do_not_register(self):
        with patch('lansync.gui.autostart_dialog.Registration') as factory:
            factory.return_value.read.return_value = None
            dialog = AutostartDialog(self.config, self.settings)
            self.assertFalse(dialog.enabled.isChecked())
            self.assertFalse(dialog.sync.isChecked())
            self.assertFalse(dialog.minimized.isChecked())
            dialog.reject()
            factory.return_value.replace.assert_not_called()

    def test_dialog_explicit_enable_then_disable(self):
        with patch('lansync.gui.autostart_dialog.Registration') as factory:
            registration = factory.return_value
            registration.read.return_value = None
            registration.render.return_value = 'owned command'
            dialog = AutostartDialog(self.config, self.settings)
            dialog.enabled.setChecked(True)
            dialog.save()
            registration.replace.assert_called_once_with(None, 'owned command')
            self.assertEqual(self.settings.value('autostart/entry'), 'owned command')
            registration.read.return_value = 'owned command'
            dialog = AutostartDialog(self.config, self.settings)
            dialog.enabled.setChecked(False)
            dialog.save()
            registration.replace.assert_called_with('owned command', None)
            self.assertIsNone(self.settings.value('autostart/entry', None))

    def test_dialog_refuses_foreign_registration(self):
        with patch('lansync.gui.autostart_dialog.Registration') as factory:
            factory.return_value.read.return_value = 'foreign command'
            with self.assertRaises(ValueError):
                AutostartDialog(self.config, self.settings)
            factory.return_value.replace.assert_not_called()

    def run_main(self, opened, arguments):
        from lansync.gui import main
        window = Mock()
        window.open_project.return_value = opened
        with patch('sys.argv', ['lansync-gui', *arguments]), \
             patch('PySide6.QtWidgets.QApplication') as app, \
             patch('lansync.gui.window.MainWindow', return_value=window), \
             patch('lansync.gui.instance.DesktopInstance') as instance, \
             patch('lansync.gui.shutdown.Shutdown'), \
             patch('PySide6.QtCore.QTimer.singleShot', side_effect=lambda _, callback: callback()):
            instance.return_value.is_primary = True
            app.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
        return window

    def test_failed_explicit_project_never_starts_previous_or_hides_error(self):
        window = self.run_main(False, ['--config', 'missing.toml', '--start-sync', '--minimized'])
        window.start_action.assert_not_called()
        window.tray.hide_window.assert_not_called()
        window.show.assert_called_once()

    def test_successful_opt_in_startup_starts_and_minimizes(self):
        window = self.run_main(True, ['--config', str(self.config), '--start-sync', '--minimized'])
        window.start_action.assert_called_once_with('start')
        window.tray.hide_window.assert_called_once()

    def test_ordinary_launch_never_starts_sync(self):
        window = self.run_main(True, ['--config', str(self.config)])
        window.start_action.assert_not_called()
        window.tray.hide_window.assert_not_called()

    def test_secondary_launch_does_not_open_config_or_start_worker(self):
        from lansync.gui import main
        with patch('sys.argv', ['lansync-gui', '--config', 'missing.toml', '--start-sync']), \
             patch('PySide6.QtWidgets.QApplication'), \
             patch('lansync.gui.window.MainWindow') as window, \
             patch('lansync.gui.instance.DesktopInstance') as instance:
            instance.return_value.is_primary = False
            instance.return_value.notify.return_value = True
            self.assertEqual(main(), 0)
            window.assert_not_called()

    def test_secondary_with_unresponsive_primary_reports_failure(self):
        from lansync.gui import main
        with patch('sys.argv', ['lansync-gui']), \
             patch('PySide6.QtWidgets.QApplication'), \
             patch('PySide6.QtWidgets.QMessageBox.warning') as warning, \
             patch('lansync.gui.window.MainWindow') as window, \
             patch('lansync.gui.instance.DesktopInstance') as instance:
            instance.return_value.is_primary = False
            instance.return_value.notify.return_value = False
            self.assertEqual(main(), 1)
            warning.assert_called_once()
            window.assert_not_called()
