import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lansync.credentials import write_bundle
from lansync.durability import make_directory
from lansync.manifest import export_manifest
from lansync.recovery import restore_copy


class DurabilityTests(unittest.TestCase):
    def test_failed_restore_flush_does_not_publish_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            recovery = base / 'state/recovery'
            recovery.mkdir(parents=True)
            identifier = 'a' * 32
            (recovery / (identifier + '.bak')).write_bytes(b'original')
            (recovery / (identifier + '.json')).write_text(json.dumps({
                'path': 'file', 'previous': {'kind': 'file', 'size': 8,
                'sha256': hashlib.sha256(b'original').hexdigest()}}))
            with patch('lansync.recovery.os.fsync', side_effect=OSError(errno.EIO, 'disk error')):
                with self.assertRaises(OSError):
                    restore_copy(base / 'state', identifier, base / 'restored')
            self.assertFalse((base / 'restored').exists())
            self.assertEqual((recovery / (identifier + '.bak')).read_bytes(), b'original')
            self.assertFalse(list(base.glob('.lansync-restore-*')))

    def test_unavailable_filesystem_root_does_not_loop(self):
        with patch.object(Path, 'exists', return_value=False):
            with self.assertRaises(FileNotFoundError):
                make_directory(Path.cwd() / 'missing-root')

    def test_credential_flush_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            with patch('lansync.credentials.os.fsync', side_effect=OSError(errno.EIO, 'disk error')):
                with self.assertRaises(OSError):
                    write_bundle(destination, {'token.txt': b'test'})
            with self.assertRaises(ValueError):
                write_bundle(destination, {'token.txt': b'replacement'})

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory fsync')
    def test_manifest_directory_flush_failure_is_visible_and_json_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'manifest.json'
            destination.write_text('{"old": true}')
            with patch('lansync.manifest.sync_directory', side_effect=OSError(errno.EIO, 'disk error')):
                with self.assertRaises(OSError):
                    export_manifest({'new': True}, destination)
            self.assertEqual(json.loads(destination.read_text()), {'new': True})
            self.assertEqual(list(Path(tmp).iterdir()), [destination])
