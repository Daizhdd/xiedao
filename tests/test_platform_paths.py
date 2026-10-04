"""Exercise frozen paths without writing into real applications or user data."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_paths


class PlatformPathsTest(unittest.TestCase):
    def test_mac_data_is_stable_across_app_locations(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(app_paths.sys, 'platform', 'darwin'), \
                patch.object(app_paths.sys, 'frozen', True, create=True), \
                patch.object(Path, 'home', return_value=Path('/Users/作者')):
            for executable in ('/Applications/写道.app/Contents/MacOS/写道',
                               '/Volumes/写道/写道.app/Contents/MacOS/写道'):
                with patch.object(app_paths.sys, 'executable', executable):
                    self.assertEqual(app_paths.data_directory(),
                                     Path('/Users/作者/Library/Application Support/写道'))

    def test_windows_portable_directory_is_preserved(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(app_paths.sys, 'platform', 'win32'), \
                patch.object(app_paths.sys, 'frozen', True, create=True), \
                patch.object(app_paths.sys, 'executable', str(Path('portable') / '写道.exe')):
            self.assertEqual(app_paths.data_directory(), Path('portable/data'))

    def test_source_default_and_explicit_isolation(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(app_paths.sys, 'platform', 'win32'), \
                patch.object(app_paths.sys, 'frozen', False, create=True):
            self.assertEqual(app_paths.data_directory(),
                             Path(app_paths.__file__).resolve().parent / 'data')
            with tempfile.TemporaryDirectory(prefix='写道-test-') as folder:
                with patch.dict(os.environ, {'XIEDAO_DATA_DIR': folder}):
                    self.assertEqual(app_paths.data_directory(), Path(folder).resolve())

    def test_resources_use_bundle_root_without_becoming_data(self):
        with patch.object(app_paths.sys, '_MEIPASS', '/bundle/Resources', create=True):
            self.assertEqual(Path(app_paths.resource_path('assets/brand-logo.png')),
                             Path('/bundle/Resources/assets/brand-logo.png'))


if __name__ == '__main__':
    unittest.main()
