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
import unicodedata

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

    def __init__(self):
        self.programs = [
            "brotli", "img2simg",
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
def call(exe, kz='Y', out=0, shstate=False, sp=0, env=None, capture=False):
    """Run a command with MIO-compatible argv handling and no implicit shell.

    capture=True buffers the process output in memory instead of printing it,
    and returns a ``(exit_code, log)`` pair — on success *and* on failure — so
    the caller always keeps the log and decides whether to show it. Returning
    the log only on non-zero exit (the previous behaviour) makes success-time
    warnings unrecoverable, which matters because e2fsdroid and friends exit 0
    after complaining.

    capture=False keeps the historical contract: output streams to the
    terminal and the bare exit code is returned.
    """
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
        message = f'> 启动命令失败: {error}'
        if capture:
            return 127, message
        print(message)
        return 127

    lines = []
    if process.stdout:
        for raw in iter(process.stdout.readline, b''):
            line = raw.decode('utf-8', 'ignore').rstrip('\n')
            lines.append(line)
            if not capture and out == 0:
                print(line.strip())
    exit_code = process.wait()
    if capture:
        return exit_code, '\n'.join(line for line in lines if line.strip())
    return exit_code


def failure_log(log, exit_code):
    """Return captured tool output for display, minus the bulk noise.

    A failed mkfs.erofs on a real ROM produces ~22k lines, of which the useful
    part is one or two. Measured on a system partition:

      - 18k ``Processing <path> ...``   per-file progress
      - 3k  ``<E> failed to write ...`` the same error repeated per thread
      - 937 ``info: ignored token ...``  fs_config entries matching no file

    Printing that verbatim buries the error and pushes it past the terminal
    scrollback, which is how a real failure ends up looking like "only info
    lines were printed". Drop the progress and info lines, and collapse runs
    of an identical repeated line into one with a count — what is left is the
    version banner plus the actual error.

    mke2fs and e2fsdroid emit untagged prose with neither prefix, so nothing
    is dropped for them.
    """
    if not log or not log.strip():
        return f'(无输出，退出码 {exit_code})'

    kept = []
    for raw in log.splitlines():
        line = raw.strip()
        if not line:
            continue
        # erofs-utils always puts its level tag at the start of a line, so a
        # tag further in means concurrent writers interleaved two lines —
        # crop the leftover progress fragment in front of it.
        for tag in ('<E>', '<W>', '<I>'):
            pos = line.find(tag)
            if pos > 0:
                line = line[pos:]
                break
        if line.startswith('info:') or line.startswith('Processing '):
            continue
        # With -T 0 several threads write progress at once, so a dropped
        # 'Processing <path> ...' line often survives as its own tail.
        if line.endswith(' ...'):
            continue
        kept.append(line)
    if not kept:
        return f'(无输出，退出码 {exit_code})'

    # Collapse adjacent duplicates: a failing write repeats the same <E> line
    # once per file/thread, which would otherwise drown its first occurrence.
    grouped = []
    for line in kept:
        if grouped and grouped[-1][0] == line:
            grouped[-1][1] += 1
        else:
            grouped.append([line, 1])
    rendered = [
        f'{line} (×{count})' if count > 1 else line
        for line, count in grouped
    ]

    limit = 40
    if len(rendered) > limit:
        # Keep the banner and the tail: tools report the fatal line last.
        rendered = (rendered[:1]
                    + [f'…省略 {len(rendered) - limit} 行…']
                    + rendered[-(limit - 2):])
    return '\n'.join(rendered)



# ---------------------------------------------------------------------------
# Console formatting helpers
# ---------------------------------------------------------------------------
def human_size(size):
    """Render a byte count for the selection tables."""
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    if size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    return f'{size / (1024 * 1024 * 1024):.2f} GB'


def display_width(text):
    """Terminal columns a string occupies (CJK glyphs are double-width)."""
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1
               for ch in text)


def pad(text, width, align='left'):
    """Pad *text* to *width* display columns, not characters.

    str.ljust/rjust count characters, so a column of Chinese headings comes
    out narrower than the ASCII rows under it and the table looks ragged.
    """
    fill = max(0, width - display_width(text))
    return (' ' * fill + text) if align == 'right' else (text + ' ' * fill)


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
