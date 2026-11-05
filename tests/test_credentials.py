import ssl
import tempfile
from pathlib import Path
import unittest
from lansync.credentials import create_credentials, export_client_credentials
from lansync.network import read_token

class CredentialTests(unittest.TestCase):
    def test_create_export_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); server=base/'server'; server.mkdir()
            create_credentials(server,['127.0.0.1','localhost'])
            self.assertGreaterEqual(len(read_token(server/'token.txt')),32)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(server/'server.pem',server/'server-key.pem')
            client=export_client_credentials(server,base/'client')
            self.assertEqual({p.name for p in client.iterdir()},{'ca.pem','token.txt'})
            ssl.create_default_context(cafile=str(client/'ca.pem'))
            self.assertEqual((server/'token.txt').read_bytes(),(client/'token.txt').read_bytes())
            before={p.name:p.read_bytes() for p in server.iterdir()}
            with self.assertRaises(ValueError): create_credentials(server,['localhost'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in server.iterdir()})
            with self.assertRaises(ValueError): export_client_credentials(server,client)

    def test_invalid_inputs_do_not_create_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with self.assertRaises(ValueError): create_credentials(base/'target',['https://server'])
            self.assertFalse((base/'target').exists())
            source=base/'source';source.mkdir()
            (source/'token.txt').write_text('a'*40)
            (source/'ca.pem').write_text('not a certificate')
            with self.assertRaises((ssl.SSLError,ValueError)): export_client_credentials(source,base/'target')
            self.assertFalse((base/'target').exists())
