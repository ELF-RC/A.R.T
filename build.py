#!/usr/bin/env python3
"""Build an A.R.T release without touching user project directories."""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / '.art-build'
DIST_DIR = ROOT / '.art-dist'
RELEASE_DIR = ROOT / '.art-release'
ARCHIVE = ROOT / 'A.R.T-Linux-amd64.zip'


def _log(step: str, msg: str) -> None:
    print(f'  [{step}] {msg}')


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


def zip_release(release_dir: Path, archive_path: Path) -> int:
    if archive_path.exists():
        archive_path.unlink()
    count = 0
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(release_dir.rglob('*')):
            if source.is_file():
                archive.write(source, source.relative_to(release_dir))
                count += 1
    return count


def main() -> None:
    print(f'\n{"=" * 40}\n{" " * 11}A.R.T Builder{" " * 15}\n{"=" * 40}')

    # Step 1: Clean
    _log('1/4', 'Cleaning build artifacts...')
    for path in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        remove_generated_path(path)

    # Step 2: PyInstaller (verbose output goes to build-call.log)
    _log('2/4', 'Compiling with PyInstaller...')
    import subprocess

    log_file = ROOT / 'build-call.log'
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller',
         str(ROOT / 'run.py'),
         '--onefile',
         '--name', 'run',
         '--distpath', str(DIST_DIR),
         '--workpath', str(BUILD_DIR),
         '--specpath', str(BUILD_DIR),
         '--exclude-module', 'numpy'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_file.write_text(result.stdout or '', encoding='utf-8')

    built_executable = DIST_DIR / 'run'
    if not built_executable.is_file():
        print(f'\n  [ERROR] PyInstaller failed. See {log_file.name} for details.', file=sys.stderr)
        if result.stdout:
            for line in result.stdout.splitlines():
                if 'ERROR' in line or 'CRITICAL' in line:
                    print(f'  {line}', file=sys.stderr)
        sys.exit(1)

    # Step 3: Assemble release
    _log('3/4', 'Assembling release directory...')
    release_dir = RELEASE_DIR
    release_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_executable, release_dir / 'run')
    os.chmod(release_dir / 'run', 0o755)
    copy_release_resources(release_dir)

    # Step 4: Archive
    _log('4/4', f'Packing {ARCHIVE.name}...')
    count = zip_release(release_dir, ARCHIVE)
    size_mb = ARCHIVE.stat().st_size / (1024 * 1024)
    _log('END', f'Build Completed:{count} files {size_mb:.1f} MB')

    # Cleanup
    for path in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        remove_generated_path(path)

    print(f'{"=" * 40}\n ')

if __name__ == '__main__':
    main()
