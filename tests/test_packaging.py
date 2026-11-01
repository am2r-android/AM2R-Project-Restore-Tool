"""Portable programs must work without Python or a decoder on the user's PATH."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_project as tool


class PortablePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='portable paths ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_source_bundle_is_beside_script_not_working_directory(self):
        with mock.patch.object(tool, '__file__', str(self.root / 'restore_project.py')):
            self.assertTrue(tool.application_dir().samefile(self.root))

    def test_frozen_bundle_is_beside_executable_not_internal_python_files(self):
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', str(self.root / 'AM2R-PRT.exe')), \
                mock.patch.object(tool, '__file__', str(self.root / '_internal/restore_project.py')):
            self.assertTrue(tool.application_dir().samefile(self.root))

    def test_bundled_decoder_does_not_need_system_path(self):
        decoder = self.root / 'tools' / ('xdelta3.exe' if sys.platform == 'win32' else 'xdelta3')
        decoder.parent.mkdir()
        decoder.write_bytes(b'fixture executable')
        with mock.patch.object(tool, '__file__', str(self.root / 'restore_project.py')), \
                mock.patch.dict('os.environ', {'PATH': ''}):
            self.assertTrue(Path(tool.find_xdelta()).samefile(decoder))

    def test_frozen_missing_decoder_cannot_silently_use_host_installation(self):
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', str(self.root / 'AM2R-PRT')), \
                mock.patch.object(tool.shutil, 'which', return_value='/host/xdelta3'):
            with self.assertRaisesRegex(tool.RestoreError, 'bundled'):
                tool.find_xdelta()


if __name__ == '__main__':
    unittest.main()
