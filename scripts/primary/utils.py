"""Shared utility functions and runtime tool management."""

import os
import re
import shlex
import shutil
import subprocess
import struct
import tempfile
import time

from heapq import merge
from itertools import cycle
from pathlib import Path

PWD_DIR = os.getcwd() + os.sep
BIN_PATH = PWD_DIR + "art-res/bin/"

RED, WHITE, CYAN, YELLOW, MAGENTA, GREEN, BOLD, CLOSE = [
    '\x1b[91m', '\x1b[97m', '\x1b[36m', '\x1b[93m',
    '\x1b[1;35m', '\x1b[1;32m', '\x1b[1m', '\x1b[0m',
]


class GlobalValue(object):
    JM = False

    def __init__(self):
        self.programs = [
            "cpio", "brotli", "img2simg", "e2fsck", "resize2fs",
            "mke2fs", "e2fsdroid", "mkfs.erofs", "lpmake",
            "extract.erofs", "magiskboot", "avbroot",
        ]

    def __getattr__(self, item):
        try:
            return getattr(self, item)
        except (Exception, BaseException):
            return "None"


V = GlobalValue()


def change_permissions_recursive(path, mode):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(os.path.join(root, d), mode)
        for f in files:
            os.chmod(os.path.join(root, f), mode)
    os.chmod(path, mode)


def init_bin_path():
    """Verify BIN_PATH exists and set up PATH + permissions."""
    if not os.path.isdir(BIN_PATH):
        print(f"Run err on: {__import__('platform').system()} {__import__('platform').machine()}")
        import sys
        sys.exit()

    os.environ["PATH"] += os.pathsep + BIN_PATH
    change_permissions_recursive(BIN_PATH, 0o777)

    for prog in V.programs:
        if not shutil.which(prog):
            import sys
            sys.exit(f"[x] Not found: {prog}\n[i] Please install {prog} \n   Or add <{prog}> to {BIN_PATH}")


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



# Android sparse image format constants.
SPARSE_HEADER_MAGIC = 0xED26FF3A
SPARSE_HEADER_SIZE = 28
SPARSE_CHUNK_HEADER_SIZE = 12
SPARSE_CHUNK_RAW = 0xCAC1
SPARSE_CHUNK_FILL = 0xCAC2
SPARSE_CHUNK_DONT_CARE = 0xCAC3
SPARSE_CHUNK_CRC32 = 0xCAC4

def is_sparse_image(path):
    """Return whether *path* starts with an Android sparse-image header."""
    try:
        with open(os.fspath(path), 'rb') as stream:
            header = stream.read(4)
    except (OSError, TypeError):
        return False
    return len(header) == 4 and struct.unpack('<I', header)[0] == SPARSE_HEADER_MAGIC

def _sparse_default_path(source, suffix):
    path = Path(os.fspath(source))
    return str(path.with_suffix(suffix))

def _sparse_temp_file(destination):
    parent = os.path.dirname(os.path.abspath(destination)) or os.curdir
    os.makedirs(parent, exist_ok=True)
    return tempfile.mkstemp(
        prefix=f'.{os.path.basename(destination)}.',
        suffix='.tmp',
        dir=parent,
    )

def sparse_to_raw(source, destination=None):
    """Expand an Android sparse image to a raw image and return its path.

    The conversion is implemented here so extraction and repacking share the
    same RAW/FILL/DONT_CARE/CRC32 handling.
    """
    source = os.fspath(source)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if not is_sparse_image(source):
        raise ValueError(f'不是 Android sparse 镜像: {source}')
    destination = (
        os.fspath(destination)
        if destination is not None
        else _sparse_default_path(source, '.unsparse.img')
    )
    if os.path.abspath(source) == os.path.abspath(destination):
        raise ValueError('sparse 和 raw 输出路径不能相同')

    def read_exact(stream, size, description):
        data = stream.read(size)
        if len(data) != size:
            raise ValueError(f'sparse 镜像{description}被截断: {source}')
        return data

    def consume(stream, size, description):
        remaining = size
        while remaining:
            data = stream.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError(f'sparse 镜像{description}被截断: {source}')
            remaining -= len(data)

    temp_fd, temp_path = _sparse_temp_file(destination)
    try:
        with open(source, 'rb') as image, os.fdopen(temp_fd, 'wb') as raw:
            header = struct.unpack(
                '<I4H4I',
                read_exact(image, SPARSE_HEADER_SIZE, '文件头'),
            )
            (
                magic, _major, _minor, file_hdr_sz, chunk_hdr_sz, block_size,
                total_blocks, total_chunks, _checksum,
            ) = header
            if magic != SPARSE_HEADER_MAGIC:
                raise ValueError(f'不是有效的 sparse 镜像: {source}')
            if file_hdr_sz < SPARSE_HEADER_SIZE:
                raise ValueError(f'sparse 文件头大小无效: {source}')
            if chunk_hdr_sz < SPARSE_CHUNK_HEADER_SIZE:
                raise ValueError(f'sparse chunk 头大小无效: {source}')
            if block_size <= 0 or block_size % 4:
                raise ValueError(f'sparse block 大小无效: {source}')

            image.seek(file_hdr_sz, os.SEEK_SET)
            logical_size = 0
            for _ in range(total_chunks):
                chunk_type, _reserved, chunk_blocks, total_size = struct.unpack(
                    '<2H2I',
                    read_exact(image, SPARSE_CHUNK_HEADER_SIZE, 'chunk 头'),
                )
                if chunk_hdr_sz > SPARSE_CHUNK_HEADER_SIZE:
                    consume(
                        image,
                        chunk_hdr_sz - SPARSE_CHUNK_HEADER_SIZE,
                        'chunk 头扩展',
                    )
                output_size = chunk_blocks * block_size
                data_size = total_size - chunk_hdr_sz
                if data_size < 0:
                    raise ValueError(f'sparse chunk 大小无效: {source}')

                if chunk_type == SPARSE_CHUNK_RAW:
                    if data_size != output_size:
                        raise ValueError(f'sparse RAW chunk 大小无效: {source}')
                    remaining = output_size
                    while remaining:
                        size = min(1024 * 1024, remaining)
                        raw.write(read_exact(image, size, 'RAW 数据'))
                        remaining -= size
                elif chunk_type == SPARSE_CHUNK_FILL:
                    if data_size != 4:
                        raise ValueError(f'sparse FILL chunk 大小无效: {source}')
                    fill = read_exact(image, 4, 'FILL 数据')
                    block = (fill * ((1024 * 1024 + 3) // 4))[:1024 * 1024]
                    remaining = output_size
                    while remaining:
                        size = min(len(block), remaining)
                        raw.write(block[:size])
                        remaining -= size
                elif chunk_type == SPARSE_CHUNK_DONT_CARE:
                    consume(image, data_size, 'DONT_CARE 数据')
                    raw.seek(output_size, os.SEEK_CUR)
                elif chunk_type == SPARSE_CHUNK_CRC32:
                    if output_size or data_size != 4:
                        raise ValueError(f'sparse CRC32 chunk 大小无效: {source}')
                    consume(image, data_size, 'CRC32 数据')
                else:
                    raise ValueError(f'不支持的 sparse chunk 类型: {chunk_type:#x}')
                logical_size += output_size

            expected_size = total_blocks * block_size
            if logical_size != expected_size:
                raise ValueError(f'sparse 镜像逻辑大小与文件头不一致: {source}')
            raw.truncate(expected_size)
        os.replace(temp_path, destination)
    except Exception:
        try:
            os.close(temp_fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return destination

def raw_to_sparse(source, destination=None, block_size=4096):
    """Convert a raw image to Android sparse format and return its path.

    Zero blocks become DONT_CARE chunks; repeated four-byte blocks become
    FILL chunks; other data is emitted as RAW chunks.
    """
    source = os.fspath(source)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if is_sparse_image(source):
        raise ValueError(f'输入已经是 Android sparse 镜像: {source}')
    if not isinstance(block_size, int) or block_size < 4 or block_size % 4:
        raise ValueError('sparse block 大小必须是不小于 4 的 4 字节倍数')
    destination = (
        os.fspath(destination)
        if destination is not None
        else _sparse_default_path(source, '.sparse.img')
    )
    if os.path.abspath(source) == os.path.abspath(destination):
        raise ValueError('raw 和 sparse 输出路径不能相同')

    def classify(block):
        if not any(block):
            return SPARSE_CHUNK_DONT_CARE, None
        pattern = block[:4]
        if block == pattern * (block_size // 4):
            return SPARSE_CHUNK_FILL, pattern
        return SPARSE_CHUNK_RAW, None

    temp_fd, temp_path = _sparse_temp_file(destination)
    try:
        with open(source, 'rb') as image, os.fdopen(temp_fd, 'w+b') as sparse:
            sparse.write(b'\x00' * SPARSE_HEADER_SIZE)
            current_type = None
            current_fill = None
            current_blocks = 0
            current_header = None
            total_blocks = 0
            total_chunks = 0
            max_raw_blocks = max(1, (0xFFFFFFFF - SPARSE_CHUNK_HEADER_SIZE) // block_size)
            max_raw_blocks = min(max_raw_blocks, 1024 * 1024)

            def flush_chunk():
                nonlocal current_type, current_fill, current_blocks
                nonlocal current_header, total_chunks
                if current_type is None:
                    return
                end = sparse.tell()
                if current_type == SPARSE_CHUNK_RAW:
                    data_size = current_blocks * block_size
                elif current_type == SPARSE_CHUNK_FILL:
                    data_size = 4
                else:
                    data_size = 0
                total_size = SPARSE_CHUNK_HEADER_SIZE + data_size
                if total_size > 0xFFFFFFFF:
                    raise ValueError('sparse chunk 过大')
                sparse.seek(current_header, os.SEEK_SET)
                sparse.write(struct.pack(
                    '<2H2I', current_type, 0, current_blocks, total_size
                ))
                sparse.seek(end, os.SEEK_SET)
                total_chunks += 1
                current_type = None
                current_fill = None
                current_blocks = 0
                current_header = None

            while True:
                block = image.read(block_size)
                if not block:
                    break
                if len(block) < block_size:
                    block += b'\x00' * (block_size - len(block))
                chunk_type, fill = classify(block)
                split = (
                    current_type != chunk_type
                    or (chunk_type == SPARSE_CHUNK_FILL and current_fill != fill)
                    or (chunk_type == SPARSE_CHUNK_RAW and current_blocks >= max_raw_blocks)
                )
                if split:
                    flush_chunk()
                if current_type is None:
                    current_type = chunk_type
                    current_fill = fill
                    current_blocks = 0
                    current_header = sparse.tell()
                    sparse.write(b'\x00' * SPARSE_CHUNK_HEADER_SIZE)
                    if chunk_type == SPARSE_CHUNK_FILL:
                        sparse.write(fill)
                if chunk_type == SPARSE_CHUNK_RAW:
                    sparse.write(block)
                current_blocks += 1
                total_blocks += 1

            flush_chunk()
            end = sparse.tell()
            sparse.seek(0, os.SEEK_SET)
            sparse.write(struct.pack(
                '<I4H4I',
                SPARSE_HEADER_MAGIC,
                1, 0,
                SPARSE_HEADER_SIZE,
                SPARSE_CHUNK_HEADER_SIZE,
                block_size,
                total_blocks,
                total_chunks,
                0,
            ))
            sparse.seek(end, os.SEEK_SET)
        os.replace(temp_path, destination)
    except Exception:
        try:
            os.close(temp_fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return destination

class CoastTime:
    def __init__(self):
        self.t = 0

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"> Coast Time:{time.perf_counter() - self.t:.8f} s")


def display(message, flag=1, end='\n'):
    flags = {1: "3", 2: "6", 3: "4", 4: "1"}
    print(f"\x1b[1;3{flags[flag]}m [ {time.strftime('%H:%M:%S', time.localtime())} ]\t {message} \x1b[0m", end=end)


def get_dir_size(ddir, max_=1.06):
    size = 0
    for (root, dirs, files) in os.walk(ddir):
        for name in files:
            if not os.path.islink(name):
                try:
                    size += os.path.getsize(os.path.join(root, name))
                except:
                    pass
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


def find_file(path, rule):
    for (root, lists, files) in os.walk(path):
        for file in files:
            if re.search(rule, os.path.basename(file)):
                yield os.path.join(root, file)


def rmdire(path):
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
        except PermissionError:
            print("无法删除文件夹，权限不足")
        else:
            print("删除成功！")


def appendf(msg, log):
    if not os.path.isfile(log) and not os.path.exists(log):
        open(log, 'tw', encoding='utf-8').close()
    with open(log, 'w', newline='\n') as file:
        print(msg, file=file)


def _human_size(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


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


def safe_extract_tar(archive, destination):
    """Stream regular TAR members into a validated WORKSPACE staging directory."""
    import sys
    destination = Path(destination).resolve()
    for member in archive:
        if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
            raise LayoutError(f'TAR 不支持的条目类型: {member.name}')
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise LayoutError(f'TAR 包含越界路径: {member.name}') from error
        if sys.version_info >= (3, 12):
            archive.extract(member, path=destination, filter='fully_trusted')
        else:
            archive.extract(member, path=destination)


# ═══════════════════════════════════════════════════════════════════════
#  File type detection (from gettype.py)
# ═══════════════════════════════════════════════════════════════════════

_FILE_SIGNATURES = (
    [b'PK', "zip"], [b'OPPOENCRYPT!', "ozip"], [b'7z', "7z"],
    [b'\x53\xef', 'ext', 1080],
    [b'\x3a\xff\x26\xed', "sparse"],
    [b'\xe2\xe1\xf5\xe0', "erofs", 1024],
    [b"CrAU", "payload"], [b"AVB0", "vbmeta"],
    [b'\xd7\xb7\xab\x1e', "dtbo"], [b'(\xb5/\xfd', 'zst'],
    [b'\xd0\x0d\xfe\xed', "dtb"], [b"MZ", "exe"], [b".ELF", 'elf'],
    [b"ANDROID!", "boot"], [b"VNDRBOOT", "vendor_boot"],
    [b'AVBf', "avb_foot"], [b'BZh', "bzip2"],
    [b'CHROMEOS', 'chrome'], [b'\x1f\x8b', "gzip"],
    [b'\x1f\x9e', "gzip"],
    [b'\x02\x21\x4c\x18', "lz4_legacy"],
    [b'\x03\x21\x4c\x18', 'lz4'], [b'\x04\x22\x4d\x18', 'lz4'],
    [b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03', "zopfli"],
    [b'\xfd7zXZ', 'xz'],
    [b']\x00\x00\x00\x04\xff\xff\xff\xff\xff\xff\xff\xff', 'lzma'],
    [b'\x02!L\x18', 'lz4_lg'],
    [b'\x89PNG', 'png'], [b"LOGO!!!!", 'logo'],
    [b'\x67\x44\x6c\x61', 'super', 4096],
    [b'\x10\x20\xF5\xF2', 'f2fs', 1024],
    [b'\x28\xb5\x2f\xfd', 'zstd'],
)


def gettype(file) -> str:
    """Detect file type by magic bytes. Returns format string or 'unknown'."""
    if not os.path.exists(file):
        return "fne"

    def compare(header: bytes, number: int = 0) -> int:
        with open(file, 'rb') as f:
            f.seek(number)
            return f.read(len(header)) == header

    for sig in _FILE_SIGNATURES:
        if len(sig) == 2:
            if compare(sig[0]):
                return sig[1]
        elif len(sig) == 3:
            if compare(sig[0], sig[2]):
                return sig[1]
    return "unknown"


def findfile(file, dir_) -> str:
    """Walk dir_ and return the first path ending with file."""
    for root, dirs, files in os.walk(dir_, topdown=True):
        if file in files:
            return root + os.sep + file

# Initialize the shared runtime tool path for all consumers.
init_bin_path()



# Copyright (C) 2014 The Android Open Source Project
# RangeSet is used by sparse-image parsing and block OTA generation.
class RangeSet(object):
    """A RangeSet represents a set of non-overlapping ranges on the
  integers (ie, a set of integers, but efficient when the set contains
  lots of runs.)"""

    def __init__(self, data=None):
        self.monotonic = False
        if isinstance(data, str):
            self._parse_internal(data)
        elif data:
            assert len(data) % 2 == 0
            self.data = tuple(self._remove_pairs(data))
            self.monotonic = all(x < y for x, y in zip(self.data, self.data[1:]))
        else:
            self.data = ()

    def __iter__(self):
        for i in range(0, len(self.data), 2):
            yield self.data[i:i + 2]

    def __eq__(self, other):
        return self.data == other.data

    def __ne__(self, other):
        return self.data != other.data

    def __nonzero__(self):
        return bool(self.data)

    def __str__(self):
        if not self.data:
            return "empty"
        else:
            return self.to_string()

    def __repr__(self):
        return '<RangeSet("' + self.to_string() + '")>'

    @classmethod
    def parse(cls, text):
        """Parse a text string consisting of a space-separated list of
    blocks and ranges, eg "10-20 30 35-40".  Ranges are interpreted to
    include both their ends (so the above example represents 18
    individual blocks.  Returns a RangeSet object.)

    If the input has all its blocks in increasing order, then returned
    RangeSet will have an extra attribute 'monotonic' that is set to
    True.  For example the input "10-20 30" is monotonic, but the input
    "15-20 30 10-14" is not, even though they represent the same set
    of blocks (and the two RangeSets will compare equal with ==).
    """
        return cls(text)

    def _parse_internal(self, text):
        data = []
        last = -1
        monotonic = True
        for p in text.split():
            if "-" in p:
                s, e = (int(x) for x in p.split("-"))
                data.append(s)
                data.append(e + 1)
                if last <= s <= e:
                    last = e
                else:
                    monotonic = False
            else:
                s = int(p)
                data.append(s)
                data.append(s + 1)
                if last <= s:
                    last = s + 1
                else:
                    monotonic = False
        data.sort()
        self.data = tuple(self._remove_pairs(data))
        self.monotonic = monotonic

    @staticmethod
    def _remove_pairs(source):
        """Remove consecutive duplicate items to simplify the result.

    [1, 2, 2, 5, 5, 10] will become [1, 10]."""
        last = None
        for i in source:
            if i == last:
                last = None
            else:
                if last is not None:
                    yield last
                last = i
        if last is not None:
            yield last

    def to_string(self):
        out = []
        for i in range(0, len(self.data), 2):
            s, e = self.data[i:i + 2]
            if e == s + 1:
                out.append(str(s))
            else:
                out.append(str(s) + "-" + str(e - 1))
        return " ".join(out)

    def to_string_raw(self):
        assert self.data
        return str(len(self.data)) + "," + ",".join(str(i) for i in self.data)

    def union(self, other):
        """Return a new RangeSet representing the union of this RangeSet
    with the argument.

    >>> RangeSet("10-19 30-34").union(RangeSet("18-29"))
    <RangeSet("10-34")>
    >>> RangeSet("10-19 30-34").union(RangeSet("22 32"))
    <RangeSet("10-19 22 30-34")>
    """
        out = []
        z = 0
        for p, d in merge(zip(self.data, cycle((+1, -1))),
                          zip(other.data, cycle((+1, -1)))):
            if (z == 0 and d == 1) or (z == 1 and d == -1):
                out.append(p)
            z += d
        return RangeSet(data=out)

    def intersect(self, other):
        """Return a new RangeSet representing the intersection of this
    RangeSet with the argument.

    >>> RangeSet("10-19 30-34").intersect(RangeSet("18-32"))
    <RangeSet("18-19 30-32")>
    >>> RangeSet("10-19 30-34").intersect(RangeSet("22-28"))
    <RangeSet("")>
    """
        out = []
        z = 0
        for p, d in merge(zip(self.data, cycle((+1, -1))),
                          zip(other.data, cycle((+1, -1)))):
            if (z == 1 and d == 1) or (z == 2 and d == -1):
                out.append(p)
            z += d
        return RangeSet(data=out)

    def subtract(self, other):
        """Return a new RangeSet representing subtracting the argument
    from this RangeSet.

    >>> RangeSet("10-19 30-34").subtract(RangeSet("18-32"))
    <RangeSet("10-17 33-34")>
    >>> RangeSet("10-19 30-34").subtract(RangeSet("22-28"))
    <RangeSet("10-19 30-34")>
    """

        out = []
        z = 0
        for p, d in merge(zip(self.data, cycle((+1, -1))),
                          zip(other.data, cycle((-1, +1)))):
            if (z == 0 and d == 1) or (z == 1 and d == -1):
                out.append(p)
            z += d
        return RangeSet(data=out)

    def overlaps(self, other):
        """Returns true if the argument has a nonempty overlap with this
    RangeSet.

    >>> RangeSet("10-19 30-34").overlaps(RangeSet("18-32"))
    True
    >>> RangeSet("10-19 30-34").overlaps(RangeSet("22-28"))
    False
    """

        # This is like intersect, but we can stop as soon as we discover the
        # output is going to be nonempty.
        z = 0
        for _, d in merge(zip(self.data, cycle((+1, -1))),
                          zip(other.data, cycle((+1, -1)))):
            if (z == 1 and d == 1) or (z == 2 and d == -1):
                return True
            z += d
        return False

    def size(self):
        """Returns the total size of the RangeSet (ie, how many integers
    are in the set).

    >>> RangeSet("10-19 30-34").size()
    15
    """

        total = 0
        for i, p in enumerate(self.data):
            if i % 2:
                total += p
            else:
                total -= p
        return total

    def map_within(self, other):
        """'other' should be a subset of 'self'.  Returns a RangeSet
    representing what 'other' would get translated to if the integers
    of 'self' were translated down to be contiguous starting at zero.

    >>> RangeSet("0-9").map_within(RangeSet("3-4"))
    <RangeSet("3-4")>
    >>> RangeSet("10-19").map_within(RangeSet("13-14"))
    <RangeSet("3-4")>
    >>> RangeSet("10-19 30-39").map_within(RangeSet("17-19 30-32"))
    <RangeSet("7-12")>
    >>> RangeSet("10-19 30-39").map_within(RangeSet("12-13 17-19 30-32"))
    <RangeSet("2-3 7-12")>
    """

        out = []
        offset = 0
        start = None
        for p, d in merge(zip(self.data, cycle((-5, +5))),
                          zip(other.data, cycle((-1, +1)))):
            if d == -5:
                start = p
            elif d == +5:
                offset += p - start
                start = None
            else:
                out.append(offset + p - start)
        return RangeSet(data=out)

    def extend(self, n):
        """Extend the RangeSet by 'n' blocks.

    The lower bound is guaranteed to be non-negative.

    >>> RangeSet("0-9").extend(1)
    <RangeSet("0-10")>
    >>> RangeSet("10-19").extend(15)
    <RangeSet("0-34")>
    >>> RangeSet("10-19 30-39").extend(4)
    <RangeSet("6-23 26-43")>
    >>> RangeSet("10-19 30-39").extend(10)
    <RangeSet("0-49")>
    """
        out = self
        for i in range(0, len(self.data), 2):
            s, e = self.data[i:i + 2]
            s1 = max(0, s - n)
            e1 = e + n
            out = out.union(RangeSet(str(s1) + "-" + str(e1 - 1)))
        return out

    def first(self, n):
        """Return the RangeSet that contains at most the first 'n' integers.

    >>> RangeSet("0-9").first(1)
    <RangeSet("0")>
    >>> RangeSet("10-19").first(5)
    <RangeSet("10-14")>
    >>> RangeSet("10-19").first(15)
    <RangeSet("10-19")>
    >>> RangeSet("10-19 30-39").first(3)
    <RangeSet("10-12")>
    >>> RangeSet("10-19 30-39").first(15)
    <RangeSet("10-19 30-34")>
    >>> RangeSet("10-19 30-39").first(30)
    <RangeSet("10-19 30-39")>
    >>> RangeSet("0-9").first(0)
    <RangeSet("")>
    """

        if self.size() <= n:
            return self

        out = []
        for s, e in self:
            if e - s >= n:
                out += (s, s + n)
                break
            else:
                out += (s, e)
                n -= e - s
        return RangeSet(data=out)
