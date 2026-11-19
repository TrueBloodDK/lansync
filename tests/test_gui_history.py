import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from lansync.gui.controller import Controller
    from lansync.gui.window import MainWindow
    from lansync.gui.history import ConflictView, RecoveryView
except ImportError:
    QApplication = None
from lansync.project_init import initialize


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class HistoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = initialize(self.base / 'project', 'client', 'https://localhost:8765')
        self.state = self.config.parent / 'state'
        self.recovery = self.state / 'recovery'
        self.recovery.mkdir(parents=True)
        self.identifier = 'a' * 32
        self.content = b'old content'
        self.backup = self.recovery / (self.identifier + '.bak')
        self.backup.write_bytes(self.content)
        (self.recovery / (self.identifier + '.json')).write_text(json.dumps({
            'path': 'notes.txt', 'previous': {'kind': 'file', 'size': len(self.content),
                                           'sha256': hashlib.sha256(self.content).hexdigest()}}))
        self.controller = Controller()
        self.results, self.errors = [], []
        self.controller.result.connect(self.results.append)
        self.controller.error.connect(self.errors.append)
        self.addCleanup(self.finish)

    def finish(self):
        self.controller.stop()
        end = time.monotonic() + 5
        while self.controller.busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.controller.busy)

    def run_action(self, action, **parameters):
        self.controller.start(self.config, action, **parameters)
        # Wait without requesting cancellation.
        end = time.monotonic() + 5
        while self.controller.busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.controller.busy)

    def test_recovery_is_offline_and_never_overwrites(self):
        with patch('lansync.gui.controller.read_token', side_effect=AssertionError('Must remain offline')):
            self.run_action('recovery-list')
            self.assertEqual(self.results[-1]['entries'][0]['id'], self.identifier)
            self.run_action('recovery-verify')
            self.assertEqual(self.results[-1]['verified'], 1)
            output = self.base / 'export.txt'
            self.run_action('recovery-export', id=self.identifier, output=output)
            self.assertEqual(output.read_bytes(), self.content)
            self.assertFalse(self.errors)
            self.run_action('recovery-export', id=self.identifier, output=output)
            self.assertTrue(self.errors)
            self.assertEqual(output.read_bytes(), self.content)
            self.assertEqual(self.backup.read_bytes(), self.content)

    def test_export_rejects_shared_root_lock_and_corrupt_backup(self):
        target = self.config.parent / 'shared/export.txt'
        self.run_action('recovery-export', id=self.identifier, output=target)
        self.assertFalse(target.exists())
        self.assertIn('вне', self.errors[-1])
        target = self.base / 'export.txt'
        (self.state / 'sync.lock').touch()
        self.run_action('recovery-export', id=self.identifier, output=target)
        self.assertIn('sync.lock', self.errors[-1])
        (self.state / 'sync.lock').unlink()
        self.backup.write_bytes(b'corrupted')
        self.run_action('recovery-verify')
        self.assertEqual(self.results[-1]['problems'], 1)
        self.run_action('recovery-export', id=self.identifier, output=target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.base.glob('.lansync-restore-*')))

    def test_resolve_routes_choice_and_copy_path_to_core(self):
        from lansync.gui.controller import Task
        (self.config.parent / 'credentials/token.txt').write_text('a' * 40)
        with patch('lansync.gui.controller.ProcessTask.run', Task.run), \
             patch('lansync.gui.controller.inspect_project', return_value={'checks': []}), \
             patch('lansync.gui.controller.make_client', return_value=nullcontext('client')), \
             patch('lansync.gui.controller.resolve_conflict', return_value={'conflicts': []}) as resolve:
            self.run_action('resolve', path='notes.txt', choose='both', copy_path='notes.local.txt')
        self.assertFalse(self.errors)
        self.assertEqual(resolve.call_args.args[3:], ('notes.txt', 'both'))
        self.assertEqual(resolve.call_args.kwargs, {'copy_path': 'notes.local.txt'})

    def test_saved_conflicts_do_not_require_credentials(self):
        report = {'conflicts': ['notes.txt'], 'uploaded': 0, 'downloaded': 0,
                  'deleted_local': 0, 'deleted_remote': 0, 'conflict_versions': {}}
        (self.state / 'last-sync.json').write_text(json.dumps(report))
        self.run_action('conflicts')
        self.assertEqual(self.results[-1]['conflicts'], ['notes.txt'])
        self.assertFalse(self.errors)

    def test_selection_and_busy_guards(self):
        conflicts = ConflictView()
        recovery = RecoveryView()
        self.addCleanup(conflicts.deleteLater)
        self.addCleanup(recovery.deleteLater)
        conflicts.set_available(True)
        conflicts.set_report({'conflicts': ['a'], 'conflict_versions': {'a': {'local': {'kind': 'file'}, 'remote': None}}})
        conflicts.table.selectRow(0)
        self.assertTrue(conflicts.choices['local'].isEnabled())
        self.assertFalse(conflicts.choices['both'].isEnabled())
        conflicts.set_available(False)
        self.assertFalse(conflicts.choices['local'].isEnabled())
        recovery.set_available(True)
        recovery.set_report({'entries': [{'id': self.identifier, 'status': 'corrupt', 'error': 'hash mismatch'}]})
        recovery.table.selectRow(0)
        self.assertFalse(recovery.export.isEnabled())

    def test_cancel_resolution_does_not_start_operation(self):
        window = MainWindow(QSettings(str(self.base / 'ui.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        window.open_project(self.config)
        window.conflict_view.set_report({'conflicts': ['notes.txt'], 'conflict_versions': {
            'notes.txt': {'local': {'kind': 'file'}, 'remote': {'kind': 'file'}}}})
        window.conflict_view.table.selectRow(0)
        with patch.object(window, 'confirm', return_value=False), patch.object(window.controller, 'start') as start:
            window.resolve_selected('remote')
        start.assert_not_called()
        window.open_project(self.config)
        self.assertEqual(window.conflict_view.table.rowCount(), 0)
