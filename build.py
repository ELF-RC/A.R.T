#!/usr/bin/env python3
"""Build an A.R.T release without touching user project directories."""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / '.art-build'
DIST_DIR = ROOT / '.art-dist'
RELEASE_DIR = ROOT / '.art-release'
ARCHIVE = ROOT / 'A.R.T-linux.zip'


def remove_generated_path(path: Path) -> None:
    """Remove only a build artifact path owned by this script."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def copy_release_resources(release_dir: Path) -> None:
    binary_source = ROOT / 'local' / 'bin' / 'Linux' / 'x86_64'
    binary_target = release_dir / 'local' / 'bin' / 'Linux' / 'x86_64'
    if binary_source.is_dir():
        shutil.copytree(binary_source, binary_target, dirs_exist_ok=True)

    for subdir in ('etc', 'set'):
        source = ROOT / 'local' / subdir
        target = release_dir / 'local' / subdir
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)

    for filename in ('setting.ini', 'LICENSE', 'README.md'):
        source = ROOT / filename
        if source.is_file():
            shutil.copy2(source, release_dir / filename)


def zip_release(release_dir: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(release_dir.rglob('*')):
            if source.is_file():
                archive.write(source, source.relative_to(release_dir))
    print(f'Pack Zip Done: {archive_path}')


def main() -> None:
    print('Building...')
    remove_generated_path(BUILD_DIR)
    remove_generated_path(DIST_DIR)
    remove_generated_path(RELEASE_DIR)

    import PyInstaller.__main__

    PyInstaller.__main__.run([
        str(ROOT / 'run.py'),
        '--onefile',
        '--name', 'run',
        '--distpath', str(DIST_DIR),
        '--workpath', str(BUILD_DIR),
        '--specpath', str(BUILD_DIR),
        '--exclude-module', 'numpy',
    ])

    built_executable = DIST_DIR / 'run'
    if not built_executable.is_file():
        raise RuntimeError(f'PyInstaller did not create {built_executable}')

    release_dir = RELEASE_DIR
    release_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_executable, release_dir / 'run')
    os.chmod(release_dir / 'run', 0o755)
    copy_release_resources(release_dir)
    zip_release(release_dir, ARCHIVE)

    # The root source tree and any user-created DNA_* projects are intentionally untouched.
    remove_generated_path(BUILD_DIR)
    remove_generated_path(DIST_DIR)
    remove_generated_path(RELEASE_DIR)
    print('Build Done!')


if __name__ == '__main__':
    main()
