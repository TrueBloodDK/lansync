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

    def test_verify_reports_all_problems_without_modification(self):
        from lansync.recovery import verify_copies
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp); recovery=state/'recovery'; recovery.mkdir()
            for index, content in enumerate((b'valid', b'broken', None)):
                identifier=f'{index:032x}'
                metadata={'path':f'file{index}', 'previous':{'kind':'file','size':5,'sha256':hashlib.sha256(b'valid').hexdigest()}}
                (recovery/(identifier+'.json')).write_text(json.dumps(metadata))
                if content is not None:
                    (recovery/(identifier+'.bak')).write_bytes(content)
            (recovery/('d'*32+'.bak')).write_bytes(b'orphan')
            (recovery/('e'*32+'.json')).write_text('invalid json')
            before={p.name:p.read_bytes() for p in recovery.iterdir()}
            result=verify_copies(state)
            self.assertEqual(result['verified'],1)
            self.assertEqual(result['problems'],4)
            self.assertEqual({e['status'] for e in result['entries']},{'verified','corrupt','missing','orphan','error'})
            self.assertEqual(before,{p.name:p.read_bytes() for p in recovery.iterdir()})

    def test_verify_empty_and_cli_exit_code(self):
        import contextlib
        import io
        from lansync.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['recovery','verify','--state-dir',tmp,'--json']),0)
            self.assertEqual(json.loads(out.getvalue())['total'],0)
            recovery=Path(tmp)/'recovery'; recovery.mkdir()
            (recovery/('a'*32+'.bak')).write_bytes(b'orphan')
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['recovery','verify','--state-dir',tmp,'--json']),2)
            self.assertEqual(json.loads(out.getvalue())['problems'],1)
