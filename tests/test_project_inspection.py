import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lansync.project_init import initialize
from lansync.credentials import create_credentials, export_client_credentials
from lansync.project_inspection import inspect_project

class InspectionTests(unittest.TestCase):
    def test_collects_missing_credentials_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project'; config=initialize(root,'client','https://localhost:8765')
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            report=inspect_project(config)
            self.assertFalse(report['ok'])
            failures={c['check'] for c in report['checks'] if not c['ok']}
            self.assertIn('token',failures)
            self.assertIn('client_tls_configuration',failures)
            self.assertFalse((root/'state').exists())
            self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_ready_projects_and_pending_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); server=base/'server'; client=base/'client'
            sc=initialize(server,'server'); cc=initialize(client,'client','https://localhost:8765')
            create_credentials(server/'credentials',['localhost'])
            export_client_credentials(server/'credentials',client/'credentials')
            with patch('httpx.Client.request',side_effect=AssertionError('Network request forbidden')):
                self.assertTrue(inspect_project(sc)['ok'])
                self.assertTrue(inspect_project(cc)['ok'])
            recovery=client/'state/recovery';recovery.mkdir(parents=True)
            (recovery/'pending.pending.json').write_text('{}')
            (client/'state/sync.lock').touch()
            report=inspect_project(cc)
            failures={c['check'] for c in report['checks'] if not c['ok']}
            self.assertIn('state_lock',failures)
            self.assertIn('pending_replacements',failures)
            self.assertNotIn((client/'credentials/token.txt').read_text().strip(),json.dumps(report))

    def test_mismatched_server_key_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=initialize(base/'project','server')
            create_credentials(base/'project/credentials',['localhost'])
            create_credentials(base/'other',['localhost'])
            (base/'project/credentials/server-key.pem').write_bytes((base/'other/server-key.pem').read_bytes())
            report=inspect_project(config)
            self.assertFalse(next(c['ok'] for c in report['checks'] if c['check']=='server_tls_pair'))
