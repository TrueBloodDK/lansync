import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('lan_benchmark', Path(__file__).resolve().parents[1] / 'scripts/lan_benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class LanBenchmarkTests(unittest.TestCase):
    def test_fixture_rejects_paths_and_invalid_metadata(self):
        valid = {'schema': 1, 'id': 'run', 'files': [{'path': 'download-000.bin', 'size': 1024, 'sha256': 'a' * 64}]}
        self.assertEqual(benchmark.validate_fixture(valid), {'download-000.bin': (1024, 'a' * 64)})
        for values in ({'path': '../outside'}, {'path': 'C:/outside'}, {'size': True},
                       {'size': -1}, {'sha256': 'z' * 64}, {'sha256': None}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                benchmark.validate_fixture({**valid, 'files': [{**valid['files'][0], **values}]})
        with self.assertRaises(ValueError):
            benchmark.validate_fixture({**valid, 'files': valid['files'] * 2})

    def test_remote_requires_pristine_files_and_write_support(self):
        entry = {'kind': 'file', 'deleted': False, 'path': 'file', 'size': 1, 'sha256': 'a' * 64}
        for manifest in ({'writable': False, 'entries': [entry]},
                         {'writable': True, 'entries': [{**entry, 'deleted': True}]},
                         {'writable': True, 'entries': [{**entry, 'kind': 'directory'}]}):
            with self.subTest(manifest=manifest), patch.object(benchmark, 'fetch_manifest', return_value=manifest):
                with self.assertRaises(ValueError):
                    benchmark.remote_files(None)
