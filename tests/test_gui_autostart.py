import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lansync.gui.autostart import Registration, desktop_entry, launch_command


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.registration = Registration('linux', self.base)

    def test_enable_update_disable_only_owned_file(self):
        unrelated = self.base / 'other.txt'
        unrelated.write_text('keep')
        self.assertIsNone(self.registration.read())
        first = desktop_entry(['/opt/LAN Sync/LANSync', '--config', '/home/me/Проект/project.toml'])
        self.registration.replace(None, first)
        self.assertEqual(self.registration.read(), first)
        updated = first.replace('Terminal=false', 'Terminal=true')
        self.registration.replace(first, updated)
        self.registration.replace(updated, None)
        self.assertIsNone(self.registration.read())
        self.assertEqual(unrelated.read_text(), 'keep')

    def test_external_edit_is_not_overwritten_or_removed(self):
        self.registration.replace(None, 'external edit')
        for replacement in (None, 'new command'):
            with self.assertRaises(ValueError):
                self.registration.replace('old command', replacement)
            self.assertEqual(self.registration.read(), 'external edit')

    def test_replace_failure_keeps_entry_and_cleans_temporary(self):
        self.registration.replace(None, 'old command')
        with patch('lansync.gui.autostart.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.registration.replace('old command', 'new command')
        self.assertEqual(self.registration.read(), 'old command')
        self.assertEqual(list(self.registration.path.parent.glob('.lansync-*')), [])

    def test_exec_escaping_has_no_shell_and_rejects_field_codes(self):
        value = desktop_entry(['/opt/LAN Sync/app', '--config', '/a/$b`c"d\\e'])
        self.assertIn('Exec="/opt/LAN Sync/app" "--config" ', value)
        self.assertIn('\\\\$', value)
        self.assertIn('\\\\`', value)
        self.assertIn('\\\\"', value)
        self.assertIn('\\\\\\\\', value)
        for bad in ('/a/%f', '/a/\nExec=evil', '/a/\x00'):
            with self.assertRaises(ValueError):
                desktop_entry(['/app', bad])

    def test_windows_command_quotes_and_length_limit(self):
        registration = Registration('win32', self.base)
        self.assertEqual(registration.render(['C:\\LAN Sync\\app.exe', '--config', 'C:\\My Project\\p.toml']),
                         '"C:\\LAN Sync\\app.exe" --config "C:\\My Project\\p.toml"')
        with self.assertRaises(ValueError):
            registration.render(['a' * 261])

    def test_frozen_command_pins_config_and_opt_in_actions(self):
        config = self.base / 'project.toml'
        config.touch()
        executable = self.base / 'LANSync'
        executable.touch()
        with patch('sys.frozen', True, create=True), patch('sys.executable', str(executable)):
            self.assertEqual(launch_command(config), [str(executable), '--config', str(config.resolve())])
            self.assertEqual(launch_command(config, True, True)[-2:], ['--start-sync', '--minimized'])

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink check')
    def test_symlink_registration_is_refused(self):
        other = self.base / 'other'
        other.write_text('keep')
        self.registration.path.parent.mkdir()
        self.registration.path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.registration.replace(None, 'new command')
        self.assertEqual(other.read_text(), 'keep')

    def test_windows_registry_uses_current_user_named_value_only(self):
        registry = Mock()
        registry.OpenKey.return_value.__enter__ = Mock(return_value='key')
        registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
        registry.CreateKey.return_value.__enter__ = Mock(return_value='key')
        registry.CreateKey.return_value.__exit__ = Mock(return_value=False)
        registry.QueryValueEx.return_value = ('old command', 1)
        with patch.dict('sys.modules', {'winreg': registry}):
            Registration('win32', self.base).replace('old command', 'new command')
        registry.SetValueEx.assert_called_once_with('key', 'LANSyncDesktop', 0, registry.REG_SZ, 'new command')
        self.assertEqual(registry.CreateKey.call_args.args[0], registry.HKEY_CURRENT_USER)
