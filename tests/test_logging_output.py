import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from lansync.cli import main
from lansync.logging_output import transcript

class LogTests(unittest.TestCase):
    def test_scan_log_and_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); (base/'shared').mkdir()
            args=['scan',str(base/'shared'),'--state-dir',str(base/'state'),'--log-file',str(base/'run.log')]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args),0)
            text=(base/'run.log').read_text()
            self.assertIn('Files: 0',text)
            self.assertIn('exit code: 0',text)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args),1)
            self.assertEqual((base/'run.log').read_text(),text)

    def test_redaction_and_stream_restoration(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'log.txt'
            old=sys.stdout
            with contextlib.redirect_stdout(io.StringIO()):
                with transcript(path, ['secret-token']):
                    print('secret-token')
            self.assertIs(sys.stdout,old)
            self.assertNotIn('secret-token',path.read_text())
            self.assertIn('[REDACTED]',path.read_text())

    def test_json_plan_with_log_stays_parseable(self):
        import json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); (base/'shared').mkdir()
            (base/'token').write_text('a'*40)
            plan={'dry_run':True,'planned':[{'operation':'upload','path':'hello.txt'}], 'conflicts':[]}
            args=['sync','https://localhost:8765',str(base/'shared'),'--state-dir',str(base/'state'),'--token-file',str(base/'token'),'--dry-run','--json','--log-file',str(base/'plan.log')]
            with patch('lansync.network.make_client'), patch('lansync.sync.synchronize',return_value=plan) as sync:
                with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args),0)
                self.assertEqual(json.loads(output.getvalue()),plan)
                self.assertTrue(sync.call_args.kwargs['dry_run'])
            self.assertIn('hello.txt',(base/'plan.log').read_text())
            self.assertFalse((base/'state').exists())
