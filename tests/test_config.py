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

    def test_check_does_not_run_or_create_state(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            config=base/'client.toml'
            config.write_text('command="sync"\nurl="https://localhost"\nroot="missing-root"\nstate_dir="state"\ntoken_file="missing-token"\ndry_run=true\njson=true\n')
            with patch('lansync.cli.network_command') as network, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run',str(config),'--check']),0)
                network.assert_not_called()
            self.assertEqual(list(base.iterdir()),[config])
            config.write_text(config.read_text()+'watch=true\n')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['run',str(config),'--check']),1)

    def test_project_actions_reuse_config(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'client.toml'
            config.write_text('command="sync"\nurl="https://localhost:8765"\nroot="shared"\nstate_dir="state"\ntoken_file="token"\nwatch=true\n')
            for action in ('once','plan','resolve'):
                extra=['--path','hello.txt','--choose','remote'] if action=='resolve' else []
                with patch('lansync.cli.network_command',return_value=0) as handler:
                    self.assertEqual(main(['run',str(config),'--action',action,*extra]),0)
                args=handler.call_args.args[0]
                self.assertEqual(args.root,base/'shared')
                self.assertEqual(args.state_dir,base/'state')
                if action!='resolve':
                    self.assertFalse(args.watch)
                    self.assertEqual(args.dry_run,action=='plan')
                else:
                    self.assertEqual(args.path,'hello.txt')
            with patch('lansync.status.show_status',return_value=0) as handler:
                self.assertEqual(main(['run',str(config),'--action','status','--json']),0)
                self.assertEqual(handler.call_args.args[0],base/'state')
                self.assertTrue(handler.call_args.kwargs['as_json'])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['run',str(config),'--action','resolve']),1)

    def test_model_without_parser_and_all_project_actions(self):
        from lansync.config import load_project
        from lansync.workflow import action_arguments
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); config=base/'client.toml'
            config.write_text('command="sync"\nurl="https://localhost"\nroot="shared"\nstate_dir="state"\ntoken_file="token"\nwatch=true\n')
            project=load_project(config)
            self.assertEqual(project.values['root'],base/'shared')
            self.assertEqual(project.values['interval'],30)
            for action in ('start','once','plan','doctor','status','resolve','recovery-list','recovery-verify','recovery-restore'):
                with self.subTest(action=action):
                    request=SimpleNamespace(action=action,json=False,path='hello' if action=='resolve' else None,choose='local' if action=='resolve' else None,id='a'*32 if action=='recovery-restore' else None,output=base/'restored' if action=='recovery-restore' else None)
                    args=action_arguments(project.namespace(),request,project.arguments())
                    self.assertTrue(args)
                    if action=='recovery-restore':
                        self.assertIn('--output='+str(base/'restored'),args)
            self.assertEqual(list(base.iterdir()),[config])

    def test_run_parses_cli_only_once(self):
        import argparse
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); (base/'shared').mkdir()
            config=base/'scan.toml'
            config.write_text('command="scan"\nroot="shared"\nstate_dir="state"\nlog_file="run.log"\n')
            original=argparse.ArgumentParser.parse_args
            calls=[]
            def counted(parser,*args,**kwargs):
                calls.append(1)
                return original(parser,*args,**kwargs)
            with patch.object(argparse.ArgumentParser,'parse_args',counted), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['run',str(config)]),0)
            self.assertEqual(len(calls),1)
            self.assertTrue((base/'state/manifest.json').exists())
            self.assertTrue((base/'run.log').exists())

    def test_keep_both_workflow_preserves_copy_path(self):
        from types import SimpleNamespace
        from lansync.workflow import prepare_action
        config=SimpleNamespace(command='sync',root=Path('/root'),url='https://localhost',state_dir=Path('/state'))
        request=SimpleNamespace(action='resolve',path='file.txt',choose='both',copy_path='local.txt',json=False,id=None,output=None)
        result=prepare_action(config,request)
        self.assertEqual(result.copy_path,'local.txt')
        self.assertEqual(result.choose,'both')
        request.copy_path=None
        with self.assertRaises(ValueError): prepare_action(config,request)
