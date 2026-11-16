import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

try:
    from PySide6.QtWidgets import QApplication, QWidget
    from lansync.gui.tray import Tray
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_absent_tray_never_hides_window(self):
        window = Mock()
        with patch('lansync.gui.tray.QSystemTrayIcon') as icon:
            icon.isSystemTrayAvailable.return_value = False
            tray = Tray(window)
            self.assertFalse(tray.hide_window())
            window.hide.assert_not_called()

    def test_tray_controls_stop_and_recovers_window_if_tray_disappears(self):
        window = QWidget()
        window.request_stop = Mock()
        self.addCleanup(window.close)
        with patch('lansync.gui.tray.QSystemTrayIcon') as icon:
            icon.isSystemTrayAvailable.return_value = True
            tray = Tray(window)
            window.show()
            self.assertTrue(tray.hide_window())
            self.assertFalse(window.isVisible())
            tray.update(True, False, 'Работает')
            self.assertTrue(tray.stop_action.isEnabled())
            tray.stop_action.trigger()
            window.request_stop.assert_called_once()
            tray.update(True, True, 'Остановка')
            self.assertFalse(tray.stop_action.isEnabled())
            icon.isSystemTrayAvailable.return_value = False
            tray.update(False, False, 'Готово')
            self.assertTrue(window.isVisible())
            tray.close()
            icon.return_value.hide.assert_called_once()

    def test_exit_shows_window_before_delegating_close(self):
        window = Mock()
        with patch('lansync.gui.tray.QSystemTrayIcon') as icon:
            icon.isSystemTrayAvailable.return_value = False
            tray = Tray(window)
            tray.exit()
            self.assertEqual([call[0] for call in window.method_calls],
                             ['showNormal', 'raise_', 'activateWindow', 'close'])
