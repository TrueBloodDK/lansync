import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from lansync.network import create_app
from lansync.sync import synchronize


class SyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.server = self.base / 'server'
        self.local = self.base / 'local'
        self.server.mkdir()
        self.local.mkdir()
        self.database = self.base / 'server-state/index.sqlite3'
        self.state = self.base / 'client-state'
        self.token = 'a' * 40
        self.client = self.new_client()

    def new_client(self, writable=True):
        client = TestClient(create_app(self.server, self.database, self.token, allow_write=writable),
                            headers={'Authorization': 'Bearer ' + self.token})
        self.addCleanup(client.close)
        return client

    def sync(self):
        return synchronize(self.client, self.local, self.state)

    def manifest(self):
        response = self.client.get('/v1/manifest')
        self.assertEqual(response.status_code, 200)
        return {e['path']: e for e in response.json()['entries']}

    def seed(self):
        (self.server / 'file').write_bytes(b'base')
        self.assertFalse(self.sync()['conflicts'])

    def test_bidirectional_create_update_and_repeat(self):
        (self.server / 'remote').write_bytes(b'server')
        (self.local / 'local').write_bytes(b'client')
        report = self.sync()
        self.assertEqual(report['uploaded'], 1)
        self.assertEqual(report['downloaded'], 1)
        (self.local / 'local').write_bytes(b'new client')
        (self.server / 'remote').write_bytes(b'new server')
        self.assertFalse(self.sync()['conflicts'])
        self.assertEqual((self.local / 'remote').read_bytes(), b'new server')
        self.assertEqual((self.server / 'local').read_bytes(), b'new client')
        report = self.sync()
        self.assertEqual(report['uploaded'] + report['downloaded'], 0)
        self.assertTrue(list((self.state / 'recovery').glob('*.bak')))
        self.assertTrue(list((self.database.parent / 'recovery').glob('*.bak')))

    def test_two_clients_conflict_preserves_originals(self):
        self.seed()
        second = self.base / 'second'
        second.mkdir()
        second_state = self.base / 'second-state'
        synchronize(self.client, second, second_state)
        (self.local / 'file').write_bytes(b'first edits')
        self.sync()
        (second / 'file').write_bytes(b'second edits')
        report = synchronize(self.client, second, second_state)
        self.assertEqual(report['conflicts'], ['file'])
        self.assertEqual((second / 'file').read_bytes(), b'second edits')
        self.assertEqual((self.server / 'file').read_bytes(), b'first edits')
        # A subsequent run must still report the unresolved conflict.
        self.assertEqual(synchronize(self.client, second, second_state)['conflicts'], ['file'])

    def test_deletions_both_directions_and_recreation(self):
        self.seed()
        (self.local / 'file').unlink()
        self.assertEqual(self.sync()['deleted_remote'], 1)
        self.assertFalse((self.server / 'file').exists())
        (self.local / 'file').write_bytes(b'recreated')
        self.assertEqual(self.sync()['uploaded'], 1)
        (self.server / 'file').unlink()
        self.assertEqual(self.sync()['deleted_local'], 1)
        self.assertFalse((self.local / 'file').exists())
        self.assertTrue(self.manifest()['file']['deleted'])

    def test_delete_modify_conflict(self):
        self.seed()
        (self.local / 'file').unlink()
        (self.server / 'file').write_bytes(b'remote change')
        self.assertEqual(self.sync()['conflicts'], ['file'])
        self.assertEqual((self.server / 'file').read_bytes(), b'remote change')

    def test_revision_persists_and_does_not_follow_scan_generation(self):
        self.seed()
        revision = self.manifest()['file']['revision']
        self.assertEqual(self.manifest()['file']['revision'], revision)
        with self.new_client() as client:
            self.assertEqual(client.get('/v1/manifest').json()['entries'][0]['revision'], revision)
        (self.server / 'file').write_bytes(b'external change')
        self.assertGreater(self.manifest()['file']['revision'], revision)

    def test_stale_revision_rejected_after_external_edit(self):
        self.seed()
        revision = self.manifest()['file']['revision']
        (self.server / 'file').write_bytes(b'external change')
        response = self.client.put('/v1/change', params={'path': 'file', 'base_revision': revision,
            'kind': 'file', 'sha256': hashlib.sha256(b'upload').hexdigest(), 'size': 6}, content=b'upload')
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.server / 'file').read_bytes(), b'external change')

    def test_directories_and_nested_deletions(self):
        (self.local / 'nested/empty').mkdir(parents=True)
        (self.local / 'nested/file').write_bytes(b'data')
        self.assertFalse(self.sync()['conflicts'])
        self.assertTrue((self.server / 'nested/empty').is_dir())
        (self.local / 'nested/file').unlink()
        (self.local / 'nested/empty').rmdir()
        (self.local / 'nested').rmdir()
        self.assertFalse(self.sync()['conflicts'])
        self.assertFalse((self.server / 'nested').exists())

    def test_readonly_and_bad_upload(self):
        with self.new_client(writable=False) as client:
            self.assertEqual(client.put('/v1/change', params={'path': 'x', 'base_revision': 0, 'kind': 'directory'}).status_code, 403)
        response = self.client.put('/v1/change', params={'path': 'x', 'base_revision': 0, 'kind': 'file',
            'sha256': hashlib.sha256(b'good').hexdigest(), 'size': 4}, content=b'evil')
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.server / 'x').exists())
        self.assertFalse(list(self.database.parent.glob('upload-*')))

    def test_baseline_binding_and_lock(self):
        self.seed()
        (self.state / 'sync.lock').write_text('')
        with self.assertRaises(FileExistsError):
            self.sync()
        (self.state / 'sync.lock').unlink()
        other = self.base / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            synchronize(self.client, other, self.state)

    def test_unknown_old_copy_does_not_resurrect_tombstone(self):
        self.seed()
        (self.server / 'file').unlink()
        self.manifest()
        other = self.base / 'other'
        other.mkdir()
        (other / 'file').write_bytes(b'base')
        result = synchronize(self.client, other, self.base / 'other-state')
        self.assertEqual(result['conflicts'], ['file'])
        self.assertFalse((self.server / 'file').exists())

    def test_cross_device_case_collision_detected_before_writes(self):
        (self.server / 'Report').write_bytes(b'server')
        (self.local / 'report').write_bytes(b'local')
        result = self.sync()
        self.assertEqual(set(result['conflicts']), {'Report', 'report'})
        self.assertEqual((self.local / 'report').read_bytes(), b'local')
        self.assertEqual((self.server / 'Report').read_bytes(), b'server')

    def test_restart_after_lost_ack_converges_without_duplicate_upload(self):
        self.seed()
        old_baseline = (self.state / 'baseline.json').read_bytes()
        (self.local / 'file').write_bytes(b'new')
        self.sync()
        # Simulate a lost response / crash before the baseline was persisted.
        (self.state / 'baseline.json').write_bytes(old_baseline)
        result = self.sync()
        self.assertFalse(result['conflicts'])
        self.assertEqual(result['uploaded'], 0)

    def test_remote_change_during_download_does_not_overwrite_local_edit(self):
        self.seed()
        (self.server / 'file').write_bytes(b'remote')
        original = self.client.stream
        from contextlib import contextmanager
        from unittest.mock import patch

        @contextmanager
        def stream(method, url, **kwargs):
            with original(method, url, **kwargs) as response:
                if url == '/v1/file':
                    (self.local / 'file').write_bytes(b'concurrent local')
                yield response
        with patch.object(self.client, 'stream', stream):
            result = self.sync()
        self.assertEqual(result['conflicts'], ['file'])
        self.assertEqual((self.local / 'file').read_bytes(), b'concurrent local')
