import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication
    from lansync.gui.instance import DesktopInstance
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class InstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def instance(self):
        instance = DesktopInstance(self.directory)
        self.addCleanup(instance.close)
        return instance

    def test_second_instance_activates_first_without_releasing_its_lock(self):
        first = self.instance()
        activations = []
        first.activated.connect(lambda: activations.append(True))
        second = self.instance()
        self.assertTrue(first.is_primary)
        self.assertFalse(second.is_primary)
        self.assertTrue(second.notify())
        deadline = time.monotonic() + 3
        while not activations and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(activations, [True])
        second.close()
        third = self.instance()
        self.assertFalse(third.is_primary)
        first.close()
        self.assertTrue(self.instance().is_primary)

    def test_separate_process_cannot_become_second_owner(self):
        first = self.instance()
        code = ('import sys; from PySide6.QtCore import QCoreApplication; '
                'from lansync.gui.instance import DesktopInstance; '
                'app=QCoreApplication([]); instance=DesktopInstance(sys.argv[1]); '
                'assert not instance.is_primary; assert instance.notify(); instance.close()')
        subprocess.run([sys.executable, '-c', code, str(self.directory)], timeout=15, check=True)
        self.assertTrue(first.is_primary)

    def test_crashed_gui_releases_only_gui_ownership_not_sync_lock(self):
        marker = self.directory / 'sync.lock'
        marker.write_text('must remain')
        code = ('import sys,os; from PySide6.QtCore import QCoreApplication; '
                'from lansync.gui.instance import DesktopInstance; '
                'app=QCoreApplication([]); instance=DesktopInstance(sys.argv[1]); '
                'assert instance.is_primary; os._exit(0)')
        subprocess.run([sys.executable, '-c', code, str(self.directory)], timeout=15, check=True)
        self.assertTrue(self.instance().is_primary)
        self.assertEqual(marker.read_text(), 'must remain')

    def test_listener_failure_releases_gui_lock(self):
        with patch('lansync.gui.instance.QLocalServer') as server:
            server.return_value.listen.return_value = False
            server.return_value.errorString.return_value = 'listen failure'
            with self.assertRaises(OSError):
                self.instance()
        self.assertTrue(self.instance().is_primary)
