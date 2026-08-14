#!/usr/bin/env python3
import os
import shutil
import zipfile
from pip._internal.cli.main import main as _main


def zip_folder(folder_path, local, name):
    abs_folder_path = os.path.abspath(folder_path)
    zip_file_path = os.path.join(local, name)
    with zipfile.ZipFile(zip_file_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(abs_folder_path):
            for file in files:
                if file == name:
                    continue
                file_path = os.path.join(root, file)
                if ".git" in file_path:
                    continue
                print(f"Adding: {file_path}")
                archive.write(file_path, os.path.relpath(file_path, abs_folder_path))
    print("Pack Zip Done!")


if __name__ == '__main__':
    local = os.getcwd()
    name = 'A.R.T-linux.zip'

    print("Building...")
    import PyInstaller.__main__
    PyInstaller.__main__.run([
        'run.py',
        '-F',
        '--exclude-module',
        'numpy'
    ])

    # Move built executable
    if os.path.exists(os.path.join(local, 'dist', 'run')):
        shutil.move(os.path.join(local, 'dist', 'run'), local)

    # Copy local/bin/Linux/x86_64 binaries into dist
    dist_bin = os.path.join(local, 'dist', 'local', 'bin', 'Linux', 'x86_64')
    os.makedirs(dist_bin, exist_ok=True)
    src_bin = os.path.join(local, 'local', 'bin', 'Linux', 'x86_64')
    if os.path.isdir(src_bin):
        shutil.copytree(src_bin, dist_bin, dirs_exist_ok=True)

    # Copy etc and set
    for subdir in ['etc', 'set']:
        src_sub = os.path.join(local, 'local', subdir)
        dst_sub = os.path.join(local, 'dist', 'local', subdir)
        if os.path.isdir(src_sub):
            shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)

    # Copy static files
    for f in ['setting.ini', 'LICENSE']:
        src = os.path.join(local, f)
        dst = os.path.join(local, 'dist', f)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)

    # Set executable permissions
    for root, _, files in os.walk(local):
        for i in files:
            fpath = os.path.join(root, i)
            if os.path.isfile(fpath) and not fpath.endswith('.py') and '.git' not in fpath:
                print(f"Chmod {fpath}")
                os.chmod(fpath, 0o755)

    # Clean up build artifacts from project root
    for item in os.listdir(local):
        if item in ('dist', 'run', 'local', 'LICENSE',
                     'build.py', 'run.py', 'setup.sh', 'requirements.txt', 'README.md', '.git', '.github', 'pys'):
            continue
        fpath = os.path.join(local, item)
        if os.path.isdir(fpath):
            shutil.rmtree(fpath, ignore_errors=True)
        else:
            os.remove(fpath)

    zip_folder('.', local, name)
