import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from lansync.recovery import list_copies, restore_copy


class RecoveryTests(unittest.TestCase):
    def test_restore_verified_copy_and_preserve_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'
            recovery = state / 'recovery'
            recovery.mkdir(parents=True)
            identifier = 'a' * 32
            backup = recovery / (identifier + '.bak')
            backup.write_bytes(b'previous version')
            metadata = {'path': 'notes.txt', 'previous': {'kind': 'file', 'size': 16, 'sha256': hashlib.sha256(backup.read_bytes()).hexdigest()}}
            (recovery / (identifier + '.json')).write_text(json.dumps(metadata))
            self.assertTrue(list_copies(state)[0]['available'])
            destination = root / 'restored.txt'
            restore_copy(state, identifier, destination)
            self.assertEqual(destination.read_bytes(), b'previous version')
            self.assertTrue(backup.exists())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                restore_copy(state, identifier, destination)
            with self.assertRaisesRegex(ValueError, 'outside'):
                restore_copy(state, identifier, state / 'new.txt')
            backup.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                restore_copy(state, identifier, root / 'bad.txt')
            self.assertFalse((root / 'bad.txt').exists())
            self.assertFalse(list(root.glob('.lansync-restore-*')))
            with self.assertRaisesRegex(ValueError, 'identifier'):
                restore_copy(state, '../escape', root / 'bad.txt')

    def test_empty_and_corrupt_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertEqual(list_copies(state), [])
            (state / 'recovery').mkdir()
            (state / 'recovery' / ('b' * 32 + '.json')).write_text('{}')
            self.assertIn('error', list_copies(state)[0])
