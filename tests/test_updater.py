import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from updater import update, digest, UpdateError

class Updates(unittest.TestCase):
    def setup_root(self, directory):
        root=Path(directory)
        (root/'server.py').write_bytes(b'old')
        old={'version':'1','files':{'server.py':digest(b'old')}}
        (root/'update-manifest.json').write_text(json.dumps(old))
        return root
    def download(self, path):
        if path=='update-manifest.json':
            return json.dumps({'version':'2','files':{'server.py':digest(b'new')}}).encode()
        return b'new'
    def test_updates_preserve_settings_backup_and_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.setup_root(directory)
            (root/'credentials.bin').write_bytes(b'private')
            update(root,self.download)
            self.assertEqual((root/'server.py').read_bytes(),b'new')
            self.assertEqual((root/'credentials.bin').read_bytes(),b'private')
            self.assertEqual(next((root/'.update-backups').glob('*/server.py')).read_bytes(),b'old')
            self.assertIn('Already up to date',update(root,self.download))
    def test_rejects_corruption_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.setup_root(directory)
            with self.assertRaises(UpdateError):
                update(root,lambda name: self.download(name) if name=='update-manifest.json' else b'corrupt')
            self.assertEqual((root/'server.py').read_bytes(),b'old')
    def test_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.setup_root(directory)
            (root/'server.py').write_bytes(b'user code')
            with self.assertRaises(UpdateError):update(root,self.download)
            self.assertEqual((root/'server.py').read_bytes(),b'user code')
    def test_rejects_paths_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.setup_root(directory)
            unsafe=json.dumps({'version':'2','files':{'../escape':digest(b'bad')}}).encode()
            with self.assertRaises(UpdateError):update(root,lambda _:unsafe)
            original=Path.replace
            def fail_manifest(path,target):
                if path.name=='update-manifest.json.update-tmp':raise OSError('simulated disk error')
                return original(path,target)
            with patch.object(Path,'replace',fail_manifest),self.assertRaises(OSError):
                update(root,self.download)
            self.assertEqual((root/'server.py').read_bytes(),b'old')
if __name__=='__main__':unittest.main()
