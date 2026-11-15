import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest

from lansync.config import load_project
from lansync.credentials import create_credentials
from lansync.project_init import initialize
from lansync.gui.projects import create_client, import_bundle

try:
    from PySide6.QtWidgets import QApplication, QDialog
    from lansync.gui.setup_dialog import SetupDialog
    from lansync.gui.plan import PlanView
except ImportError:
    QApplication = None


class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.bundle = create_credentials(self.base / 'server-keys', ['localhost'])

    def test_creation_preserves_existing_files_and_copies_only_client_credentials(self):
        root = self.base / 'existing files'
        root.mkdir()
        (root / 'важно.txt').write_bytes(b'keep me')
        config = create_client(self.base / 'project', 'https://localhost:8765', self.bundle, root)
        args = load_project(config).namespace()
        self.assertEqual(args.root, root.resolve())
        self.assertEqual((root / 'важно.txt').read_bytes(), b'keep me')
        self.assertEqual({p.name for p in (config.parent / 'credentials').iterdir()}, {'ca.pem', 'token.txt'})
        self.assertFalse(args.state_dir.exists())
        with self.assertRaises(ValueError):
            create_client(self.base / 'project', 'https://localhost:8765', self.bundle)

    def test_invalid_ca_does_not_leave_project(self):
        (self.bundle / 'ca.pem').write_text('not a certificate')
        with self.assertRaises(ValueError):
            create_client(self.base / 'project', 'https://localhost', self.bundle)
        self.assertFalse((self.base / 'project').exists())

    def test_project_cannot_put_state_inside_shared_root(self):
        root = self.base / 'shared'
        root.mkdir()
        with self.assertRaises(ValueError):
            create_client(root / 'project', 'https://localhost', self.bundle, root)
        self.assertFalse((root / 'project').exists())

    def test_import_refuses_lock_and_existing_credentials(self):
        config = initialize(self.base / 'project', 'client', 'https://localhost')
        state = config.parent / 'state'
        state.mkdir()
        (state / 'sync.lock').touch()
        with self.assertRaises(ValueError):
            import_bundle(config, self.bundle)
        self.assertFalse(any((config.parent / 'credentials').iterdir()))
        (state / 'sync.lock').unlink()
        import_bundle(config, self.bundle)
        original = (config.parent / 'credentials/token.txt').read_bytes()
        with self.assertRaises(ValueError):
            import_bundle(config, self.bundle)
        self.assertEqual((config.parent / 'credentials/token.txt').read_bytes(), original)


@unittest.skipIf(QApplication is None, 'Optional GUI dependencies are not installed')
class ProjectWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_creates_project_without_starting_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bundle = create_credentials(base / 'bundle', ['localhost'])
            dialog = SetupDialog()
            dialog.directory.setText(str(base / 'project'))
            dialog.bundle.setText(str(bundle))
            dialog.url.setText('https://localhost:8765')
            dialog.create()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertTrue(dialog.config.is_file())
            self.assertFalse((dialog.config.parent / 'state').exists())
            dialog.deleteLater()

    def test_plan_rows_filter_and_invalidation(self):
        view = PlanView()
        view.set_report({'planned': [{'path': 'отчёт.txt', 'operation': 'upload', 'bytes': 32},
                                     {'path': 'old.txt', 'operation': 'delete_remote', 'bytes': 0}],
                         'conflicts': ['conflict.txt']})
        self.assertEqual(view.model.rowCount(), 3)
        view.search.setText('УДАЛИТЬ')
        self.assertEqual(view.proxy.rowCount(), 1)
        view.invalidate()
        self.assertIn('устареть', view.note.text())
        self.assertEqual(view.model.rowCount(), 3)
        view.clear()
        self.assertEqual(view.model.rowCount(), 0)
        self.assertEqual(view.search.text(), '')
        view.set_report({'planned': [], 'conflicts': []})
        view.invalidate()
        self.assertIn('устареть', view.note.text())
        view.deleteLater()
