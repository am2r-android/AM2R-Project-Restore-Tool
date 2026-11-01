#!/usr/bin/env python3
"""Build a versioned developer source/asset kit from complete local projects.

This maintainer command needs full private source inputs. The generated source
archives contain text only; version-specific binary differences go into xdelta.
Neither complete game assets nor a proprietary GameMaker runtime are bundled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from restore_project import (CHUNK, RestoreError, digest_file, original_zip,
                             safe_relative, find_xdelta, check_file)

ORIGINAL = {'size': 48345822,
            'sha256': '36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3'}
RESOURCE_FOLDERS = {'scripts', 'objects', 'rooms', 'timelines', 'shaders', 'extensions',
                    'options', 'sprites', 'sounds', 'fonts', 'tilesets', 'sequences',
                    'paths', 'animcurves', 'notes'}
CODE_SUFFIXES = {'.gml', '.yy', '.java', '.ext', '.fsh', '.vsh'}
ROOT_TEXT = {'AM2R-project.yyp', 'AM2R-project.resource_order', 'README.txt',
             'SOURCE-MAP.txt', 'CODE-STYLE.txt', '.gitignore', '.gitattributes'}
EXCLUDED = {'.git', 'build', '.gradle', '.cxx', '__pycache__', 'local.properties', '.suite-generated'}


def record(name, data):
    return {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write_json(path, value):
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.write('\n')


def zip_member(archive, name, data):
    safe_relative(name)
    info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def text_data(path):
    data = path.read_bytes()
    data.decode('utf-8-sig')
    if b'\0' in data:
        raise RestoreError('Non-text file in source allowlist: ' + path.name)
    return data


def classify(name):
    parts = Path(safe_relative(name)).parts
    suffix = Path(name).suffix.lower()
    if set(parts) & EXCLUDED or parts[0] == 'docs' or Path(name).suffix == '.md':
        return 'excluded'
    if name in ROOT_TEXT:
        return 'text'
    if parts[0] == 'notes' and name.endswith('.txt'):
        return 'private-note'
    if parts[0] in RESOURCE_FOLDERS:
        if Path(name).suffix in CODE_SUFFIXES or (parts[0] == 'extensions' and name.endswith('.xml')):
            return 'text'
        if Path(name).name == 'LICENSE.txt':
            return 'text'
        if suffix == '.png' or (parts[0] == 'sounds' and (suffix in {'.ogg', '.wav', '.mp3'} or
                (len(parts) == 3 and parts[1] == parts[2]))):
            return 'asset'
    if parts[0] == 'datafiles' and suffix in {'.png', '.ttf', '.ini', '.json', '.txt'}:
        return 'asset'
    raise RestoreError('Unclassified input; review before exporting: ' + name)


def inventory(root, extra):
    text, assets, excluded = {}, {}, []
    folded = set()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RestoreError('Source links are not supported: ' + str(path.relative_to(root)))
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        safe_relative(name)
        if name.casefold() in folded:
            raise RestoreError('Case-colliding source file names: ' + name)
        folded.add(name.casefold())
        kind = classify(name)
        if kind == 'excluded':
            excluded.append(name)
        elif kind == 'text':
            text[name] = text_data(path)
        elif kind == 'private-note':
            text[name] = b'The local import report is excluded from this developer source package.\nSee README.txt and SOURCE-MAP.txt for the current project.\n'
        else:
            assets[name] = {'path': path, 'size': path.stat().st_size, 'sha256': digest_file(path)}
    for name, data in extra.items():
        safe_relative(name)
        if name in text or name in assets:
            raise RestoreError('Additional documentation would replace source: ' + name)
        data.decode('utf-8-sig')
        if b'\0' in data:
            raise RestoreError('Additional documentation is not text: ' + name)
        text[name] = data
    project = json.loads(text['AM2R-project.yyp'])
    available = set(text) | set(assets)
    for resource in project['resources']:
        if resource['id']['path'] not in available:
            raise RestoreError('Missing registered resource: ' + resource['id']['path'])
    for included in project.get('IncludedFiles', []):
        name = included['filePath'] + '/' + included['name']
        if name not in available:
            raise RestoreError('Missing included data file: ' + name)
    return text, assets, excluded


def build(standard, dual_screen, original, output, extra_dir, snapshot):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / 'bundle'
    bundle.mkdir()
    extra = {p.relative_to(extra_dir).as_posix(): text_data(p)
             for p in sorted(extra_dir.rglob('*')) if p.is_file()}
    if not {'BUILDING.txt', 'LICENSE.txt', 'THIRD-PARTY.txt'} <= set(extra):
        raise RestoreError('Supply the reviewed game build instructions and source notices.')
    sources, unique = {}, {}
    for edition, root in [('standard', Path(standard)), ('dual-screen', Path(dual_screen))]:
        text, assets, excluded = inventory(root, extra)
        sources[edition] = text, assets, excluded
        for asset in assets.values():
            unique.setdefault(asset['sha256'], asset)
    with tempfile.TemporaryDirectory(prefix='.build-', dir=output) as temporary:
        work = Path(temporary)
        with original_zip(original, ORIGINAL, work) as (archive, prefix, datawin):
            originals = {}
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.startswith(prefix):
                    continue
                name = entry.filename[len(prefix):]
                if name == 'data.win':
                    continue
                h = hashlib.sha256()
                with archive.open(entry) as stream:
                    for data in iter(lambda: stream.read(CHUNK), b''):
                        h.update(data)
                originals[(entry.file_size, h.hexdigest())] = name
            blobs, offset = {}, 0
            payload = work / 'assets.bin'
            with payload.open('xb') as stream:
                for checksum, row in sorted(unique.items()):
                    member = originals.get((row['size'], checksum))
                    if member:
                        blobs[checksum] = {'size': row['size'], 'member': member}
                    else:
                        blobs[checksum] = {'size': row['size'], 'offset': offset}
                        with row['path'].open('rb') as source:
                            shutil.copyfileobj(source, stream, CHUNK)
                        offset += row['size']
            patch = bundle / 'assets.xdelta'
            env = dict(os.environ)
            env.pop('XDELTA', None)
            print('Encoding %d unique assets, %.1f MiB of rebuilt inputs…' % (len(blobs), offset / CHUNK), flush=True)
            subprocess.run([find_xdelta(), '-e', '-9', '-D', '-A', '-S', 'none', '-s', str(datawin),
                            str(payload), str(patch)], check=True, env=env)
            description = {'schema': 1, 'snapshot': snapshot, 'source': ORIGINAL,
                           'patch': {'path': patch.name, 'size': patch.stat().st_size, 'sha256': digest_file(patch)},
                           'payload': {'size': offset, 'sha256': digest_file(payload)}, 'blobs': blobs}
            # Verify actual decode before constructing project manifests that reference it.
            rebuilt = work / 'check.bin'
            subprocess.run([find_xdelta(), '-d', '-D', '-R', '-s', str(datawin), str(patch), str(rebuilt)],
                           check=True, env=env)
            if not check_file(rebuilt, description['payload']):
                raise RestoreError('Generated restoration patch does not round-trip.')
            write_json(bundle / 'manifest.json', description)
    bundle_hash = digest_file(bundle / 'manifest.json')
    results = {'snapshot': snapshot, 'bundle_sha256': bundle_hash,
               'patch_bytes': patch.stat().st_size, 'unique_assets': len(unique), 'editions': {}}
    for edition, (text, assets, excluded) in sources.items():
        manifest = {'schema': 2, 'edition': edition, 'snapshot': snapshot,
                    'bundle_sha256': bundle_hash,
                    'source_files': [record(name, data) for name, data in sorted(text.items())],
                    'files': [{'path': name, 'size': row['size'], 'sha256': row['sha256']}
                              for name, row in sorted(assets.items())]}
        filename = 'AM2R-project-' + edition + '.zip'
        with zipfile.ZipFile(output / filename, 'x') as archive:
            for name, data in sorted(text.items()):
                zip_member(archive, 'AM2R-project/' + name, data)
            zip_member(archive, 'AM2R-project/asset-inputs.json', (json.dumps(manifest, indent=2) + '\n').encode())
        results['editions'][edition] = {'source_archive': filename, 'sha256': digest_file(output / filename),
                                       'source_files': len(text), 'assets': len(assets)}
    here = Path(__file__).resolve().parent
    for name in ('restore_project.py', 'build_bundle.py', 'README.txt', 'LICENSE', 'THIRD-PARTY.txt'):
        shutil.copyfile(here / name, output / name)
    (output / 'tests').mkdir()
    shutil.copyfile(here / 'tests/test_restore.py', output / 'tests/test_restore.py')
    write_json(output / 'kit.json', results)
    print('Developer kit built:', output, flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('standard', 'dual-screen', 'original', 'output', 'extra-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--snapshot', required=True, help='Version label for this exact source snapshot')
    args = parser.parse_args()
    build(args.standard, args.dual_screen, args.original, args.output, args.extra_dir, args.snapshot)


if __name__ == '__main__':
    main()
