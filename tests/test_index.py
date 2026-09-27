import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lansync.cli import main
from lansync.manifest import export_manifest
from lansync.scanner import scan_tree
from lansync.storage import Store


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "files"
        self.root.mkdir()
        self.state = self.base / "state"

    def store(self):
        store = Store(self.state / "index.sqlite3", self.root)
        self.addCleanup(store.close)
        return store

    def test_nested_unicode_empty_and_large_files(self):
        (self.root / "пустой каталог").mkdir()
        data = b"abc" * 500000
        (self.root / "данные.bin").write_bytes(data)
        (self.root / "empty").write_bytes(b"")
        result = scan_tree(self.root)
        self.assertFalse(result.errors)
        self.assertEqual(result.entries["данные.bin"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result.entries["empty"]["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(result.entries["пустой каталог"]["kind"], "directory")

    def test_restart_modify_rename_delete_recreate(self):
        file = self.root / "one.txt"
        file.write_text("first")
        store = Store(self.state / "index.sqlite3", self.root)
        first = store.update(scan_tree(self.root))
        store.close()
        store = self.store()
        file.write_text("second")
        second = store.update(scan_tree(self.root))
        self.assertNotEqual(first["entries"][0]["sha256"], second["entries"][0]["sha256"])
        self.assertEqual(first["device_id"], second["device_id"])
        file.rename(self.root / "two.txt")
        moved = store.update(scan_tree(self.root))
        self.assertTrue(moved["entries"][0]["deleted"])
        self.assertFalse(moved["entries"][1]["deleted"])
        (self.root / "two.txt").unlink()
        self.assertTrue(all(e["deleted"] for e in store.update(scan_tree(self.root))["entries"]))
        file.write_text("restored")
        self.assertFalse(store.update(scan_tree(self.root))["entries"][0]["deleted"])

    def test_access_error_does_not_delete_previous_entries(self):
        (self.root / "saved").write_text("important")
        store = self.store()
        store.update(scan_tree(self.root))
        with patch("lansync.scanner.os.scandir", side_effect=PermissionError("denied")):
            manifest = store.update(scan_tree(self.root))
        self.assertFalse(manifest["complete"])
        self.assertFalse(manifest["entries"][0]["deleted"])
        self.assertTrue(manifest["errors"])

    def test_hash_error_keeps_previous_hash_and_reports_incomplete(self):
        (self.root / "file").write_text("before")
        store = self.store()
        initial = store.update(scan_tree(self.root))
        with patch("lansync.scanner.hash_file", side_effect=OSError("changed during read")):
            current = store.update(scan_tree(self.root))
        self.assertFalse(current["complete"])
        self.assertEqual(initial["entries"], current["entries"])

    def test_manifest_export_and_cli(self):
        (self.root / "file").write_text("hello")
        self.assertEqual(main(["scan", str(self.root), "--state-dir", str(self.state)]), 0)
        output = json.loads((self.state / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(output["entries"][0]["size"], 5)
        self.assertTrue(output["complete"])

    def test_reject_internal_state_and_database_export(self):
        self.assertEqual(main(["scan", str(self.root), "--state-dir", str(self.root / "state")]), 1)
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(main(["scan", str(self.root), "--state-dir", str(self.state),
                               "--manifest", str(self.state / "index.sqlite3")]), 1)

    def test_root_binding(self):
        self.store()
        with self.assertRaises(ValueError):
            Store(self.state / "index.sqlite3", self.base)

    def test_failed_export_preserves_old_manifest(self):
        destination = self.base / "manifest.json"
        destination.write_text("previous", encoding="utf-8")
        with patch("lansync.manifest.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                export_manifest({"entries": []}, destination)
        self.assertEqual(destination.read_text(), "previous")
        self.assertFalse(list(self.base.glob(".lansync-*.tmp")))

    def test_symlink_is_not_followed(self):
        target = self.base / "outside.txt"
        target.write_text("outside")
        try:
            (self.root / "link").symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation unavailable on this host")
        result = scan_tree(self.root)
        self.assertEqual(result.entries, {})
        self.assertEqual(result.skipped[0]["path"], "link")


if __name__ == "__main__":
    unittest.main()
