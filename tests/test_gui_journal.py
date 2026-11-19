import json
from pathlib import Path
import tempfile
import unittest

from lansync.gui.journal import Journal, redact
from lansync.gui.problems import explain


class JournalTests(unittest.TestCase):
    def test_rotation_redaction_export_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = Journal(root/'logs', max_bytes=1024, backups=2)
            token = 'secret_' * 8
            for index in range(35):
                journal.append(f'{index}: Bearer {token} https://user:password@host/folder ' + 'данные ' * 30)
            files = list((root/'logs').iterdir())
            self.assertLessEqual(len(files), 3)
            for file in files:
                self.assertLessEqual(file.stat().st_size, 1024)
                for line in file.read_text(encoding='utf-8').splitlines():
                    json.loads(line)
            output = root/'export.txt'
            journal.export(output)
            text = output.read_text(encoding='utf-8')
            self.assertNotIn(token, text)
            self.assertNotIn('password', text)
            self.assertIn('[скрыто]', text)
            with self.assertRaises(FileExistsError):
                journal.export(output)
            self.assertEqual(text, output.read_text(encoding='utf-8'))
            self.assertIn('34:', text)

    def test_disk_failure_is_nonfatal_and_next_write_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'logs'
            path.write_text('occupied')
            journal = Journal(path)
            self.assertEqual(journal.append('message'), 'message')
            self.assertIsNotNone(journal.error)
            path.unlink()
            journal.append('recovered')
            self.assertIsNone(journal.error)
            self.assertIn('recovered', journal.path.read_text())

    def test_large_record_stays_bounded_and_redacts_before_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = Journal(temp, max_bytes=1024)
            journal.append('я ' * 100000 + 'x'*40)
            self.assertLessEqual(journal.path.stat().st_size, 1024)
            value = json.loads(journal.path.read_text(encoding='utf-8'))
            self.assertIn('[сокращено]', value['message'])
            self.assertNotIn('x'*40, redact('token=' + 'x'*40))
            journal.append('\0' * 10000)
            self.assertLessEqual(journal.path.stat().st_size, 1024)

    def test_known_problems_have_specific_actions(self):
        cases = {'sync.lock': 'блокиров', 'pending_replacements': 'recovery',
                 'certificate_verification_failed': 'сертификат', 'authorization_failed': 'токен',
                 'connection_failed': 'брандмауэр', 'timeout': 'тайм-аут',
                 'No space left on device': 'место', 'Permission denied': 'прав',
                 'unknown issue': 'журнал'}
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertIn(expected, explain(message))
