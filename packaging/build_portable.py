#!/usr/bin/env python3
"""Build a portable application on its target OS with pinned dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from restore_project import EXECUTABLE_NAME, TOOL_NAME, TOOL_VERSION


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', type=Path, required=True)
    parser.add_argument('--decoder', type=Path, required=True)
    parser.add_argument('--licenses', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new output directory.')
    output.mkdir(parents=True)
    kit = args.kit.resolve()
    decoder = args.decoder.resolve()
    windows = sys.platform == 'win32'
    if not decoder.is_file() or not args.licenses.is_dir():
        parser.error('Supply the verified decoder and its reviewed dependency notices.')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--windowed', '--name', EXECUTABLE_NAME,
               '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
               '--specpath', str(output), str(source / 'restore_project.py')]
    if sys.platform.startswith('linux'):
        # PyInstaller treats XCB as a system dependency. Tk's bundled X11
        # library still needs it on a clean desktop/container installation.
        from PyInstaller.depend.bindepend import resolve_library_path
        xcb = resolve_library_path('libxcb.so.1')
        if not xcb:
            parser.error('The Linux build environment needs libxcb.so.1.')
        command.extend(['--add-binary', xcb + ':.'])
    subprocess.run(command, check=True, cwd=source)
    app = output / 'dist' / EXECUTABLE_NAME
    (app / 'tools').mkdir()
    target = app / 'tools' / ('xdelta3.exe' if windows else 'xdelta3')
    shutil.copy2(decoder, target)
    if not windows:
        target.chmod(0o755)
    shutil.copytree(args.licenses, app / 'licenses')
    shutil.copytree(kit / 'bundle', app / 'bundle')
    for name in ('SUPPORTED-RELEASES.txt', 'catalog-inputs.json'):
        shutil.copy2(kit / name, app / name)
    for name in ('README.txt', 'LICENSE', 'THIRD-PARTY.txt'):
        shutil.copy2(source / name, app / name)
    # Include the matching utility source independently of the game projects.
    with zipfile.ZipFile(app / 'restorer-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('restore_project.py', 'build_bundle.py', 'build_catalog.py', 'README.txt', 'LICENSE', 'THIRD-PARTY.txt'):
            archive.write(source / name, name)
        for folder in ('tests', 'packaging', 'docs'):
            for path in sorted((source / folder).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    archive.write(path, path.relative_to(source))
    versions = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
    info = {'tool_name': TOOL_NAME, 'executable_name': EXECUTABLE_NAME,
            'tool_version': TOOL_VERSION, 'system': platform.system(),
            'architecture': platform.machine(), 'python': platform.python_version(),
            'build_packages': versions.splitlines(), 'decoder_sha256': digest(target),
            'asset_bundle_sha256': digest(app / 'bundle/manifest.json'),
            'catalog_sha256': digest(app / 'bundle/catalog.json')}
    (app / 'BUILD-INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    records = []
    for path in sorted(app.rglob('*')):
        if path.is_file() and not path.is_symlink():
            records.append(digest(path) + '  ' + path.relative_to(app).as_posix())
    (app / 'SHA256SUMS.txt').write_text('\n'.join(records) + '\n', encoding='utf-8')
    print('Portable application:', app, flush=True)


if __name__ == '__main__':
    main()
