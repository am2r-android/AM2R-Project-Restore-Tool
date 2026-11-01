"""Known old exports must restore their own assets while retaining code edits."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_project as tool
import test_restore as fixtures
from test_restore import sha, write_json


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RestoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.bundle = self.fixture.bundle
        self.descriptor = copy.deepcopy(self.fixture.manifest)
        self.descriptor['snapshot'] = 'older'
        self.catalog = {'schema': 1, 'bundle_sha256': self.descriptor['bundle_sha256'], 'projects': {}}

    def register(self, legacy=False):
        if legacy:
            write_json(self.project / 'asset-inputs.json', {
                'schema': 1, 'edition': 'dual', 'files': self.descriptor['files']})
            self.descriptor['edition'] = 'dual-screen'
        checksum = sha((self.project / 'asset-inputs.json').read_bytes())
        self.key = checksum
        self.descriptor['manifest_sha256'] = checksum
        self.descriptor['compatible_releases'] = ['older']
        self.description_path = self.bundle / 'projects/older.json'
        self.description_path.parent.mkdir(exist_ok=True)
        self.save_descriptor()

    def save_descriptor(self):
        write_json(self.description_path, self.descriptor)
        data = self.description_path.read_bytes()
        self.catalog['projects'][self.key] = [
            {'path': 'projects/older.json', 'size': len(data), 'sha256': sha(data)}]
        write_json(self.bundle / 'catalog.json', self.catalog)

    def test_schema_one_restores_and_normalizes_dual_edition(self):
        self.register(legacy=True)
        before = (self.project / 'asset-inputs.json').read_bytes()
        result = self.fixture.restore()
        self.assertEqual(result['edition'], 'dual-screen')
        self.assertEqual(result['restored'], 3)
        self.assertEqual((self.project / 'asset-inputs.json').read_bytes(), before)
        self.assertEqual((self.project / 'sprites/example/a.png').read_bytes(), self.fixture.asset)

    def test_catalog_can_restore_modern_source_without_its_original_bundle(self):
        self.register()
        original = json.loads((self.project / 'asset-inputs.json').read_text())
        original['bundle_sha256'] = 'a' * 64
        write_json(self.project / 'asset-inputs.json', original)
        self.register()
        self.assertEqual(self.fixture.restore()['restored'], 3)

    def test_unknown_manifest_cannot_fall_back_to_latest_assets(self):
        self.register()
        with (self.project / 'asset-inputs.json').open('ab') as f:
            f.write(b' ')
        with self.assertRaisesRegex(tool.RestoreError, 'catalog'):
            self.fixture.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_changed_descriptor_rejected_before_writing(self):
        self.register()
        with self.description_path.open('ab') as f:
            f.write(b' ')
        with self.assertRaises(tool.RestoreError):
            self.fixture.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_catalog_cannot_select_other_bundle(self):
        self.register()
        self.descriptor['bundle_sha256'] = 'b' * 64
        self.save_descriptor()
        with self.assertRaises(tool.RestoreError):
            self.fixture.restore()

    def test_edited_code_preserved_and_check_uses_catalog(self):
        self.register(legacy=True)
        (self.project / 'code.gml').write_text('my new code();\n')
        result = self.fixture.restore()
        self.assertEqual(result['modified_source'], ['code.gml'])
        self.assertEqual((self.project / 'code.gml').read_text(), 'my new code();\n')
        self.assertEqual(tool.inspect_project(self.project, self.bundle)['missing_assets'], [])

    def test_source_variants_with_shared_assets_select_matching_code(self):
        self.register(legacy=True)
        newer = copy.deepcopy(self.descriptor)
        newer['snapshot'] = 'newer'
        code = b'newer version code();\n'
        newer['source_files'][1] = self.fixture.record('code.gml', code)
        path = self.bundle / 'projects/newer.json'
        write_json(path, newer)
        self.catalog['projects'][self.key].append(self.fixture.record('projects/newer.json', path.read_bytes()))
        write_json(self.bundle / 'catalog.json', self.catalog)
        (self.project / 'code.gml').write_bytes(code)
        result = self.fixture.restore()
        self.assertEqual(result['snapshot'], 'newer')
        self.assertEqual(result['modified_source'], [])
        self.assertEqual((self.project / 'code.gml').read_bytes(), code)

    def test_catalog_descriptor_path_cannot_escape_bundle(self):
        self.register()
        self.catalog['projects'][self.key][0]['path'] = '../outside.json'
        write_json(self.bundle / 'catalog.json', self.catalog)
        with self.assertRaises(tool.RestoreError):
            self.fixture.restore()


if __name__ == '__main__':
    unittest.main()
