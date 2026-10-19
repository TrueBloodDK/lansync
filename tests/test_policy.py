from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lansync.policy import excluded, portability_errors, validate_patterns
from lansync.scanner import scan_tree, scan_paths, ScanResult, hash_file
from lansync.storage import Store
from lansync.cli import main


def entry(path):
    return {"path": path, "kind": "file", "size": 0, "mtime_ns": 0,
            "sha256": "test", "deleted": False}


class NameTests(unittest.TestCase):
    def test_windows_invalid_names_on_every_host(self):
        for name in ('CON.txt', 'nul', 'COM¹.log', 'a:b', 'file.', 'file ', 'a\\b', 'a\x01b', 'x' * 256):
            with self.subTest(name=name):
                self.assertTrue(portability_errors([entry(name)]))
        self.assertFalse(portability_errors([entry('документы/отчёт.txt'), entry('.hidden'), entry('COM10.txt')]))

    def test_collisions_include_parents_and_unicode(self):
        errors = portability_errors([entry('Docs/a'), entry('docs/b'), entry('é.txt'), entry('e\u0301.txt')])
        self.assertEqual({e['path'] for e in errors}, {'Docs', 'docs', 'é.txt', 'e\u0301.txt'})

    def test_patterns_are_case_sensitive_and_cover_descendants(self):
        self.assertTrue(excluded('nested/cache/item', ['cache']))
        self.assertTrue(excluded('build/sub/item', ['build/*']))
        self.assertTrue(excluded('a/file.tmp', ['*.tmp']))
        self.assertFalse(excluded('a/file.TMP', ['*.tmp']))
        for rule in ('', '../a', '/a', 'a/', 'a\\b', '!a'):
            with self.assertRaises(ValueError):
                validate_patterns([rule])


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.store = Store(self.base / 'state/index.sqlite3', self.root)
        self.addCleanup(self.store.close)

    def test_exclusion_never_hashes_and_never_tombstones_old_file(self):
        path = self.root / 'secret.tmp'
        path.write_text('old')
        self.store.update(scan_tree(self.root))
        with patch('lansync.scanner.hash_file', wraps=hash_file) as hashing:
            manifest = self.store.update(scan_tree(self.root, ['*.tmp']))
        hashing.assert_not_called()
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['entries'], [])
        path.unlink()
        manifest = self.store.update(scan_paths(self.root, [path], ['*.tmp']))
        self.assertEqual(manifest['entries'], [])
        import json
        stored = json.loads(self.store.connection.execute('SELECT data FROM entries').fetchone()[0])
        self.assertFalse(stored['deleted'])
        restored = self.store.update(scan_tree(self.root))
        self.assertTrue(restored['entries'][0]['deleted'])

    def test_nested_rules_match_identically_in_incremental_scan(self):
        folder = self.root / 'cache'
        folder.mkdir()
        (folder / 'keep').write_text('yes')
        (folder / 'private').write_text('no')
        rules = ['cache/private']
        full = self.store.update(scan_tree(self.root, rules))
        partial = self.store.update(scan_paths(self.root, [folder], rules))
        # Windows directory metadata can settle after child creation.
        comparable = lambda items: [{k: v for k, v in e.items() if k != 'mtime_ns'} for e in items]
        self.assertEqual(comparable(full['entries']), comparable(partial['entries']))
        self.assertEqual([e['path'] for e in partial['entries']], ['cache', 'cache/keep'])
        self.assertTrue(partial['complete'])

    def test_reinclude_reads_current_content(self):
        file = self.root / 'test.tmp'
        file.write_text('old')
        first = self.store.update(scan_tree(self.root))
        self.store.update(scan_tree(self.root, ['*.tmp']))
        file.write_text('new')
        latest = self.store.update(scan_tree(self.root))
        self.assertNotEqual(first['entries'][0]['sha256'], latest['entries'][0]['sha256'])

    def test_partial_policy_change_rejected(self):
        self.store.update(scan_tree(self.root))
        with self.assertRaises(ValueError):
            self.store.update(scan_paths(self.root, [], ['*.tmp']))

    def test_collision_with_old_index_entry_and_resolution(self):
        # Synthetic index entries let Windows test case-sensitive Linux names.
        self.store.update(ScanResult(entries={'A': entry('A')}))
        conflict = self.store.update(ScanResult(entries={'a': entry('a')}, scopes={'a'}))
        self.assertFalse(conflict['complete'])
        self.assertEqual(len(conflict['portability_errors']), 2)
        fixed = self.store.update(ScanResult(scopes={'A'}))
        self.assertTrue(fixed['complete'])

    def test_cli_repeatable_exclusions(self):
        (self.root / 'file.tmp').write_text('temp')
        (self.root / 'cache').mkdir()
        self.assertEqual(main(['scan', str(self.root), '--state-dir', str(self.base / 'cli-state'),
                               '--exclude', '*.tmp', '--exclude', 'cache']), 0)
