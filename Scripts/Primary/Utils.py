"""Shared utility functions grouped by responsibility.

Sections are intentionally kept in one stable common helper module for the
extract, remake, and primary packages.
"""

import os
import platform
import sys
import shlex
import shutil
import subprocess

from pathlib import Path


class LayoutError(RuntimeError):
    """Base error for invalid project layouts and unsafe paths."""

# ---------------------------------------------------------------------------
# Runtime state and bundled external-tool paths
# ---------------------------------------------------------------------------
SOURCE_ROOT = Path(__file__).resolve().parents[2]
_RESOURCE_ROOT_CANDIDATES = [
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
    SOURCE_ROOT,
    Path.cwd(),
]
ROOT_DIR = next(
    (candidate for candidate in _RESOURCE_ROOT_CANDIDATES
     if candidate is not None and (candidate / "art-res").is_dir()),
    SOURCE_ROOT,
)
PWD_DIR = str(ROOT_DIR) + os.sep


def _bundled_bin_path() -> Path:
    architecture = platform.machine().lower()
    suffix = "arm64" if architecture in {"aarch64", "arm64"} else "amd64"
    candidates = (
        ROOT_DIR / "art-res" / "bin",
        ROOT_DIR / "art-res" / f"bin-{suffix}",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


BIN_PATH = str(_bundled_bin_path()) + os.sep

RED, WHITE, CYAN, YELLOW, MAGENTA, GREEN, BOLD, CLOSE = [
    '\x1b[91m', '\x1b[97m', '\x1b[36m', '\x1b[93m',
    '\x1b[1;35m', '\x1b[1;32m', '\x1b[1m', '\x1b[0m',
]


# Global mutable state shared by the interactive workflow.
class GlobalValue(object):
    JM = False

    def __init__(self):
        self.programs = [
            "cpio", "brotli", "img2simg", "e2fsck", "resize2fs",
            "mke2fs", "e2fsdroid", "mkfs.erofs", "lpmake",
            "extract.erofs", "magiskboot", "avbroot",
        ]

    def __getattr__(self, item):
        return None


V = GlobalValue()


# Runtime setup helpers.
def change_permissions_recursive(path, mode):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(os.path.join(root, d), mode)
        for f in files:
            os.chmod(os.path.join(root, f), mode)
    os.chmod(path, mode)


def _normalize_bin_permissions(path):
    """Set bundled-tool directories to 0777 and regular files to 0755."""
    if os.path.islink(path):
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o777)
        for name in files:
            target = os.path.join(root, name)
            if not os.path.islink(target):
                os.chmod(target, 0o755)
    os.chmod(path, 0o777)


def init_bin_path():
    """Verify BIN_PATH exists and set up PATH + permissions."""
    if not os.path.isdir(BIN_PATH):
        print(f"Run err on: {__import__('platform').system()} {__import__('platform').machine()}")
        import sys
        sys.exit()

    os.environ["PATH"] += os.pathsep + BIN_PATH
    _normalize_bin_permissions(BIN_PATH)

    for prog in V.programs:
        if not shutil.which(prog):
            import sys
            sys.exit(f"[x] Not found: {prog}\n[i] Please install {prog} \n   Or add <{prog}> to {BIN_PATH}")


# Execute one bundled tool while preserving the historical call API.
def call(exe, kz='Y', out=0, shstate=False, sp=0, env=None):
    """Run a command with MIO-compatible argv handling and no implicit shell."""
    del sp
    if isinstance(exe, (list, tuple)):
        cmd = [str(item) for item in exe if item not in (None, '')]
        if kz == 'Y' and cmd and not os.path.isabs(cmd[0]):
            cmd[0] = os.path.join(BIN_PATH, cmd[0])
    elif shstate:
        cmd = f'{BIN_PATH}{exe}' if kz == 'Y' else exe
    else:
        cmd = shlex.split(str(exe))
        if kz == 'Y' and cmd and not os.path.isabs(cmd[0]):
            cmd[0] = os.path.join(BIN_PATH, cmd[0])

    try:
        process = subprocess.Popen(
            cmd,
            shell=shstate,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except OSError as error:
        print(f'> 启动命令失败: {error}')
        return 127

    if process.stdout:
        for line in iter(process.stdout.readline, b''):
            if out == 0:
                print(line.decode('utf-8', 'ignore').strip())
    return process.wait()



# ---------------------------------------------------------------------------
# Ordinary filesystem helpers
# ---------------------------------------------------------------------------
def get_dir_size(ddir, max_=1.06):
    size = 0
    for (root, dirs, files) in os.walk(ddir):
        for name in files:
            file_path = os.path.join(root, name)
            if not os.path.islink(file_path):
                try:
                    size += os.path.getsize(file_path)
                except OSError:
                    continue
    return int(size * max_)


def ceil(x):
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        int_part = int(x)
        if x > 0 and x > int_part:
            return int_part + 1
        return int_part
    return int(x)


def rmdire(path):
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
        except PermissionError:
            print("无法删除文件夹，权限不足")
        else:
            print("删除成功！")


# Validate archive members before extracting into a project path.
def safe_extract_zip(archive, destination):
    """Extract a ZIP only after rejecting members that escape its destination."""
    import stat
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise LayoutError(f'ZIP 输出目录无效: {destination}')
    destination = destination.resolve()
    for member in archive.infolist():
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
            raise LayoutError(f'ZIP 不支持链接或特殊文件: {member.filename}')
        target = (destination / member.filename).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise LayoutError(f'ZIP 包含越界路径: {member.filename}') from error
    archive.extractall(destination)


def findfile(file, dir_) -> str:
    """Walk dir_ and return the first path ending with file."""
    for root, dirs, files in os.walk(dir_, topdown=True):
        if file in files:
            return root + os.sep + file
