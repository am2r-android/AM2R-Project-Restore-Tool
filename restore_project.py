#!/usr/bin/env python3
"""Restore matching modern AM2R project assets from an owned AM2R 1.1 ZIP.

The verified-input and xdelta workflow derives from the MIT-licensed AM2R Android
Autopatcher. This separate developer utility never downloads a game or rewrites
project code. See LICENSE and README.txt beside this script.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

CHUNK = 1024 * 1024
MAX_FILE = 8 * 1024 * 1024 * 1024
MAX_JSON = 32 * CHUNK
TOOL_VERSION = '0.2.0'
TOOL_NAME = 'AM2R Project Restore Tool'
EXECUTABLE_NAME = 'AM2R-PRT'


class RestoreError(Exception):
    """Input or destination cannot safely be used for this restoration."""


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(CHUNK), b''):
            h.update(data)
    return h.hexdigest()


def safe_relative(name):
    if not isinstance(name, str) or not name or any(c in name for c in ('\\', ':', '\0')):
        raise RestoreError('Invalid relative file name.')
    parts = name.split('/')
    for part in parts:
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or
                any(ord(c) < 32 for c in part) or
                re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)):
            raise RestoreError('Unsafe relative file name: ' + name)
    return name


def is_redirect(path):
    """Reject links, including NTFS junctions which are not symbolic links."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, 'st_file_attributes', 0) &
                 getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))


def local_path(root, name):
    path = root
    parts = safe_relative(name).split('/')
    for index, part in enumerate(parts):
        path = path / part
        if is_redirect(path):
            raise RestoreError('Links and redirected folders are not supported: ' + name)
        if index < len(parts) - 1 and path.exists() and not path.is_dir():
            raise RestoreError('A file blocks a project directory: ' + name)
    if path.exists() and not path.is_file():
        raise RestoreError('Expected a regular file: ' + name)
    return path


def read_json(path):
    if is_redirect(path) or not path.is_file() or path.stat().st_size > MAX_JSON:
        raise RestoreError('Missing or oversized manifest: ' + path.name)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as error:
        raise RestoreError('Invalid JSON: ' + path.name) from error
    if not isinstance(value, dict):
        raise RestoreError('Manifest must be an object: ' + path.name)
    return value


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def check_record(row):
    if (not isinstance(row, dict) or type(row.get('size')) is not int or
            not 0 <= row['size'] <= MAX_FILE or not valid_hash(row.get('sha256'))):
        raise RestoreError('Invalid size or checksum in manifest.')


def check_file(path, row):
    return path.is_file() and path.stat().st_size == row['size'] and digest_file(path) == row['sha256']


def validate_project_manifest(project, manifest):
    if (manifest.get('schema') != 2 or manifest.get('edition') not in ('standard', 'dual-screen') or
            not valid_hash(manifest.get('bundle_sha256'))):
        raise RestoreError('This project needs the matching modern asset-restoration manifest.')
    names = set()
    for key in ('source_files', 'files'):
        rows = manifest.get(key)
        if not isinstance(rows, list) or len(rows) > 100000:
            raise RestoreError('Invalid project file list.')
        for row in rows:
            check_record(row)
            name = safe_relative(row.get('path'))
            folded = name.casefold()
            if folded in names or folded == 'asset-inputs.json':
                raise RestoreError('Overlapping or duplicate file name: ' + name)
            names.add(folded)
            local_path(project, name)
    for name in names:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in names for i in range(1, len(parts))):
            raise RestoreError('A file is also used as a directory: ' + name)
    if 'AM2R-project.yyp' not in {r['path'] for r in manifest['source_files']}:
        raise RestoreError('Manifest has no GameMaker project resource.')


def catalog_project(project, bundle):
    """Select a verified asset set, then the nearest published code reference."""
    catalog = read_json(local_path(bundle, 'catalog.json'))
    if (catalog.get('schema') != 1 or not valid_hash(catalog.get('bundle_sha256')) or
            not isinstance(catalog.get('projects'), dict)):
        raise RestoreError('Unsupported restoration catalog.')
    manifest_hash = digest_file(local_path(project, 'asset-inputs.json'))
    candidates = catalog['projects'].get(manifest_hash)
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
        raise RestoreError('This source manifest is not in the bundled catalog. Use an unmodified published manifest or an updated tool.')
    references, actual = [], {}
    for record in candidates:
        check_record(record)
        path = local_path(bundle, record.get('path'))
        if not check_file(path, record):
            raise RestoreError('A restoration catalog entry is missing or damaged.')
        description = read_json(path)
        if (description.get('manifest_sha256') != manifest_hash or
                description.get('bundle_sha256') != catalog['bundle_sha256']):
            raise RestoreError('Catalog entry does not match this project and asset bundle.')
        validate_project_manifest(project, description)
        matches = 0
        for row in description['source_files']:
            name = row['path']
            if name not in actual:
                source = local_path(project, name)
                actual[name] = (source.stat().st_size, digest_file(source)) if source.is_file() else None
            if actual[name] == (row['size'], row['sha256']):
                matches += 1
        # Source edits remain allowed. Shared manifests always refer to the same
        # asset set; this ranking only chooses the reference used in the report.
        references.append((matches, description))
    return max(references, key=lambda item: item[0])[1]


def load_project(project, bundle=None):
    project = Path(project).absolute()
    if is_redirect(project) or not project.is_dir():
        raise RestoreError('Select an extracted GameMaker project folder.')
    manifest = read_json(local_path(project, 'asset-inputs.json'))
    bundle = Path(bundle) if bundle is not None else application_dir() / 'bundle'
    if (bundle / 'catalog.json').exists():
        manifest = catalog_project(project, bundle)
    validate_project_manifest(project, manifest)
    return project, manifest


def inspect_project(project, bundle=None):
    project, manifest = load_project(project, bundle)
    return inspect_files(project, manifest)


def inspect_files(project, manifest):
    result = {'edition': manifest['edition'], 'snapshot': manifest.get('snapshot'),
              'missing_assets': [], 'different_assets': [],
              'missing_source': [], 'modified_source': [], 'matching_assets': 0,
              'compatible_releases': manifest.get('compatible_releases', []),
              'omitted_inputs': manifest.get('omitted_inputs', []),
              'replacement_notes': manifest.get('replacement_notes', [])}
    for key, missing, different in (('source_files', 'missing_source', 'modified_source'),
                                    ('files', 'missing_assets', 'different_assets')):
        for row in manifest[key]:
            path = local_path(project, row['path'])
            if not path.exists():
                result[missing].append(row['path'])
            elif not check_file(path, row):
                result[different].append(row['path'])
            elif key == 'files':
                result['matching_assets'] += 1
    return result


def load_bundle(bundle, project_manifest):
    bundle = Path(bundle).resolve()
    path = local_path(bundle, 'manifest.json')
    if not path.is_file() or digest_file(path) != project_manifest['bundle_sha256']:
        raise RestoreError('The restoration bundle does not match this source project.')
    description = read_json(path)
    if description.get('schema') != 1 or not isinstance(description.get('blobs'), dict):
        raise RestoreError('Unsupported restoration bundle.')
    for key in ('source', 'patch', 'payload'):
        check_record(description.get(key))
    if description['patch'].get('path') != 'assets.xdelta':
        raise RestoreError('Invalid restoration patch name.')
    segments = []
    for checksum, blob in description['blobs'].items():
        if not isinstance(blob, dict):
            raise RestoreError('Invalid asset description in bundle.')
        check_record(dict(blob, sha256=checksum))
        if ('offset' in blob) == ('member' in blob):
            raise RestoreError('Each asset needs exactly one source.')
        if 'offset' in blob:
            offset = blob['offset']
            if type(offset) is not int or offset < 0 or offset + blob['size'] > description['payload']['size']:
                raise RestoreError('Asset range exceeds the reconstructed payload.')
            segments.append((offset, blob['size']))
        else:
            safe_relative(blob['member'])
    position = 0
    for offset, length in sorted(segments):
        if offset != position:
            raise RestoreError('Payload ranges overlap or leave unexpected bytes.')
        position += length
    if position != description['payload']['size']:
        raise RestoreError('Payload length does not match its assets.')
    for row in project_manifest['files']:
        blob = description['blobs'].get(row['sha256'])
        if blob is None or blob['size'] != row['size']:
            raise RestoreError('Bundle is missing an asset required by the project.')
    patch = local_path(bundle, description['patch']['path'])
    if not check_file(patch, description['patch']):
        raise RestoreError('Restoration patch is missing or damaged.')
    return description, patch


def copy_checked(source, target, row):
    h, size = hashlib.sha256(), 0
    with target.open('xb') as output:
        while True:
            data = source.read(min(CHUNK, row['size'] - size + 1))
            if not data:
                break
            size += len(data)
            if size > row['size']:
                raise RestoreError('An input exceeds its expected size.')
            h.update(data)
            output.write(data)
    if size != row['size'] or h.hexdigest() != row['sha256']:
        raise RestoreError('An input does not match the required original copy.')


@contextmanager
def original_zip(path, expected, work):
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise RestoreError('Select the original AM2R 1.1 ZIP.') from error
    with archive:
        names, candidates = set(), []
        for entry in archive.infolist():
            name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            safe_relative(name)
            if name.casefold() in names or stat.S_ISLNK(entry.external_attr >> 16):
                raise RestoreError('Duplicate names or links in the original ZIP.')
            names.add(name.casefold())
            if not entry.is_dir() and name.split('/')[-1].lower() == 'data.win':
                candidates.append(entry)
        if len(candidates) != 1 or candidates[0].file_size != expected['size']:
            raise RestoreError('The ZIP must contain the original, unmodified AM2R 1.1 data.win.')
        entry = candidates[0]
        datawin = work / 'data.win'
        with archive.open(entry) as stream:
            copy_checked(stream, datawin, expected)
        prefix = entry.filename.rpartition('/')[0]
        yield archive, (prefix + '/' if prefix else ''), datawin


def application_dir():
    """Keep the restoration bundle beside the app, outside frozen Python files."""
    path = sys.executable if getattr(sys, 'frozen', False) else __file__
    return Path(path).resolve().parent


def find_xdelta():
    executable = 'xdelta3.exe' if sys.platform == 'win32' else 'xdelta3'
    bundled = application_dir() / 'tools' / executable
    if bundled.is_file():
        return str(bundled)
    if getattr(sys, 'frozen', False):
        raise RestoreError('The bundled xdelta3 decoder is missing. Extract the complete application archive again.')
    found = shutil.which('xdelta3') or shutil.which('xdelta3.exe')
    if not found:
        raise RestoreError('Install xdelta3 and make it available on PATH, then retry. See README.txt.')
    return found


def decode_payload(xdelta, datawin, patch, target):
    env = dict(os.environ)
    env.pop('XDELTA', None)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    result = subprocess.run([xdelta, '-d', '-D', '-R', '-s', str(datawin), str(patch), str(target)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace', env=env, creationflags=flags)
    if result.returncode:
        raise RestoreError('xdelta3 could not rebuild the assets: ' + result.stderr.strip()[-1500:])


def restore(project, original, bundle, progress=lambda message: None):
    project, manifest = load_project(project, bundle)
    status = inspect_files(project, manifest)
    progress('Asset set: %s; %s source reference.' % (manifest['edition'], manifest.get('snapshot', 'matching')))
    if status['missing_source']:
        raise RestoreError('Source files are missing; extract the matching source archive again. First: ' + status['missing_source'][0])
    if status['different_assets']:
        raise RestoreError('Existing asset differs and was preserved: ' + status['different_assets'][0])
    status['restored'] = 0
    if not status['missing_assets']:
        return status
    description, patch = load_bundle(bundle, manifest)
    missing = set(status['missing_assets'])
    pending = [r for r in manifest['files'] if r['path'] in missing]
    needed = {r['sha256'] for r in pending}
    required = sum(r['size'] for r in pending) + description['payload']['size'] + description['source']['size']
    required += sum(b['size'] for h, b in description['blobs'].items() if h in needed and 'member' in b)
    required += max(32 * CHUNK, len(pending) * 8192)
    if shutil.disk_usage(project).free < required:
        raise RestoreError('Not enough free space. Allow about %.1f GiB for this restoration.' % (required / 1024**3 + .1))
    xdelta = find_xdelta()
    with tempfile.TemporaryDirectory(prefix='.asset-restore-', dir=project) as temporary:
        work = Path(temporary)
        progress('Checking your original AM2R 1.1 ZIP…')
        with original_zip(original, description['source'], work) as (archive, prefix, datawin):
            external = {}
            for checksum in sorted(needed):
                blob = description['blobs'][checksum]
                if 'member' not in blob:
                    continue
                name = prefix + blob['member']
                try:
                    entry = archive.getinfo(name)
                except KeyError as error:
                    raise RestoreError('Original ZIP is missing ' + blob['member']) from error
                if entry.file_size != blob['size']:
                    raise RestoreError('Original ZIP file differs: ' + blob['member'])
                path = work / checksum
                with archive.open(entry) as stream:
                    copy_checked(stream, path, dict(blob, sha256=checksum))
                external[checksum] = path
            progress('Rebuilding and verifying project assets…')
            payload = work / 'assets.bin'
            decode_payload(xdelta, datawin, patch, payload)
            if not check_file(payload, description['payload']):
                raise RestoreError('Reconstructed asset payload failed verification.')
            with payload.open('rb') as stream:
                for checksum, blob in description['blobs'].items():
                    if 'offset' not in blob:
                        continue
                    stream.seek(blob['offset'])
                    remaining, h = blob['size'], hashlib.sha256()
                    while remaining:
                        data = stream.read(min(CHUNK, remaining))
                        if not data:
                            raise RestoreError('Asset payload is truncated.')
                        remaining -= len(data)
                        h.update(data)
                    if h.hexdigest() != checksum:
                        raise RestoreError('An asset in the payload failed verification.')
                # Every input and existing destination has passed before writing assets.
                # A fresh temporary file per path avoids linking duplicate sprite layers
                # together; developers must be able to edit them independently.
                for index, row in enumerate(pending):
                    target = local_path(project, row['path'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    local_path(project, row['path'])
                    blob = description['blobs'][row['sha256']]
                    prepared = work / 'next-input'
                    if 'member' in blob:
                        shutil.copyfile(external[row['sha256']], prepared)
                    else:
                        stream.seek(blob['offset'])
                        with prepared.open('xb') as output:
                            remaining = blob['size']
                            while remaining:
                                data = stream.read(min(CHUNK, remaining))
                                if not data:
                                    raise RestoreError('Asset payload changed while reading.')
                                output.write(data)
                                remaining -= len(data)
                    if not check_file(prepared, row):
                        raise RestoreError('Prepared asset failed verification: ' + row['path'])
                    try:
                        os.link(prepared, target)
                    except FileExistsError as error:
                        raise RestoreError('A destination appeared during restoration; preserved: ' + row['path']) from error
                    prepared.unlink()
                    status['restored'] += 1
                    if index % 250 == 0 or index + 1 == len(pending):
                        progress('Restored %d / %d assets.' % (index + 1, len(pending)))
    status['matching_assets'] += status['restored']
    status['missing_assets'] = []
    return status


def gui():
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    window = tk.Tk()
    window.title(TOOL_NAME + ' ' + TOOL_VERSION)
    window.geometry('690x400')
    window.minsize(580, 350)
    frame = ttk.Frame(window, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Restore assets for published AM2R GameMaker projects.', wraplength=640).pack(anchor='w')
    ttk.Label(frame, text='Extract a Standard or Dual Screen game-source ZIP. The catalog detects its asset set automatically; see SUPPORTED-RELEASES.txt.', wraplength=640).pack(anchor='w', pady=(4, 12))
    original, project = tk.StringVar(), tk.StringVar()
    for label, variable, directory in [('Original AM2R 1.1 ZIP', original, False), ('Extracted project folder', project, True)]:
        ttk.Label(frame, text=label).pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 8))
        ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
        def browse(value=variable, folder=directory):
            chosen = filedialog.askdirectory() if folder else filedialog.askopenfilename(filetypes=[('ZIP files', '*.zip')])
            if chosen:
                value.set(chosen)
        ttk.Button(row, text='Browse…', command=browse).pack(side='right', padx=(8, 0))
    status = tk.StringVar(value='Code edits and existing files are preserved.')
    ttk.Label(frame, textvariable=status, wraplength=640).pack(anchor='w', pady=10)
    messages, busy = queue.Queue(), [False]
    def start():
        if not original.get() or not project.get():
            messagebox.showerror('Choose inputs', 'Select your original ZIP and extracted project folder.')
            return
        inputs = project.get(), original.get(), application_dir() / 'bundle'
        busy[0] = True
        button.configure(state='disabled')
        def work():
            try:
                result = restore(*inputs, progress=lambda message: messages.put(('progress', message)))
                messages.put(('done', result))
            except (RestoreError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                messages.put(('error', str(error)))
        threading.Thread(target=work, daemon=False).start()
    button = ttk.Button(frame, text='Restore project assets', command=start)
    button.pack(anchor='w')
    def receive():
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == 'progress':
                    status.set(value)
                else:
                    busy[0] = False
                    button.configure(state='normal')
                    if kind == 'done':
                        status.set('Done. %s reference: %d assets restored; %d source edits preserved. Open AM2R-project.yyp in GameMaker.' %
                                   (value['snapshot'], value['restored'], len(value['modified_source'])))
                    else:
                        status.set('Restoration stopped. Existing files were preserved.')
                        messagebox.showerror('Could not restore', value)
        except queue.Empty:
            pass
        window.after(100, receive)
    def close():
        if busy[0]:
            messagebox.showinfo('Restoration running', 'Please wait for the current restoration to finish.')
        else:
            window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    receive()
    window.mainloop()


def main():
    if len(sys.argv) == 1:
        gui()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=TOOL_NAME + ' ' + TOOL_VERSION)
    parser.add_argument('--project', type=Path, required=True, help='Extracted source folder containing asset-inputs.json')
    parser.add_argument('--zip', type=Path, dest='original', help='Your original AM2R 1.1 ZIP')
    parser.add_argument('--bundle', type=Path, default=application_dir() / 'bundle')
    parser.add_argument('--check', action='store_true', help='Read-only completeness and source-edit report')
    parser.add_argument('--report', type=Path, help='Write a new JSON report; existing reports are preserved')
    args = parser.parse_args()
    try:
        if args.report and args.report.exists():
            raise RestoreError('Report already exists; choose a new report filename.')
        if args.check:
            result = inspect_project(args.project, args.bundle)
        else:
            if not args.original:
                parser.error('--zip is required for restoration')
            result = restore(args.project, args.original, args.bundle, progress=print)
        if args.report:
            with args.report.open('x', encoding='utf-8') as output:
                json.dump(result, output, indent=2)
                output.write('\n')
        print(json.dumps(result, indent=2))
        return 1 if result['missing_assets'] or result['different_assets'] or result['missing_source'] else 0
    except (RestoreError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print('Cannot restore:', error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
