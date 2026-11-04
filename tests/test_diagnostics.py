import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from lansync.network import create_app
from lansync.diagnostics import diagnose, inspect_server

class DiagnosticTests(unittest.TestCase):
    def test_real_manifest_read_only_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); root=base/'shared'; root.mkdir()
            (root/'file').write_bytes(b'hello'); (root/'empty').mkdir()
            with TestClient(create_app(root,base/'state/index.sqlite3','a'*40),headers={'Authorization':'Bearer '+'a'*40}) as client:
                result=inspect_server(client)
            self.assertEqual(result['files'],1)
            self.assertEqual(result['directories'],1)
            self.assertEqual(result['bytes'],5)
            self.assertFalse(result['writable'])
            self.assertEqual((root/'file').read_bytes(),b'hello')

    def test_failure_categories_do_not_leak_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'token'; token.write_text('a'*40)
            errors=[(httpx.ConnectError('secret detail'),'connection_failed'),
                    (httpx.ConnectError('CERTIFICATE_VERIFY_FAILED'),'certificate_verification_failed'),
                    (httpx.ReadTimeout('secret detail'),'timeout'),
                    (ValueError('secret detail'),'invalid_manifest')]
            request=httpx.Request('GET','https://localhost/v1/manifest')
            errors.append((httpx.HTTPStatusError('secret detail',request=request,response=httpx.Response(401,request=request)),'authorization_failed'))
            for error,reason in errors:
                with self.subTest(reason=reason), patch('lansync.diagnostics.make_client'), patch('lansync.diagnostics.inspect_server',side_effect=error):
                    report=diagnose('https://localhost',token)
                    self.assertFalse(report['ok'])
                    self.assertEqual(report['checks'][-1]['reason'],reason)
                    self.assertNotIn('secret detail',json.dumps(report))
                    self.assertNotIn('a'*40,json.dumps(report))

    def test_missing_credentials_and_insecure_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'token'
            self.assertEqual(diagnose('https://localhost',token)['checks'][-1]['check'],'credentials')
            token.write_text('a'*40)
            report=diagnose('http://192.168.0.1',token)
            self.assertFalse(report['ok'])
            self.assertEqual(report['checks'][-1]['check'],'transport_configuration')
