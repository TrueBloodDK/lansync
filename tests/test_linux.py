import os
from pathlib import Path
import tempfile
import unittest

from lansync.scanner import scan_tree, scan_paths
from lansync.storage import Store


@unittest.skipUnless(os.name == 'posix', 'Requires a POSIX filesystem')
class PosixTests(unittest.TestCase):
    def test_actual_case_collisions_reserved_names_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'root'
            root.mkdir()
            for name in ('Report.txt', 'report.txt', 'CON.txt', 'bad:name'):
                (root / name).write_text(name)
            if len(list(root.iterdir())) != 4:
                self.skipTest('Requires a case-sensitive filesystem')
            (base / 'outside').write_text('not shared')
            (root / 'link').symlink_to(base / 'outside')
            store = Store(base / 'index.sqlite3', root)
            try:
                manifest = store.update(scan_tree(root))
                self.assertFalse(manifest['complete'])
                self.assertEqual({e['path'] for e in manifest['portability_errors']},
                                 {'Report.txt', 'report.txt', 'CON.txt', 'bad:name'})
                self.assertNotIn('link', {e['path'] for e in manifest['entries']})
                (root / 'report.txt').unlink()
                updated = store.update(scan_paths(root, [root / 'report.txt']))
                self.assertFalse(any('collision' in e['message'] for e in updated['portability_errors']))
            finally:
                store.close()

    def test_fifo_is_skipped_without_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.mkfifo(root / 'pipe')
            result = scan_tree(root)
            self.assertEqual(result.entries, {})
            self.assertEqual(result.skipped[0]['path'], 'pipe')
