"""Catalog generation must identify archives and exclude private import data."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_catalog as builder


class CatalogBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'old-source.zip'
        files = [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                 for name, data in [('sprites/old.png', b'old image'),
                                    ('notes/import/import.txt', b'private machine report'),
                                    ('.suite-generated', b'private build marker')]]
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('AM2R-project/asset-inputs.json', json.dumps({'schema': 1, 'edition': 'dual', 'files': files}))
            z.writestr('AM2R-project/AM2R-project.yyp', '{"resources":[]}')
            z.writestr('AM2R-project/code.gml', 'old game code();')
        self.release = {'name': self.archive.name, 'tag': 'older', 'url': 'https://example.com/old-source.zip',
                        'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}

    def test_legacy_catalog_keeps_assets_but_replaces_private_import_report(self):
        project, replacements = builder.read_export(self.archive, self.release)
        self.assertEqual(project['edition'], 'dual-screen')
        self.assertEqual([r['path'] for r in project['source_files']], ['AM2R-project.yyp', 'code.gml'])
        self.assertEqual(project['files'][0]['sha256'], hashlib.sha256(b'old image').hexdigest())
        self.assertEqual(project['omitted_inputs'], ['.suite-generated'])
        self.assertEqual(project['replacement_notes'], ['notes/import/import.txt'])
        self.assertNotIn(b'private machine report', replacements.values())
        self.assertTrue(any(b'import report' in data for data in replacements.values()))

    def test_tampered_archive_not_added_to_catalog(self):
        with self.archive.open('ab') as f:
            f.write(b'changed')
        with self.assertRaises(builder.RestoreError):
            builder.read_export(self.archive, self.release)

    def test_unknown_manifest_schema_rejected(self):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('AM2R-project/asset-inputs.json', '{"schema":99,"edition":"dual","files":[]}')
        self.release['sha256'] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaises(builder.RestoreError):
            builder.read_export(self.archive, self.release)


if __name__ == '__main__':
    unittest.main()
