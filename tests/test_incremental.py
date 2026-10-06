from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lansync.scanner import hash_file, scan_paths, scan_tree
from lansync.storage import Store


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "files"
        self.root.mkdir()
        self.store = Store(self.base / "index.sqlite3", self.root)
        self.addCleanup(self.store.close)
        (self.root / "a").write_text("a")
        (self.root / "b").write_text("b")
        self.store.update(scan_tree(self.root))

    def test_only_target_is_hashed_and_other_file_is_preserved(self):
        target = self.root / "a"
        target.write_text("changed")
        with patch("lansync.scanner.hash_file", wraps=hash_file) as hashing:
            manifest = self.store.update(scan_paths(self.root, [target, target]))
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(hashing.call_args.args[0], target)
        self.assertFalse(any(entry["deleted"] for entry in manifest["entries"]))
        self.assertEqual(manifest["entries"], list(scan_tree(self.root).entries.values()))

    def test_directory_move_matches_full_scan(self):
        old = self.root / "old"
        old.mkdir()
        (old / "child").write_text("data")
        self.store.update(scan_tree(self.root))
        new = self.root / "new"
        old.rename(new)
        manifest = self.store.update(scan_paths(self.root, [old, new, new / "child"]))
        live = {e["path"]: e for e in manifest["entries"] if not e["deleted"]}
        self.assertEqual(live, scan_tree(self.root).entries)
        self.assertTrue(next(e for e in manifest["entries"] if e["path"] == "old/child")["deleted"])

    def test_failed_path_stays_incomplete_until_successful_retry(self):
        with patch("lansync.scanner.hash_file", side_effect=PermissionError("denied")):
            first = self.store.update(scan_paths(self.root, [self.root / "a"]))
        self.assertFalse(first["complete"])
        second = self.store.update(scan_paths(self.root, [self.root / "b"]))
        self.assertFalse(second["complete"])
        third = self.store.update(scan_paths(self.root, [self.root / "a"]))
        self.assertTrue(third["complete"])

    def test_delete_does_not_delete_unrelated_files(self):
        (self.root / "a").unlink()
        manifest = self.store.update(scan_paths(self.root, [self.root / "a"]))
        entries = {e["path"]: e for e in manifest["entries"]}
        self.assertTrue(entries["a"]["deleted"])
        self.assertFalse(entries["b"]["deleted"])

    def test_outside_event_is_ignored(self):
        result = scan_paths(self.root, [self.base / "outside"])
        self.assertEqual(result.scopes, set())
        manifest = self.store.update(result)
        self.assertFalse(any(e["deleted"] for e in manifest["entries"]))
