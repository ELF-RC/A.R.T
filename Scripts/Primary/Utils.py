"""Shared utility functions grouped by responsibility.

Sections are intentionally kept in one stable common helper module for the
extract, remake, and primary packages.
"""

import os
import platform
import re
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


# ---------------------------------------------------------------------------
# Tool-output severity
#
# The bundled tools disagree about how they report problems:
#
#   mkfs.erofs  tags lines by level — <E> error, <W> warning, <I> info — and
#               additionally emits bare `info: ignored token ...` lines for
#               benign cases, e.g. an fs_config entry that matches no file in
#               the partition. On a real ROM those number in the thousands (a
#               MIUI system partition alone produces ~3000), so printing the
#               captured log verbatim buries the actual error under noise.
#   e2fsdroid,  no level markers at all — prose only, e.g. "e2fsdroid: No such
#   mke2fs      file or directory while opening file /path/img".
#
# Classification therefore trusts an explicit tag when one is present and
# falls back to keywords for the prose tools.
# ---------------------------------------------------------------------------
_TAGGED_LEVEL = re.compile(r'^\s*<(?P<level>[EWID])>')
_INFO_PREFIX = re.compile(r'^\s*(?:info|notice|debug)\s*:', re.IGNORECASE)

# Prose worth showing. Deliberately excludes 'ignored' and 'skip', which make
# up the benign bulk of erofs output.
_SERIOUS_MARKERS = (
    'error', 'cannot', "can't", 'failed', 'failure', 'fatal', 'abort',
    'not found', 'no such', 'unable', 'denied', 'invalid', 'refusing',
    'warn', 'corrupt', 'overflow', 'mismatch', 'too small', 'too large',
)

# Cap on how many lines reach the terminal; the rest stay in the log file.
MAX_LOG_LINES = 40


def classify_log(log):
    """Annotate each non-empty output line as ``serious``, ``info`` or ``other``.

    Returns an ordered ``[(level, line)]`` list so callers can reconstruct the
    real tail of a run — separate level buckets would scramble it.

    ``info`` means the tool explicitly marked the line as such (an ``<I>`` tag
    or an ``info:`` prefix). ``other`` is untagged prose carrying no keyword:
    on mke2fs that is progress output, worthless on success but the only text
    available when a prose tool fails without a recognizable keyword.
    """
    classified = []
    for raw in (log or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        tagged = _TAGGED_LEVEL.match(line)
        if tagged:
            level = 'serious' if tagged.group('level') in 'EW' else 'info'
        elif _INFO_PREFIX.match(line):
            level = 'info'
        elif any(marker in line.lower() for marker in _SERIOUS_MARKERS):
            level = 'serious'
        else:
            level = 'other'
        classified.append((level, line))
    return classified


def _serious(classified):
    """Serious lines only, in original order."""
    return [line for level, line in classified if level == 'serious']


def _info_count(classified):
    """How many lines the tool itself flagged as informational."""
    return sum(1 for level, _ in classified if level == 'info')


def _tail(classified, limit=MAX_LOG_LINES):
    """Last *limit* lines of a run, in order, whatever their level."""
    return [line for _, line in classified[-limit:]]


def _capped(lines, limit=MAX_LOG_LINES):
    """Truncate a line list for terminal display, saying how much was cut."""
    shown = list(lines[:limit])
    if len(lines) > limit:
        shown.append(f'…另有 {len(lines) - limit} 行，见完整日志')
    return shown


def save_log(log, log_path):
    """Write the unfiltered log to *log_path*; return the path, or None.

    A failed pack is diagnosed after the fact more often than not, and the
    terminal copy is deliberately partial, so the full output has to survive
    somewhere.
    """
    if not log_path or not (log or '').strip():
        return None
    parent = os.path.dirname(log_path)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as handle:
            handle.write(log.rstrip('\n') + '\n')
    except OSError:
        return None
    return log_path


def pack_log_path(tool, label=''):
    """Where a failed *tool* run should dump its full log.

    Lives beside the project workspace so it is easy to find and attach to a
    bug report. Returns '' when no workspace is set, which makes save_log()
    a no-op rather than writing into the current directory.
    """
    workspace = getattr(V, 'workspace', '') or ''
    if not workspace or workspace == 'None':
        return ''
    name = f'{label}_{tool}' if label else tool
    return os.path.join(workspace, f'{name}.log')


def failure_log(log, exit_code):
    """Build a display string for a failed run, dropping the benign bulk.

    Used where a log goes into an exception message rather than straight to
    the terminal. Falls back to the tail of the log when nothing was
    recognized as an error, since that is where prose tools report the fatal
    one — mke2fs and e2fsdroid emit no level tags at all.
    """
    classified = classify_log(log)
    serious = _serious(classified)
    if serious:
        return '\n'.join(_capped(serious))
    if classified:
        return '\n'.join(_tail(classified))
    return f'(无输出，退出码 {exit_code})'


def report_failure(log, tool, exit_code, log_path=None, reason=None):
    """Print a usable diagnosis for a failed tool run.

    Only serious lines reach the terminal. The unfiltered log is written to
    *log_path* when one is given and its location reported, so the thousands
    of `info: ignored token` lines a real ROM produces stay available without
    burying the error they would otherwise hide.
    """
    classified = classify_log(log)
    serious = _serious(classified)
    info = _info_count(classified)
    saved = save_log(log, log_path)

    head = reason or f'{tool} 失败 (退出码 {exit_code})'
    if serious:
        lines = _capped(serious)
    elif classified:
        # Nothing looked like an error: show the end of the run, where tools
        # usually report what actually went wrong.
        head += '，未识别出 error 级输出，以下为日志末尾'
        lines = _tail(classified)
    else:
        lines = []
        head += '，无输出'

    print(f'Process log: {head}')
    for line in lines:
        print(f'  {line}')
    if serious and info:
        print(f'  (另有 {info} 条 info 级输出)')
    if saved:
        print(f'  完整日志: {saved}')


def surface_warnings(log, tool=''):
    """Print serious lines from an otherwise successful run.

    A clean exit is not a clean run: e2fsdroid reports unmatched fs_config
    entries and SELinux labels and still exits 0, leaving the image subtly
    wrong with no visible failure.

    Info-level lines are counted, never printed: every successful mkfs.erofs
    emits thousands of benign `info: ignored token` lines for fs_config
    entries that match no file in the partition, and dumping them would
    defeat the quiet Success!/Failed! style this function exists to keep.

    Returns True when something was printed.
    """
    classified = classify_log(log)
    serious = _serious(classified)
    info = _info_count(classified)
    if not serious and not info:
        return False
    label = f' ({tool})' if tool else ''
    print(f'\n{YELLOW}工具输出提示{label}:{CLOSE}')
    for line in _capped(serious, 20):
        print(f'  {line}')
    if info:
        print(f'  另有 {info} 条 info 级输出（正常，多为 fs_config 条目未匹配到文件）')
    return True



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
