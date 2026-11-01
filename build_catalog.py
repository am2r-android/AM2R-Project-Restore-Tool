#!/usr/bin/env python3
"""Build an offline catalog for checksum-verified published game source ZIPs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

from build_bundle import ORIGINAL, record, write_json
from restore_project import (CHUNK, TOOL_NAME, TOOL_VERSION, RestoreError, check_file, digest_file,
                             find_xdelta, original_zip, safe_relative,
                             validate_project_manifest)

NOTE = (b'The original local import report is excluded from this developer project.\n'
        b'See the source archive build instructions and the asset restoration documentation.\n')


def read_export(path, release):
    if digest_file(path) != release['sha256']:
        raise RestoreError('Source archive checksum differs: ' + path.name)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = set()
        for entry in entries:
            safe_relative(entry.filename.rstrip('/'))
            name = entry.filename.rstrip('/').casefold()
            if name in names or stat.S_ISLNK(entry.external_attr >> 16):
                raise RestoreError('Duplicate paths or links in source ZIP.')
            names.add(name)
        manifests = [e.filename for e in entries if e.filename.endswith('/asset-inputs.json')]
        if len(manifests) != 1:
            raise RestoreError('Source ZIP must contain one project manifest.')
        manifest_name = manifests[0]
        prefix = manifest_name.removesuffix('asset-inputs.json')
        raw = archive.read(manifest_name)
        manifest = json.loads(raw)
        if manifest.get('schema') not in (1, 2) or manifest.get('edition') not in ('standard', 'dual', 'dual-screen'):
            raise RestoreError('Unsupported published project manifest.')
        source = []
        for entry in entries:
            if entry.is_dir() or entry.filename == manifest_name:
                continue
            if not entry.filename.startswith(prefix):
                raise RestoreError('Source ZIP has files outside its project folder.')
            source.append(record(entry.filename[len(prefix):], archive.read(entry)))
        source.sort(key=lambda row: row['path'])
        if manifest['schema'] == 2 and source != sorted(manifest['source_files'], key=lambda row: row['path']):
            raise RestoreError('Published source files do not match their manifest.')
    assets, omitted, notes, replacements = [], [], [], {}
    for row in manifest['files']:
        name = safe_relative(row['path'])
        if name == '.suite-generated':
            omitted.append(name)
        elif manifest['schema'] == 1 and name.startswith('notes/') and name.endswith('.txt'):
            replacement = record(name, NOTE)
            assets.append(replacement)
            replacements[replacement['sha256']] = NOTE
            notes.append(name)
        else:
            assets.append(row)
    description = {'schema': 2, 'edition': 'dual-screen' if manifest['edition'] == 'dual' else manifest['edition'],
                   'snapshot': release['tag'], 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                   'bundle_sha256': '0' * 64, 'source_files': source, 'files': assets,
                   'omitted_inputs': omitted, 'replacement_notes': notes,
                   'archive': {key: release[key] for key in ('tag', 'name', 'sha256', 'url')}}
    # Validate paths and collisions without extracting any source files.
    with tempfile.TemporaryDirectory() as directory:
        validate_project_manifest(Path(directory), description)
    return description, replacements


def find_assets(required, donors, generated):
    sources = dict(generated)
    by_path = {}
    for checksum, row in required.items():
        by_path.setdefault(row['path'], set()).add(checksum)
    checked = set()
    for root in donors:
        for name, checksums in by_path.items():
            if checksums <= sources.keys():
                continue
            path = root / name
            if not path.is_file() or path.is_symlink():
                continue
            info = path.stat()
            key = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
            if key in checked or info.st_size not in {required[h]['size'] for h in checksums}:
                continue
            checked.add(key)
            checksum = digest_file(path)
            if checksum in required:
                sources.setdefault(checksum, path)
    missing = set(required) - sources.keys()
    if missing:
        raise RestoreError('Missing %d exact asset versions. First: %s' % (len(missing), required[sorted(missing)[0]]['path']))
    return sources


def build(archives, releases, donors, original, output):
    descriptions, replacements, required = [], {}, {}
    for release in releases:
        name = safe_relative(release['name'])
        description, generated = read_export(archives / name, release)
        descriptions.append(description)
        replacements.update(generated)
        for row in description['files']:
            required.setdefault(row['sha256'], row)
    sources = find_assets(required, donors, replacements)
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / 'bundle'
    (bundle / 'projects').mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='asset-catalog-') as directory:
        work = Path(directory)
        with original_zip(original, ORIGINAL, work) as (archive, prefix, datawin):
            originals = {}
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.startswith(prefix):
                    continue
                name = entry.filename[len(prefix):]
                if name == 'data.win':
                    continue
                with archive.open(entry) as stream:
                    h = hashlib.file_digest(stream, 'sha256').hexdigest()
                originals[(entry.file_size, h)] = name
            payload = work / 'assets.bin'
            blobs, offset = {}, 0
            with payload.open('xb') as stream:
                for checksum, row in sorted(required.items()):
                    member = originals.get((row['size'], checksum))
                    if member:
                        blobs[checksum] = {'size': row['size'], 'member': member}
                        continue
                    blobs[checksum] = {'size': row['size'], 'offset': offset}
                    source = sources[checksum]
                    data = source if isinstance(source, bytes) else source.read_bytes()
                    if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != checksum:
                        raise RestoreError('Donor changed while building: ' + row['path'])
                    stream.write(data)
                    offset += len(data)
            patch = bundle / 'assets.xdelta'
            env = dict(os.environ)
            env.pop('XDELTA', None)
            print('Encoding %d unique assets for %d public archives.' % (len(blobs), len(descriptions)), flush=True)
            subprocess.run([find_xdelta(), '-e', '-9', '-D', '-A', '-S', 'none', '-s', str(datawin), str(payload), str(patch)], check=True, env=env)
            description = {'schema': 1, 'source': ORIGINAL,
                           'patch': {'path': 'assets.xdelta', 'size': patch.stat().st_size, 'sha256': digest_file(patch)},
                           'payload': {'size': offset, 'sha256': digest_file(payload)}, 'blobs': blobs}
            check = work / 'check.bin'
            subprocess.run([find_xdelta(), '-d', '-D', '-R', '-s', str(datawin), str(patch), str(check)], check=True, env=env)
            if not check_file(check, description['payload']):
                raise RestoreError('Catalog patch failed round-trip verification.')
            write_json(bundle / 'manifest.json', description)
    bundle_hash = digest_file(bundle / 'manifest.json')
    index = {'schema': 1, 'tool_version': TOOL_VERSION, 'bundle_sha256': bundle_hash, 'projects': {}}
    supported = [TOOL_NAME + ' ' + TOOL_VERSION, '',
                 'Supported published GameMaker source archives (both editions):',
                 'https://github.com/am2r-android/AM2R-Autopatcher-Android/releases', '']
    for description in descriptions:
        checksum = description['manifest_sha256']
        description['bundle_sha256'] = bundle_hash
        description['compatible_releases'] = sorted({d['snapshot'] for d in descriptions if d['manifest_sha256'] == checksum})
        entry_id = description['archive']['sha256']
        path = bundle / 'projects' / (entry_id + '.json')
        # Identical archives can be attached to multiple releases.
        if path.exists():
            prior = json.loads(path.read_text())
            if (prior['files'], prior['source_files']) != (description['files'], description['source_files']):
                raise RestoreError('Conflicting source archive records.')
        else:
            write_json(path, description)
            entry = {'path': 'projects/' + path.name, 'size': path.stat().st_size, 'sha256': digest_file(path)}
            index['projects'].setdefault(checksum, []).append(entry)
        supported.extend([description['archive']['name'], '  ' + description['archive']['url'],
                          '  SHA-256: ' + entry_id])
    supported.extend(['', 'Earlier repository releases publish the Autopatcher code, not GameMaker game-source archives.',
                      'Autopatcher source archives and GitHub automatic repository snapshots are not game projects.',
                      'Future game releases require a catalog update when their source manifest is new.',
                      'The tool version is independent of game versions. Restoration never upgrades game code.',
                      'Legacy local import reports use a clean note; internal build markers are omitted.'])
    write_json(bundle / 'catalog.json', index)
    (output / 'SUPPORTED-RELEASES.txt').write_text('\n'.join(supported) + '\n', encoding='utf-8')
    write_json(output / 'catalog-inputs.json', releases)
    return {'tool_version': TOOL_VERSION, 'archives': len(descriptions), 'manifests': len(index['projects']),
            'unique_assets': len(required), 'patch_bytes': patch.stat().st_size,
            'catalog_sha256': digest_file(bundle / 'catalog.json'), 'bundle_sha256': bundle_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--releases', type=Path, required=True, help='Reviewed public archive names, URLs and SHA-256 records')
    parser.add_argument('--donor', type=Path, required=True, action='append', help='Complete matching local source; repeat for older assets')
    parser.add_argument('--zip', type=Path, dest='original', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.archives, json.loads(args.releases.read_text()), args.donor, args.original, args.output), indent=2))


if __name__ == '__main__':
    main()
