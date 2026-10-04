"""Standalone EXT4 and sparse image extraction.

The parsing core is the vendored python-ext4 package
(Scripts/Primary/EXT4Core). This module adds the Android layer on top:
sparse/raw detection, fsconfig/file_contexts collection and image
extraction.
"""

import mmap
import os
import re
import shutil
import struct
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count
from pathlib import Path

from Scripts.Primary.WorkSpace import record_global_info
from Scripts.Primary.EXT4Core import (
    Inode,
    Volume,
    InvalidStreamException,
    InodeError,
    ExtendedAttributeError,
)
from Scripts.Primary.EXT4Core.enum import EXT4_FT, EXT4_FL
from Scripts.Primary.EXT4Core.inode import MalformedInodeError, OpenDirectoryError
from Scripts.Primary.ImageTools import sparse_to_raw


EXT4_RAW_HEADER_MAGIC = 0xED26FF3A

class ImageExtractionError(RuntimeError):
    """Raised when an image cannot be extracted without data loss."""


class EXT4_IMAGE_HEADER(object):

    def __init__(self, buf):
        (self.magic, self.major, self.minor, self.file_header_size, self.chunk_header_size, self.block_size,
         self.total_blocks, self.total_chunks, self.crc32) = struct.unpack('<I4H4I', buf)


def is_valid_ext4_directory_entry(entry_name, entry_inode_idx):
    """Return whether an EXT4 directory entry points to a real filesystem node."""
    return (
        entry_inode_idx != 0
        and isinstance(entry_name, str)
        and entry_name not in {'', '.', '..'}
    )


def _mode_str(i_mode):
    """Render an EXT4 i_mode value as a 4-digit octal string, the canonical
    Android fs_config mode form (e.g. '0755', '4755', '1777')."""
    perm = i_mode & 0o7777
    return f'{perm:04o}'


def _on_disk_component(name):
    r"""The component name as written to disk. All characters are kept as-is
    (the repacker's fs_config / contexts handle non-ASCII via \xNN escaping);
    only spaces are unsupported by Android's config format. A space in a
    name is warned about once rather than renamed, since renaming desyncs
    the config from the on-disk tree."""
    if ' ' in name:
        print(f'\x1b[1;33m[Warning] 路径含空格，Android fs_config 不支持: {name}\x1b[0m')
    return name


# High-level EXT4 extraction and metadata generation facade.
class ULTRAMAN(object):

    def __init__(self):
        self.FileName = ''
        self.BASE_DIR = ''
        self.OUTPUT_IMAGE_FILE = ''
        self.EXTRACT_DIR = ''
        self.contexts = []
        self.fsconfig = []
        self.sign_offset = 0

    def __file_name(self, file_path):
        name = os.path.basename(file_path).split('.img')[0]
        name = name.split('.unsparse')[0]
        name = name.replace('/', '\\')
        return name

    @staticmethod
    def __appendf(msg, log):
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'w', encoding='utf-8', newline='\n') as file:
            print(msg, file=file)

    def checkSignOffset(self, file):
        size = os.stat(file.name).st_size
        length = 0 if size <= 52428800 else 52428800
        with mmap.mmap(file.fileno(), length, access=mmap.ACCESS_READ) as mm:
            return mm.find(struct.pack('<L', EXT4_RAW_HEADER_MAGIC))

    def __ImgSizeFromSparseFile(self, target):
        img_file = open(target, 'rb')

        if self.sign_offset > 0:
            img_file.seek(self.sign_offset, 0)

        header = EXT4_IMAGE_HEADER(img_file.read(28))
        imgsize = header.block_size * header.total_blocks
        img_file.close()

        return imgsize

    def GetImageType(self, target):
        filename, file_extension = os.path.splitext(target)
        if file_extension == '.img':
            with open(target, "rb") as img_file:
                setattr(self, 'sign_offset', self.checkSignOffset(img_file))
                if self.sign_offset > 0:
                    img_file.seek(self.sign_offset, 0)
                header = EXT4_IMAGE_HEADER(img_file.read(28))
                if header.magic != EXT4_RAW_HEADER_MAGIC:
                    return 'img'
                else:
                    return 'simg'

    def FIX_MOTO(self, input_file):
        if not os.path.exists(input_file):
            return
        output_file = input_file + "_"
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except OSError:
                pass
        with open(input_file, 'rb') as f:
            data = f.read(500000)
        moto = re.search(b'\x4d\x4f\x54\x4f', data)
        if not moto:
            return
        result = []
        for i in re.finditer(b'\x53\xef', data):
            result.append(i.start() - 1080)
        offset = 0
        for i in result:
            if data[i] == 0:
                offset = i
                break
        if offset > 0:
            with open(output_file, 'wb') as o, open(input_file, 'rb') as f:
                f.seek(offset)
                data = f.read(15360)
                if data:
                    o.write(data)
        try:
            os.remove(input_file)
            os.rename(output_file, input_file)
        except OSError:
            pass

    def __fix_size(self):
        """Expand a truncated EXT4 image to the size recorded in its superblock."""
        orig_size = os.path.getsize(self.OUTPUT_IMAGE_FILE)
        # Read s_blocks_count / block size straight from the on-disk superblock
        # (offset 1024) to avoid depending on the parser's derived sizes.
        with open(self.OUTPUT_IMAGE_FILE, 'rb') as file:
            file.seek(1024)
            superblock = file.read(1024)
        if len(superblock) < 40:
            return
        s_blocks_count = struct.unpack_from('<L', superblock, 12)[0]
        block_size = 1024 << struct.unpack_from('<L', superblock, 24)[0]
        real_size = s_blocks_count * block_size
        if orig_size < real_size:
            print(f'> EXT4 镜像被截断，扩展: {orig_size} -> {real_size}')
            with open(self.OUTPUT_IMAGE_FILE, 'r+b') as file:
                file.truncate(real_size)

    def MONSTER(self, target, output_dir):
        output_dir = Path(output_dir)
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ImageExtractionError(f'提取目录无效: {output_dir}')
        self.BASE_DIR = os.path.realpath(os.path.dirname(target)) + os.sep
        self.EXTRACT_DIR = str(output_dir.resolve()) + os.sep
        self.OUTPUT_IMAGE_FILE = self.BASE_DIR + os.path.basename(target)
        self.FileName = self.__file_name(os.path.basename(target))
        image_type = self.GetImageType(target)
        if image_type == 'simg':
            self.OUTPUT_IMAGE_FILE = self.Simg2Rimg(target)
        elif image_type != 'img':
            raise ImageExtractionError(f'无法识别 EXT4 镜像: {target}')

        with open(os.path.abspath(self.OUTPUT_IMAGE_FILE), 'rb') as stream:
            moto = re.search(b'MOTO', stream.read(500000))
        if moto:
            self.FIX_MOTO(os.path.abspath(self.OUTPUT_IMAGE_FILE))
        try:
            self.__fix_size()
            self.EXT4_EXTRACTOR()
        finally:
            if image_type == 'simg' and os.path.isfile(self.OUTPUT_IMAGE_FILE):
                os.remove(self.OUTPUT_IMAGE_FILE)
        return True

    def Simg2Rimg(self, target):
        """Convert sparse data through the shared utility module."""
        temp_dir = os.path.dirname(self.EXTRACT_DIR)
        destination = os.path.join(
            temp_dir,
            f".{os.path.basename(target)}.unsparse.img",
        )
        return sparse_to_raw(target, destination, temp_dir=temp_dir)

    def EXT4_EXTRACTOR(self):
        output_root = Path(self.EXTRACT_DIR).resolve()
        config_dir = output_root.parent / 'config'
        if output_root.is_symlink() or not output_root.is_dir():
            raise ImageExtractionError(f'EXT4 输出目录无效: {output_root}')
        if config_dir.is_symlink():
            raise ImageExtractionError(f'EXT4 metadata 目录无效: {config_dir}')
        config_dir.mkdir(parents=True, exist_ok=True)

        contexts_path = config_dir / f'{self.FileName}_file_contexts'
        fsconfig_path = config_dir / f'{self.FileName}_fs_config'
        info_path = config_dir / 'info.json'
        partition_size = os.path.getsize(self.OUTPUT_IMAGE_FILE)
        with open(self.OUTPUT_IMAGE_FILE, 'rb') as filesystem:
            filesystem.seek(1024)
            superblock = filesystem.read(1024)
        if len(superblock) != 1024:
            raise ImageExtractionError(f'EXT4 superblock 被截断: {self.OUTPUT_IMAGE_FILE}')
        inode_count = struct.unpack_from('<L', superblock, 0)[0]
        block_size = 1024 << struct.unpack_from('<L', superblock, 24)[0]
        per_group = struct.unpack_from('<L', superblock, 32)[0]
        label = bytes(superblock[120:136]).rstrip(b'\x00').decode('utf-8', 'replace')
        # AOSP writes '/' as the volume label for system-as-root; A.R.T keeps
        # all partition content under WORKSPACE/<partition>/, so fs_config /
        # contexts carry a <partition>/ prefix and the mount point must be
        # /<partition> (not '/') for the packer to match. Fall back to the
        # partition name when the superblock label is '/' or empty.
        if not label or label == '/':
            label = self.FileName
        manifest = {
            'label': label,
            'type': 'ext4',
            'size': partition_size,
        }

        seen_targets = set()
        # Regular files are collected here in phase 1 and extracted
        # concurrently in phase 2; each entry is the inode index plus the
        # on-disk target + mode (per-worker re-resolves the inode via its
        # own Volume since inodes bind to the Volume that created them).
        file_tasks = []

        def output_path(components):
            # All on-disk names are kept verbatim (non-ASCII is handled by the
            # repacker's \xNN contexts escaping); only path separators and
            # traversal are rejected. Spaces are unsupported by Android's
            # fs_config format and are warned about, not renamed.
            if not components or any(
                not component or component in {'.', '..'} or '/' in component or '\\' in component
                for component in components
            ):
                raise ImageExtractionError(f'EXT4 包含无法安全表示的路径: {components!r}')
            target = output_root.joinpath(*components)
            try:
                target.relative_to(output_root)
            except ValueError as error:
                raise ImageExtractionError(f'EXT4 路径越界: {components!r}') from error
            if target in seen_targets:
                raise ImageExtractionError(f'EXT4 路径冲突: {target}')
            if target.parent.is_symlink() or not target.parent.is_dir():
                raise ImageExtractionError(f'EXT4 父目录无效: {target.parent}')
            seen_targets.add(target)
            return target

        def read_link(inode, volume):
            if isinstance(inode, Inode):
                return inode.readlink().decode('utf-8')
            return ''

        def scan_dir(root_inode, volume, components=()):
            for entry, file_type in root_inode.opendir():
                entry_name = entry.name_str
                entry_inode_idx = int(entry.inode)
                if not is_valid_ext4_directory_entry(entry_name, entry_inode_idx):
                    continue
                entry_inode = volume.inodes[entry_inode_idx]
                entry_components = (*components, entry_name)
                target = output_path(entry_components)
                mode = _mode_str(int(entry_inode.i_mode))
                if len(mode) != 4:
                    raise ImageExtractionError(f'EXT4 文件权限无效: {entry_name!r}')
                uid = int(entry_inode.i_uid)
                gid = int(entry_inode.i_gid)
                # fs_config paths must match the on-disk tree exactly: all
                # names are kept verbatim (non-ASCII is escaped to \xNN only
                # at repack time). A space in a name is warned about, not
                # rewritten, since renaming would desync the config.
                fs_path = f'{self.FileName}/' + '/'.join(entry_components)
                cap = ''
                link_target = ''
                for attribute, value in entry_inode.xattrs:
                    if attribute == 'security.selinux':
                        escaped = fs_path
                        for character in '\\^$.|?*+(){}[]':
                            escaped = escaped.replace(character, '\\' + character)
                        self.contexts.append(f'/{escaped} {value.decode("utf-8").rstrip(chr(0))}')
                    elif attribute == 'security.capability':
                        values = struct.unpack('<5I', value)
                        if values[1] > 65535:
                            capability = hex(int(f'{values[3]:04x}{values[1]:04x}', 16))
                        else:
                            capability = hex(int(f'{values[3]:04x}{values[2]:04x}{values[1]:04x}', 16))
                        cap = f' capabilities={capability}'

                if file_type == EXT4_FT.DIR:
                    try:
                        target.mkdir()
                    except OSError as error:
                        raise ImageExtractionError(f'EXT4 目录创建失败: {target}: {error}') from error
                    if os.geteuid() == 0:
                        os.chmod(target, int(mode, 8))
                        os.chown(target, uid, gid)
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap}')
                    scan_dir(entry_inode, volume, entry_components)
                elif file_type == EXT4_FT.REG_FILE:
                    # Phase 1: defer data extraction to phase 2. The inode
                    # index is re-resolved per-worker via a dedicated Volume
                    # (inodes bind to the Volume that created them). i_size
                    # drives the size-aware chunking in phase 2 so a few large
                    # files don't stall a single worker.
                    file_tasks.append((entry_inode_idx, str(target), mode, uid, gid, int(entry_inode.i_size)))
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap}')
                elif file_type == EXT4_FT.SYMLINK:
                    link_target = read_link(entry_inode, volume)
                    try:
                        os.symlink(link_target, target)
                    except OSError as error:
                        raise ImageExtractionError(f'EXT4 符号链接创建失败: {target}: {error}') from error
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap} {link_target}')
                else:
                    raise ImageExtractionError(f'EXT4 包含不支持的文件类型: {entry_name!r}')

        with open(self.OUTPUT_IMAGE_FILE, 'rb') as image_file:
            image_file.seek(0)
            # tolerate_unknown prefixes: vendor ext4 images carry xattr entries
            # with e_name_index values outside the standard 0-8 table (e.g. 110);
            # warn and skip them instead of aborting the whole extraction.
            volume = Volume(image_file, ignore_attr_name_index=True)
            scan_dir(volume.root, volume)

        # Phase 2: extract regular-file data concurrently via processes.
        # Each worker opens its own image handle and builds an independent
        # Volume (its own GIL + inode cache); inodes bind to the Volume that
        # created them, so the index is re-resolved per-worker. File extents
        # are disjoint and each writes a distinct target path, so no lock is
        # needed. ThreadPoolExecutor could not parallelize this — EXT4Core is
        # pure-Python CPU work that holds the GIL; processes bypass that.
        if file_tasks:
            self._extract_files_concurrent(file_tasks)

        partition_name = self.FileName
        self.fsconfig.insert(0, '/ 0 2000 0755' if partition_name == 'vendor' else '/ 0 0 0755')
        self.fsconfig.insert(1, f'{partition_name} 0 2000 0755' if partition_name == 'vendor' else '/lost+found 0 0 0700')
        self.fsconfig.insert(2 if partition_name == 'system' else 1, f'{partition_name} 0 0 0755')
        self.__appendf('\n'.join(self.fsconfig), fsconfig_path)
        record_global_info(config_dir, partition_name, manifest)
        if self.contexts:
            self.contexts.sort()
            root_context = None
            for context in self.contexts:
                fields = context.split(maxsplit=1)
                if len(fields) == 2 and re.search(r'lost.{2}found', context):
                    root_context = fields[1]
                    break
            if not root_context:
                root_context = 'u:object_r:rootfs:s0'
            if root_context:
                self.contexts.insert(0, f'/ {root_context}')
                self.contexts.insert(1, f'/{partition_name}(/.*)? {root_context}')
                self.contexts.insert(2, f'/{partition_name} {root_context}')
                self.contexts.insert(3, f'/{partition_name}/lost+\\found {root_context}')
        self.__appendf('\n'.join(self.contexts), contexts_path)
        return True

    def _extract_files_concurrent(self, file_tasks):
        total = len(file_tasks)
        workers = min(8, cpu_count() or 1, total)
        if workers <= 1:
            _dump_files(self.OUTPUT_IMAGE_FILE, file_tasks)
            return
        # Size-aware greedy chunking: sort by size descending, then assign
        # each file to the worker with the smallest accumulated bytes. This
        # keeps a few large files (e.g. webview.apk ~180MB) from stalling one
        # worker while others idle on small files. Each chunk still holds
        # disjoint inode indices whose extents occupy disjoint disk blocks.
        sorted_tasks = sorted(file_tasks, key=lambda t: t[5], reverse=True)
        chunks = [[] for _ in range(workers)]
        loads = [0] * workers
        for task in sorted_tasks:
            w = loads.index(min(loads))
            chunks[w].append(task)
            loads[w] += task[5]
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_dump_files, self.OUTPUT_IMAGE_FILE, chunk)
                for chunk in chunks if chunk
            ]
            for future in as_completed(futures):
                future.result()

# ---------------------------------------------------------------------------
# Phase-2 concurrent file extraction worker (module-level for pickling)
# ---------------------------------------------------------------------------
# Each process opens its own handle on the image and builds an independent
# Volume: its own GIL, its own inode cache, its own cursor/stream. Inodes
# bind to the Volume that created them, so the index is re-resolved here
# rather than reusing phase-1 inode objects. File extents are disjoint and
# each writes a distinct target path, so no lock is needed.
def _dump_files(image_path, chunk):
    from Scripts.Primary.EXT4Core import Volume
    with open(image_path, 'rb') as stream:
        volume = Volume(stream, ignore_attr_name_index=True)
        block_size = volume.block_size
        base = volume.offset
        for inode_idx, target, mode, uid, gid, _size in chunk:
            inode = volume.inodes[inode_idx]
            try:
                with open(target, 'xb') as out:
                    if inode.is_inline:
                        # Inline data lives in the inode block itself; BlockIO
                        # handles that via BytesIO, so fall back to it.
                        reader = inode.open()
                        try:
                            while True:
                                data = reader.read(1024 * 1024)
                                if not data:
                                    break
                                out.write(data)
                        finally:
                            close_reader = getattr(reader, 'close', None)
                            if close_reader:
                                close_reader()
                    else:
                        # Read each leaf extent in one shot instead of 4KB-
                        # per-block through BlockIO.peek(): a 173MB file with
                        # 51 extents drops from ~45000 seek+read calls to 51,
                        # and a small one-extent file drops from N to 1.
                        file_size = int(inode.i_size)
                        written = 0
                        next_block = 0
                        for extent in inode.extents:
                            if written >= file_size:
                                break
                            ee_block = int(extent.ee_block)
                            ee_len = extent.len
                            # Fill any hole between the previous extent and
                            # this one (implicit zero region).
                            if ee_block > next_block:
                                hole = min(
                                    (ee_block - next_block) * block_size,
                                    file_size - written,
                                )
                                out.write(b'\x00' * hole)
                                written += hole
                            data_len = min(
                                ee_len * block_size, file_size - written
                            )
                            if extent.is_initialized:
                                stream.seek(
                                    base + extent.ee_start * block_size
                                )
                                data = stream.read(data_len)
                                if len(data) != data_len:
                                    raise ImageExtractionError(
                                        f'EXT4 文件读取不完整: {target}'
                                    )
                                out.write(data)
                            else:
                                # Uninitialized extent within file: zeros.
                                out.write(b'\x00' * data_len)
                            written += data_len
                            next_block = ee_block + ee_len
                        # Fill any trailing hole between the last extent and
                        # the file size (BlockIO returns null_block for these
                        # logical blocks, so the on-disk tree expects zeros).
                        if written < file_size:
                            out.write(b'\x00' * (file_size - written))
                            written = file_size
            except OSError as error:
                raise ImageExtractionError(
                    f'EXT4 文件写入失败: {target}: {error}'
                ) from error
            if os.geteuid() == 0:
                os.chmod(target, int(mode, 8))
                os.chown(target, uid, gid)


# ---------------------------------------------------------------------------
# Standalone extraction facade
# ---------------------------------------------------------------------------
_SAFE_PARTITION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_RESERVED_PARTITIONS = {".", "..", "config", "INPUT", "OUT", "WORKSPACE"}


# Output validation and metadata verification.
def _validate_partition(partition):
    if not isinstance(partition, str) or not _SAFE_PARTITION.fullmatch(partition):
        raise ImageExtractionError(f"非法分区名称: {partition!r}")
    if partition in _RESERVED_PARTITIONS:
        raise ImageExtractionError(f"保留分区名称: {partition}")
    return partition


def _prepare_partition_output(partition, destination):
    """Prepare the output and metadata directories without project imports."""
    _validate_partition(partition)
    output_dir = Path(destination)
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ImageExtractionError(f"EXT4 输出目录无效: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    config_dir = output_dir.parent / "config"
    if config_dir.is_symlink() or (config_dir.exists() and not config_dir.is_dir()):
        raise ImageExtractionError(f"EXT4 metadata 目录无效: {config_dir}")
    config_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, config_dir


def _metadata_path(config_dir, partition, suffix):
    return Path(config_dir) / f"{partition}{suffix}"


def _verify_metadata(partition, config_dir):
    """Ensure the metadata generated by the embedded extractor is complete."""
    contexts = _metadata_path(config_dir, partition, "_file_contexts")
    if not contexts.exists():
        contexts.touch()
    required = (
        _metadata_path(config_dir, partition, "_file_contexts"),
        _metadata_path(config_dir, partition, "_fs_config"),
        config_dir / "info.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ImageExtractionError(
            f"{partition} 缺少必要 metadata: {', '.join(missing)}"
        )


# Public EXT4 extraction entry point.
def extract_ext4(working_source, partition, destination):
    """Extract an EXT4 image into the supplied destination directory.

    The python-ext4 parser lives in Scripts/Primary/EXT4Core; this module adds the
    Android layer (sparse detection, fsconfig/contexts collection).
    """
    try:
        output_dir, config_dir = _prepare_partition_output(partition, destination)
        print('Process is releasing the file...')
        ULTRAMAN().MONSTER(working_source, str(output_dir))
        _verify_metadata(partition, config_dir)
        return True
    except (
        ImageExtractionError,
        OSError,
        ValueError,
        struct.error,
        UnicodeError,
        InvalidStreamException,
        InodeError,
        ExtendedAttributeError,
        MalformedInodeError,
        OpenDirectoryError,
        MemoryError,
    ):
        # decompress_img prints the Success/Failed banner; just signal failure.
        return False
