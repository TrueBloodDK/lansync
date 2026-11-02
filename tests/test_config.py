import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from lansync.cli import main

class ConfigTests(unittest.TestCase):
    def test_relative_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'shared').mkdir()
            (base / 'shared' / 'hello.txt').write_text('hello')
            config = base / 'scan.toml'
            config.write_text('command = "scan"\nroot = "shared"\nstate_dir = "state"\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run', str(config)]), 0)
            self.assertTrue((base / 'state' / 'manifest.json').exists())

    def test_invalid_configs(self):
        for body in ('command="run"', 'command="scan"\nroot="x"\nty po=1',
                     'command="scan"\nroot="x"\nunknown=true',
                     'command="serve"\nroot="x"\ntoken_file="token"\nallow_write="yes"',
                     'command="scan"', 'command="scan"\nroot="x"\nexclude="*.tmp"'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / 'config.toml'
                config.write_text(body)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['run', str(config)]), 1)

    def test_network_commands_dispatch(self):
        from unittest.mock import patch
        for command in ('serve', 'pull', 'sync'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                body = f'command="{command}"\nroot="shared"\ntoken_file="credentials/token.txt"\n'
                if command != 'serve':
                    body += 'url="https://192.168.0.10:8765"\nca_file="credentials/ca.pem"\n'
                if command in ('serve', 'sync'):
                    body += 'state_dir="state"\n'
                if command == 'serve':
                    body += 'allow_write=true\nport=8765\nexclude=["*.tmp", ".git"]\n'
                if command == 'sync':
                    body += 'watch=true\ninterval=2.5\nretry_max=10\n'
                config = base / 'config.toml'
                config.write_text(body)
                with patch('lansync.cli.network_command', return_value=0) as handler:
                    self.assertEqual(main(['run', str(config)]), 0)
                args = handler.call_args.args[0]
                self.assertEqual(args.command, command)
                self.assertEqual(args.root, base / 'shared')
                self.assertEqual(args.token_file, base / 'credentials/token.txt')
                self.assertFalse(args.allow_http)
                if command == 'serve':
                    self.assertTrue(args.allow_write)
                    self.assertEqual(args.exclude, ['*.tmp', '.git'])
                if command == 'sync':
                    self.assertTrue(args.watch)
                    self.assertEqual(args.interval, 2.5)

    def test_watch_dispatch(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'shared').mkdir()
            config = base / 'watch.toml'
            config.write_text('command="watch"\nroot="shared"\nstate_dir="state"\ndebounce=0.2\nexclude=["*.tmp"]\n')
            with patch('lansync.watcher.watch') as handler, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run', str(config)]), 0)
            self.assertEqual(handler.call_args.args[0], base / 'shared')
            self.assertEqual(handler.call_args.kwargs['debounce'], 0.2)

    def test_invalid_types(self):
        for body in ('command=[]', 'command={}', 'command="sync"\nroot="x"\nurl="https://localhost"\nstate_dir="s"\ntoken_file="t"\ninterval=nan',
                     'command="serve"\nroot="x"\ntoken_file="t"\nport=true'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / 'config.toml'
                config.write_text(body)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['run', str(config)]), 1)
