"""Behavior checks using small, real xdelta bundles and temporary projects."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_project as tool
import build_bundle as builder


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.bundle = self.root / 'bundle'
        self.project.mkdir()
        self.bundle.mkdir()
        self.original = self.root / 'original.zip'
        self.base = b'original verified game data\n' * 1000
        self.asset = self.base[:700] + b'updated sprite pixels\n'
        self.music = b'original external music\n' * 100
        with zipfile.ZipFile(self.original, 'w') as z:
            z.writestr('owned/data.win', self.base)
            z.writestr('owned/music.ogg', self.music)
        (self.root / 'data.win').write_bytes(self.base)
        (self.root / 'payload').write_bytes(self.asset)
        subprocess.run(['xdelta3', '-e', '-D', '-A', '-s', str(self.root / 'data.win'),
                        str(self.root / 'payload'), str(self.bundle / 'assets.xdelta')], check=True)
        patch = (self.bundle / 'assets.xdelta').read_bytes()
        self.description = {
            'schema': 1, 'snapshot': 'fixture',
            'source': {'size': len(self.base), 'sha256': sha(self.base)},
            'patch': {'path': 'assets.xdelta', 'size': len(patch), 'sha256': sha(patch)},
            'payload': {'size': len(self.asset), 'sha256': sha(self.asset)},
            'blobs': {
                sha(self.asset): {'size': len(self.asset), 'offset': 0},
                sha(self.music): {'size': len(self.music), 'member': 'music.ogg'},
            },
        }
        (self.project / 'AM2R-project.yyp').write_text('{"name":"fixture"}\n')
        (self.project / 'code.gml').write_text('original code();\n')
        self.manifest = {
            'schema': 2, 'edition': 'standard', 'snapshot': 'fixture',
            'bundle_sha256': '',
            'source_files': [self.record('AM2R-project.yyp', (self.project / 'AM2R-project.yyp').read_bytes()),
                             self.record('code.gml', (self.project / 'code.gml').read_bytes())],
            'files': [self.record('sprites/example/a.png', self.asset),
                      self.record('sprites/example/layers/a.png', self.asset),
                      self.record('sounds/music/music', self.music)],
        }
        self.save_manifest()

    @staticmethod
    def record(name, data):
        return {'path': name, 'size': len(data), 'sha256': sha(data)}

    def save_manifest(self):
        write_json(self.bundle / 'manifest.json', self.description)
        self.manifest['bundle_sha256'] = sha((self.bundle / 'manifest.json').read_bytes())
        write_json(self.project / 'asset-inputs.json', self.manifest)

    def restore(self):
        return tool.restore(self.project, self.original, self.bundle)

    def test_restores_exact_assets_without_changing_code(self):
        before = {p.name: p.read_bytes() for p in self.project.iterdir()}
        result = self.restore()
        self.assertEqual(result['restored'], 3)
        for row in self.manifest['files']:
            self.assertEqual(sha((self.project / row['path']).read_bytes()), row['sha256'])
        for name, data in before.items():
            self.assertEqual((self.project / name).read_bytes(), data)
        self.assertEqual(tool.inspect_project(self.project)['missing_assets'], [])

    def test_idempotent_and_keeps_user_code_edits(self):
        self.restore()
        (self.project / 'code.gml').write_text('my new feature();\n')
        result = self.restore()
        self.assertEqual(result['restored'], 0)
        self.assertEqual(result['modified_source'], ['code.gml'])
        self.assertEqual((self.project / 'code.gml').read_text(), 'my new feature();\n')

    def test_equal_content_files_are_independent(self):
        self.restore()
        first = self.project / 'sprites/example/a.png'
        second = self.project / 'sprites/example/layers/a.png'
        self.assertNotEqual(first.stat().st_ino, second.stat().st_ino)
        first.write_bytes(b'edited image')
        self.assertEqual(second.read_bytes(), self.asset)

    def test_wrong_original_writes_no_assets(self):
        with zipfile.ZipFile(self.original, 'w') as z:
            z.writestr('data.win', b'wrong game')
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_bad_external_file_writes_no_assets(self):
        with zipfile.ZipFile(self.original, 'w') as z:
            z.writestr('data.win', self.base)
            z.writestr('music.ogg', b'wrong music')
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_existing_different_asset_aborts_before_other_writes(self):
        path = self.project / 'sounds/music/music'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'my replacement music')
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertEqual(path.read_bytes(), b'my replacement music')
        self.assertFalse((self.project / 'sprites').exists())

    def test_corrupt_patch_writes_no_assets(self):
        with (self.bundle / 'assets.xdelta').open('ab') as f:
            f.write(b'corrupt')
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_wrong_payload_hash_writes_no_assets(self):
        self.description['payload']['sha256'] = '0' * 64
        self.save_manifest()
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_wrong_blob_hash_writes_no_assets(self):
        wrong = sha(b'wrong pixels')
        self.description['blobs'][wrong] = self.description['blobs'].pop(sha(self.asset))
        for row in self.manifest['files'][:2]:
            row['sha256'] = wrong
        self.save_manifest()
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())

    def test_path_traversal_case_duplicates_and_code_overlap_rejected(self):
        original = json.loads(json.dumps(self.manifest))
        for name in ('../escape.png', '/absolute.png', 'a\\b', 'a/../b',
                     'C:/file', 'CON', 'a./file', 'code.gml', 'CODE.GML'):
            with self.subTest(name=name):
                self.manifest = json.loads(json.dumps(original))
                self.manifest['files'][0]['path'] = name
                self.save_manifest()
                with self.assertRaises(tool.RestoreError):
                    self.restore()
        self.assertFalse((self.root / 'escape.png').exists())

    def test_symlink_directory_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.project / 'sprites').symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows does not grant this account symbolic-link creation.')
            raise
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'NTFS directory junctions are Windows-specific')
    def test_junction_cannot_redirect_assets_outside_project(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.project / 'sprites'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(link.rmdir)
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertEqual(list(outside.iterdir()), [])

    def test_mismatched_bundle_rejected(self):
        self.manifest['bundle_sha256'] = '0' * 64
        write_json(self.project / 'asset-inputs.json', self.manifest)
        with self.assertRaises(tool.RestoreError):
            self.restore()

    def test_file_blocking_destination_directory_rejected_before_writes(self):
        (self.project / 'sounds').write_bytes(b'keep this file')
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'sprites').exists())
        self.assertEqual((self.project / 'sounds').read_bytes(), b'keep this file')

    def test_invalid_blob_description_rejected(self):
        self.description['blobs'][sha(self.asset)] = None
        self.save_manifest()
        with self.assertRaises(tool.RestoreError):
            self.restore()

    def test_overlapping_payload_ranges_rejected(self):
        self.description['blobs'][sha(b'overlap')] = {'size': 10, 'offset': 5}
        self.save_manifest()
        with self.assertRaises(tool.RestoreError):
            self.restore()

    def test_missing_code_is_not_silently_replaced(self):
        (self.project / 'code.gml').unlink()
        with self.assertRaises(tool.RestoreError):
            self.restore()
        self.assertFalse((self.project / 'code.gml').exists())
        self.assertFalse((self.project / 'sprites').exists())

    def test_unsafe_or_duplicate_zip_entries_rejected(self):
        for names in (['../data.win'], ['data.win', 'DATA.WIN']):
            with self.subTest(names=names):
                with zipfile.ZipFile(self.original, 'w') as z:
                    for name in names:
                        z.writestr(name, self.base)
                with self.assertRaises(tool.RestoreError):
                    self.restore()

    def test_interrupted_restore_resumes_without_overwriting(self):
        self.restore()
        (self.project / 'sounds/music/music').unlink()
        self.assertEqual(self.restore()['restored'], 1)

    def test_check_is_read_only_and_reports_changed_assets(self):
        before = sorted(p.relative_to(self.project).as_posix() for p in self.project.rglob('*'))
        result = tool.inspect_project(self.project)
        self.assertEqual(len(result['missing_assets']), 3)
        self.assertEqual(before, sorted(p.relative_to(self.project).as_posix() for p in self.project.rglob('*')))


class ExportPolicyTests(unittest.TestCase):
    def test_private_binary_inputs_are_not_exported_as_assets(self):
        for name in ('options/android/signing.jks', 'datafiles/private.keystore',
                     'extensions/runtime/proprietary.so', 'sounds/music/private.bin'):
            with self.subTest(name=name), self.assertRaises(tool.RestoreError):
                builder.classify(name)

    def test_known_build_assets_and_source_keep_distinct_roles(self):
        self.assertEqual(builder.classify('datafiles/lang/japanese.ini'), 'asset')
        self.assertEqual(builder.classify('sounds/music/music'), 'asset')
        self.assertEqual(builder.classify('scripts/display/display.gml'), 'text')
        self.assertEqual(builder.classify('fonts/example/LICENSE.txt'), 'text')
        self.assertEqual(builder.classify('notes/import/import.txt'), 'private-note')
        self.assertEqual(builder.classify('.git/config'), 'excluded')


if __name__ == '__main__':
    unittest.main()
