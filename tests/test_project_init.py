import tempfile
from pathlib import Path
import unittest
from lansync.project_init import initialize
from lansync.config import load_project

class InitTests(unittest.TestCase):
    def test_generated_projects_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ('client','server'):
                root=Path(tmp)/mode
                config=initialize(root,mode,'https://127.0.0.1:8765')
                project=load_project(config)
                self.assertEqual(project.values['root'],root/'shared')
                self.assertTrue((root/'credentials').is_dir())
                self.assertFalse((root/'state').exists())
                before=config.read_bytes()
                with self.assertRaises(FileExistsError): initialize(root,mode,'https://127.0.0.1:8765')
                self.assertEqual(config.read_bytes(),before)

    def test_bad_url_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project'
            for url in ('http://server','https://user:password@server','https://server/path'):
                with self.assertRaises(ValueError): initialize(root,'client',url)
                self.assertFalse(root.exists())
