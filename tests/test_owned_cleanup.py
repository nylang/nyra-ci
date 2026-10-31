"""Only read-only files inside the validated disposable tree can be relaxed."""
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import owned_cleanup


class OwnedCleanup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'owned'
        self.root.mkdir()
        self.file = self.root / 'pack.idx'
        self.file.write_bytes(b'owned Git pack index')

    def invoke_error(self, path, error):
        def simulated_rmtree(root, *, onexc):
            onexc(lambda name: Path(name).unlink(), str(path), error)
        with patch.object(owned_cleanup.shutil, 'rmtree', simulated_rmtree):
            owned_cleanup.remove_tree(self.root)

    def test_owned_readonly_git_file_is_removed(self):
        self.file.chmod(stat.S_IRUSR)
        self.invoke_error(self.file, PermissionError('Windows read-only file'))
        self.assertFalse(self.file.exists())

    def test_other_io_error_is_preserved(self):
        with self.assertRaisesRegex(OSError, 'other failure'):
            self.invoke_error(self.file, OSError('other failure'))
        self.assertTrue(self.file.exists())

    def test_writable_access_denial_is_not_weakened(self):
        with self.assertRaises(PermissionError):
            self.invoke_error(self.file, PermissionError('locked handle or ACL'))
        self.assertTrue(self.file.exists())

    def test_outside_file_is_never_modified(self):
        outside = self.root.parent / 'outside'
        outside.write_bytes(b'protected')
        outside.chmod(stat.S_IRUSR)
        with self.assertRaises(PermissionError):
            self.invoke_error(outside, PermissionError('outside'))
        self.assertEqual(outside.read_bytes(), b'protected')
        self.assertFalse(outside.stat().st_mode & stat.S_IWUSR)
        outside.chmod(stat.S_IRUSR | stat.S_IWUSR)


if __name__ == '__main__':
    unittest.main()
