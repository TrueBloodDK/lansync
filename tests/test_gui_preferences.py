from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lansync.config import load_project
from lansync.gui.preferences import save_preferences
from lansync.project_init import initialize
from lansync.sync import locked_sync_state


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = initialize(Path(self.temp.name) / 'project', 'client', 'https://localhost:8765')
        self.original = self.config.read_bytes() + b'\n# Keep this comment in backup\n'
        self.config.write_bytes(self.original)
        self.args = load_project(self.config).namespace()

    def test_save_preserves_binding_baseline_and_original_backup(self):
        self.args.state_dir.mkdir(exist_ok=True)
        baseline = self.args.state_dir / 'baseline.json'
        baseline.write_bytes(b'untouched baseline')
        backup = save_preferences(self.config, self.original, {'interval': 12, 'local_events': False})
        updated = load_project(self.config).namespace()
        self.assertEqual(updated.interval, 12)
        self.assertFalse(updated.local_events)
        for key in ('url', 'root', 'state_dir', 'token_file', 'ca_file'):
            self.assertEqual(getattr(updated, key), getattr(self.args, key))
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertEqual(baseline.read_bytes(), b'untouched baseline')
        self.assertFalse((self.args.state_dir / 'sync.lock').exists())

    def test_invalid_or_identity_changes_leave_original(self):
        for changes in ({'retry_max': 1}, {'request_timeout': -1}, {'local_events': 'yes'},
                        {'url': 'https://other:8765'}, {'interval': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                save_preferences(self.config, self.original, changes)
            self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(list(self.config.parent.glob('.lansync-config-*')), [])
        self.assertEqual(list(self.config.parent.glob('*.backup-*')), [])

    def test_busy_project_does_not_change_or_remove_lock(self):
        with locked_sync_state(self.args.root, self.args.state_dir):
            with self.assertRaises((ValueError, OSError)):
                save_preferences(self.config, self.original, {'interval': 12})
            self.assertTrue((self.args.state_dir / 'sync.lock').exists())
            self.assertEqual(self.config.read_bytes(), self.original)

    def test_external_config_change_is_preserved(self):
        external = self.original + b'# external edit\n'
        self.config.write_bytes(external)
        with self.assertRaises(ValueError):
            save_preferences(self.config, self.original, {'interval': 12})
        self.assertEqual(self.config.read_bytes(), external)

    def test_replace_failure_keeps_original_and_backup_and_releases_lock(self):
        with patch('lansync.gui.preferences.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                save_preferences(self.config, self.original, {'interval': 12})
        self.assertEqual(self.config.read_bytes(), self.original)
        backups = list(self.config.parent.glob('*.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertFalse((self.args.state_dir / 'sync.lock').exists())
        self.assertEqual(list(self.config.parent.glob('.lansync-config-*')), [])
