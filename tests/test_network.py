import hashlib
import json
from pathlib import Path
import socket
import tempfile
from threading import Thread
import time
import unittest

from fastapi.testclient import TestClient
import httpx
import trustme
import uvicorn

from lansync.network import create_app, make_client, pull, validate_manifest

TOKEN = 'test-token-' + 'a' * 40


class NetworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'source'
        self.target = self.base / 'target'
        self.source.mkdir()
        self.target.mkdir()
        (self.source / 'empty-dir').mkdir()
        (self.source / 'данные.txt').write_bytes(b'abc' * 500000)
        (self.source / 'empty').write_bytes(b'')
        self.app = create_app(self.source, self.base / 'state/index.sqlite3', TOKEN)
        self.client = TestClient(self.app, headers={'Authorization': 'Bearer ' + TOKEN})
        self.addCleanup(self.client.close)

    def test_transfer_and_repeat(self):
        self.assertEqual(pull(self.client, self.target), {'downloaded': 2, 'unchanged': 0})
        self.assertEqual((self.source / 'данные.txt').read_bytes(), (self.target / 'данные.txt').read_bytes())
        self.assertTrue((self.target / 'empty-dir').is_dir())
        self.assertEqual(pull(self.client, self.target), {'downloaded': 0, 'unchanged': 2})
        self.assertFalse(list(self.target.rglob('.lansync-*.tmp')))

    def test_authentication_and_no_public_docs(self):
        for endpoint in ('/v1/manifest', '/v1/file?path=empty&sha256=x'):
            response = self.client.get(endpoint, headers={'Authorization': 'Bearer wrong'})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/docs').status_code, 404)

    def test_path_traversal_and_excluded_files(self):
        self.client.get('/v1/manifest')
        for path in ('../outside', '/absolute', 'C:/secret', 'a\\b', 'a/../empty'):
            self.assertEqual(self.client.get('/v1/file', params={'path': path, 'sha256': 'x'}).status_code, 400)
        app = create_app(self.source, self.base / 'excluded/index.sqlite3', TOKEN, ['empty'])
        with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
            client.get('/v1/manifest')
            self.assertEqual(client.get('/v1/file', params={'path': 'empty', 'sha256': hashlib.sha256(b'').hexdigest()}).status_code, 404)

    def test_changed_source_rejected(self):
        manifest = self.client.get('/v1/manifest').json()
        entry = next(e for e in manifest['entries'] if e['path'] == 'empty')
        (self.source / 'empty').write_bytes(b'changed')
        self.assertEqual(self.client.get('/v1/file', params={'path': 'empty', 'sha256': entry['sha256']}).status_code, 409)

    def test_local_conflict_keeps_existing_content(self):
        (self.target / 'empty').write_bytes(b'local edits')
        with self.assertRaisesRegex(ValueError, 'Local conflict'):
            pull(self.client, self.target)
        self.assertEqual((self.target / 'empty').read_bytes(), b'local edits')
        self.assertFalse((self.target / 'данные.txt').exists())

    def test_invalid_manifest_is_rejected_before_writes(self):
        manifest = self.client.get('/v1/manifest').json()
        manifest['entries'][0]['path'] = '../escape'
        with self.assertRaises(ValueError):
            validate_manifest(manifest)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_manifest_rejects_duplicates_and_file_parent(self):
        def manifest(entries):
            return {'schema_version': 2, 'complete': True, 'entries': entries}
        file = {'path': 'parent', 'kind': 'file', 'deleted': False, 'size': 0,
                'sha256': hashlib.sha256(b'').hexdigest()}
        for entries in ([file, dict(file)], [file, {**file, 'path': 'parent/child'}],
                        [file, {**file, 'path': 'PARENT'}], [{**file, 'size': -1}]):
            with self.assertRaises(ValueError):
                validate_manifest(manifest(entries))

    def test_destination_symlink_is_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        try:
            (self.target / 'empty-dir').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            pull(self.client, self.target)
        self.assertEqual(list(outside.iterdir()), [])

    def test_bad_hash_and_interrupted_stream_leave_no_file(self):
        entry = {'path': 'file', 'kind': 'file', 'deleted': False, 'size': 3,
                 'sha256': hashlib.sha256(b'abc').hexdigest()}
        manifest = {'schema_version': 2, 'complete': True, 'entries': [entry]}

        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                yield b'a'
                raise httpx.ReadError('connection lost')

        for stream in (None, Broken()):
            def respond(request):
                if request.url.path == '/v1/manifest':
                    return httpx.Response(200, json=manifest)
                return httpx.Response(200, content=b'bad') if stream is None else httpx.Response(200, stream=stream)
            with httpx.Client(base_url='https://test', transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises((ValueError, httpx.ReadError)):
                    pull(client, self.target)
            self.assertEqual(list(self.target.iterdir()), [])

    def test_file_created_during_transfer_is_not_overwritten(self):
        entry = {'path': 'file', 'kind': 'file', 'deleted': False, 'size': 3,
                 'sha256': hashlib.sha256(b'abc').hexdigest()}

        def respond(request):
            if request.url.path == '/v1/manifest':
                return httpx.Response(200, json={'schema_version': 2, 'complete': True, 'entries': [entry]})
            (self.target / 'file').write_bytes(b'user edit')
            return httpx.Response(200, content=b'abc')
        with httpx.Client(base_url='https://test', transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(FileExistsError):
                pull(client, self.target)
        self.assertEqual((self.target / 'file').read_bytes(), b'user edit')
        self.assertFalse(list(self.target.glob('.lansync-*.tmp')))

    def test_http_requires_explicit_loopback(self):
        for url, allow in [('http://192.168.1.5', True), ('http://127.0.0.1', False), ('https://user:password@example.com', False)]:
            with self.assertRaises(ValueError):
                make_client(url, TOKEN, allow_http=allow)

    def test_real_https_transfer_and_certificate_verification(self):
        ca = trustme.CA()
        certificate = ca.issue_cert('127.0.0.1')
        ca_path, cert_path, key_path = (self.base / name for name in ('ca.pem', 'cert.pem', 'key.pem'))
        ca.cert_pem.write_to_path(ca_path)
        certificate.cert_chain_pems[0].write_to_path(cert_path)
        certificate.private_key_pem.write_to_path(key_path)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        config = uvicorn.Config(self.app, host='127.0.0.1', port=port, ssl_certfile=str(cert_path),
                                ssl_keyfile=str(key_path), log_level='error', lifespan='off')
        server = uvicorn.Server(config)
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            url = f'https://127.0.0.1:{port}'
            with make_client(url, TOKEN) as client:
                with self.assertRaises(httpx.ConnectError):
                    client.get('/v1/manifest')
            with make_client(url, TOKEN, ca_path) as client:
                self.assertEqual(pull(client, self.target)['downloaded'], 2)
        finally:
            server.should_exit = True
            thread.join(5)
            listener.close()
        self.assertFalse(thread.is_alive())
