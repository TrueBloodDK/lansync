import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from lansync.cli import main


class StatusTests(unittest.TestCase):
    def test_reports_and_stale_lock_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            report = dict(uploaded=1, downloaded=2, deleted_local=0, deleted_remote=1, conflicts=['hello.txt'])
            (state / 'last-sync.json').write_text(json.dumps(report))
            (state / 'loop-status.json').write_text(json.dumps(dict(status='retrying', error='Connection refused', last_success_utc='2026-11-02T00:00:00+00:00')))
            (state / 'sync.lock').write_text('old lock')
            before = {p.name: p.read_bytes() for p in state.iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['status', '--state-dir', tmp]), 0)
            for text in ('hello.txt', 'retrying', 'Connection refused', 'may be stale'):
                self.assertIn(text, output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['status', '--state-dir', tmp, '--json']), 0)
            self.assertEqual(json.loads(output.getvalue())['last_sync'], report)
            self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir()})

    def test_empty_missing_and_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['status', '--state-dir', tmp]), 0)
            self.assertIn('No synchronization reports', output.getvalue())
            missing = Path(tmp) / 'missing'
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['status', '--state-dir', str(missing)]), 1)
            self.assertFalse(missing.exists())
            for content in ('{', '[]', '{"conflicts": "bad"}'):
                (Path(tmp) / 'last-sync.json').write_text(content)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['status', '--state-dir', tmp]), 1)

    def test_corrupt_loop_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            for report in ({}, {'status': []}, {'status': 'retrying', 'failures': True},
                           {'status': 'retrying', 'retry_in_seconds': float('nan')},
                           {'status': 'failed', 'error': {}}):
                with self.subTest(report=report):
                    (Path(tmp) / 'loop-status.json').write_text(json.dumps(report))
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(['status', '--state-dir', tmp]), 1)

    def test_older_loop_report_is_marked(self):
        from lansync.status import read_status
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            report=dict(uploaded=0,downloaded=0,deleted_local=0,deleted_remote=0,conflicts=[],completed_utc='2026-11-04T12:00:00+00:00')
            (state/'last-sync.json').write_text(json.dumps(report))
            (state/'loop-status.json').write_text(json.dumps(dict(status='failed',updated_utc='2026-11-04T11:00:00+00:00')))
            self.assertTrue(read_status(state)['loop_report_older'])
            (state/'loop-status.json').write_text(json.dumps(dict(status='synced',updated_utc='2026-11-04T13:00:00+00:00')))
            self.assertFalse(read_status(state)['loop_report_older'])

    def test_monitor_waits_for_state_and_suppresses_duplicates(self):
        from lansync.status import status_updates
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'later'
            class Stop:
                count=0
                def is_set(self): return self.count>=4
                def wait(self, delay):
                    self.count+=1
                    if self.count==1: state.mkdir()
                    if self.count==3: (state/'sync.lock').touch()
            events=list(status_updates(state,interval=.01,stop=Stop()))
            self.assertEqual(len(events),3)
            self.assertFalse(events[0]['available'])
            self.assertTrue(events[1]['available'])
            self.assertTrue(events[2]['snapshot']['lock_present'])

    def test_monitor_json_is_ndjson_and_invalid_interval_rejected(self):
        from lansync.status import watch_status
        with tempfile.TemporaryDirectory() as tmp:
            class Stop:
                done=False
                def is_set(self): return self.done
                def wait(self, delay): self.done=True
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(watch_status(tmp,as_json=True,stop=Stop()),0)
            event=json.loads(output.getvalue())
            self.assertTrue(event['available'])
            self.assertIn('observed_utc',event)
            with self.assertRaises(ValueError): watch_status(tmp,interval=0,stop=Stop())
