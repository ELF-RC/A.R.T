from __future__ import annotations

import errno
import io
import os
import warnings
from collections.abc import Generator
from ctypes import (
    LittleEndianStructure,
    LittleEndianUnion,
    c_uint16,
    c_uint32,
    sizeof,
)
from typing import (
    TYPE_CHECKING,
    Any,
    Self,
    final,
)

from Scripts.Primary.eeems._vendored.cachetools import (
    LRUCache,
    cachedmethod,
)

from ._compat import (
    ReadableStream,
    assert_cast,
    override,
)
from .block import BlockIO
from .directory import (
    EXT4_DIR_ROUND,
    DirectoryEntry,
    DirectoryEntry2,
    DirectoryEntryHash,
)
from .enum import (
    EXT4_FEATURE_INCOMPAT,
    EXT4_FL,
    EXT4_FT,
    EXT4_OS,
    MODE,
)
from .extent import (
    Extent,
    ExtentHeader,
    ExtentIndex,
    ExtentTree,
)
from .htree import (
    DXRoot,
    HtreeHashError,
)
from .struct import (
    Ext4Struct,
    MagicError,
    crc32c,
)
from .superblock import Superblock
from .xattr import (
    ExtendedAttributeHeader,
    ExtendedAttributeIBodyHeader,
)

if TYPE_CHECKING:
    from .volume import Volume


class OpenDirectoryError(Exception):
    pass


class InodeError(Exception):
    pass


class MalformedInodeError(Exception):
    pass


@final
class Linux1(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    _fields_ = (("l_i_version", c_uint32),)


@final
class Hurd1(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    _fields_ = (("h_i_translator", c_uint32),)


@final
class Masix1(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("m_i_reserved1",)
    _fields_ = (("m_i_reserved1", c_uint32),)


@final
class Osd1(LittleEndianUnion):
    __slots__ = ()
    _pack_ = 1
    _fields_ = (
        ("linux1", Linux1),
        ("hurd1", Hurd1),
        ("masix1", Masix1),
    )


@final
class Linux2(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("l_i_reserved",)
    _fields_ = (
        ("l_i_blocks_high", c_uint16),
        ("l_i_file_acl_high", c_uint16),
        ("l_i_uid_high", c_uint16),
        ("l_i_gid_high", c_uint16),
        ("l_i_checksum_lo", c_uint16),
        ("l_i_reserved", c_uint16),
    )


@final
class Hurd2(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("h_i_reserved1",)
    _fields_ = (
        ("h_i_reserved1", c_uint16),
        ("h_i_mode_high", c_uint16),
        ("h_i_uid_high", c_uint16),
        ("h_i_gid_high", c_uint16),
        ("h_i_author", c_uint32),
    )


@final
class Masix2(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("h_i_reserved1", "m_i_reserved2")
    _fields_ = (
        ("h_i_reserved1", c_uint16),
        ("m_i_file_acl_high", c_uint16),
        ("m_i_reserved2", c_uint32 * 2),
    )


@final
class Osd2(LittleEndianUnion):
    __slots__ = ()
    _pack_ = 1
    _fields_ = (
        ("linux2", Linux2),
        ("hurd2", Hurd2),
        ("masix2", Masix2),
    )


class Inode(Ext4Struct):
    EXT4_GOOD_OLD_INODE_SIZE: int = 128
    EXT2_GOOD_OLD_INODE_SIZE: int = 128
    __slots__: tuple[str, ...] = (
        "i_no",
        "tree",
    )
    _pack_ = 1  # pyright: ignore[reportUnannotatedClassAttribute]
    _fields_ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        ("i_mode", MODE.basetype),
        ("i_uid", c_uint16),
        ("i_size_lo", c_uint32),
        ("i_atime", c_uint32),
        ("i_ctime", c_uint32),
        ("i_mtime", c_uint32),
        ("i_dtime", c_uint32),
        ("i_gid", c_uint16),
        ("i_links_count", c_uint16),
        ("i_blocks_lo", c_uint32),
        ("i_flags", EXT4_FL.basetype),
        ("osd1", Osd1),
        ("i_block", c_uint32 * 15),
        ("i_generation", c_uint32),
        ("i_file_acl_lo", c_uint32),
        ("i_size_high", c_uint32),
        ("i_obso_faddr", c_uint32),
        ("osd2", Osd2),
        ("i_extra_isize", c_uint16),
        ("i_checksum_hi", c_uint16),
        ("i_ctime_extra", c_uint32),
        ("i_mtime_extra", c_uint32),
        ("i_atime_extra", c_uint32),
        ("i_crtime", c_uint32),
        ("i_crtime_extra", c_uint32),
        ("i_version_hi", c_uint32),
        ("i_projid", c_uint32),
    )

    @classmethod
    def get_file_type(cls, volume: Volume, offset: int) -> EXT4_FT:
        _ = volume.seek(offset + Inode.i_mode.offset)
        file_type = MODE(
            int.from_bytes(volume.read(Inode.i_mode.size), "little") & 0xF000
        )
        match file_type:
            case MODE.IFIFO:
                return EXT4_FT.FIFO  # pyright: ignore[reportReturnType]

            case MODE.IFCHR:
                return EXT4_FT.CHRDEV  # pyright: ignore[reportReturnType]

            case MODE.IFDIR:
                return EXT4_FT.DIR  # pyright: ignore[reportReturnType]

            case MODE.IFBLK:
                return EXT4_FT.BLKDEV  # pyright: ignore[reportReturnType]

            case MODE.IFREG:
                return EXT4_FT.REG_FILE  # pyright: ignore[reportReturnType]

            case MODE.IFLNK:
                return EXT4_FT.SYMLINK  # pyright: ignore[reportReturnType]

            case MODE.IFSOCK:
                return EXT4_FT.SOCK  # pyright: ignore[reportReturnType]

            case _:
                return EXT4_FT.UNKNOWN  # pyright: ignore[reportReturnType]

    def __new__(cls, volume: Volume, offset: int, i_no: int) -> Self:
        if cls is not Inode:
            return super().__new__(cls)

        file_type = cls.get_file_type(volume, offset)
        match file_type:
            case EXT4_FT.FIFO:
                return super().__new__(Fifo)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.DIR:
                return super().__new__(Directory)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.REG_FILE:
                return super().__new__(File)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.SYMLINK:
                return super().__new__(SymbolicLink)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.CHRDEV:
                return super().__new__(CharacterDevice)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.BLKDEV:
                return super().__new__(BlockDevice)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.SOCK:
                return super().__new__(Socket)  # pyright: ignore[reportArgumentType]

            case EXT4_FT.UNKNOWN:
                return super().__new__(UnknownInode)  # pyright: ignore[reportArgumentType]

            case _:
                raise InodeError(f"Unknown file type 0x{file_type:X}")

    def __init__(self, volume: Volume, offset: int, i_no: int) -> None:
        self.i_no: int = i_no
        super().__init__(volume, offset)
        self.tree: ExtentTree | None = ExtentTree(self)

    @property
    def extra_inode_data(self) -> bytes:
        if not self.has_hi:
            return b""

        size = sizeof(self)
        assert size == self.EXT2_GOOD_OLD_INODE_SIZE + self.i_extra_isize  # pyright: ignore[reportAny]
        _ = self.volume.seek(self.offset + size)
        return self.volume.read(self.superblock.s_inode_size - size)  # pyright: ignore[reportAny]

    @property
    def superblock(self) -> Superblock:
        return self.volume.superblock

    @property
    def block_size(self) -> int:
        return self.volume.block_size

    @property
    def i_size(self) -> int:
        i_size_lo = assert_cast(self.i_size_lo, int)  # pyright: ignore[reportAny]
        i_size_high = assert_cast(self.i_size_high, int)  # pyright: ignore[reportAny]
        return i_size_high << 32 | i_size_lo

    @property
    def i_file_acl(self) -> int:
        i_file_acl_lo = assert_cast(self.i_file_acl_lo, int)  # pyright: ignore[reportAny]
        l_i_file_acl_high = assert_cast(self.osd2.linux2.l_i_file_acl_high, int)  # pyright: ignore[reportAny]
        return l_i_file_acl_high << 32 | i_file_acl_lo

    @property
    def has_hi(self) -> bool:
        s_inode_size = assert_cast(self.superblock.s_inode_size, int)  # pyright: ignore[reportAny]
        return s_inode_size > self.EXT2_GOOD_OLD_INODE_SIZE

    @property
    def fits_in_hi(self) -> bool:
        i_extra_isize = assert_cast(self.i_extra_isize, int)  # pyright: ignore[reportAny]
        return (
            self.has_hi
            and Inode.i_checksum_hi.offset + Inode.i_checksum_hi.size
            <= self.EXT2_GOOD_OLD_INODE_SIZE + i_extra_isize
        )

    @property
    def seed(self) -> int:
        seed = crc32c(self.i_no.to_bytes(4, "little"), self.volume.seed)
        return crc32c(
            self.i_generation.to_bytes(Inode.i_generation.size, "little"),  # pyright: ignore[reportAny]
            seed,
        )

    @Ext4Struct.checksum.getter
    def checksum(self) -> int | None:
        s_creator_os: EXT4_OS = EXT4_OS(self.superblock.s_creator_os)  # pyright: ignore[reportAny]
        if s_creator_os != EXT4_OS.LINUX:
            return None

        data = bytes(self)
        checksum_offset = (
            Inode.osd2.offset + Osd2.linux2.offset + Linux2.l_i_checksum_lo.offset
        )
        checksum_size = Linux2.l_i_checksum_lo.size
        csum = crc32c(data[:checksum_offset], self.seed)
        csum = crc32c(b"\0" * checksum_size, csum)
        csum = crc32c(
            data[checksum_offset + checksum_size : self.EXT2_GOOD_OLD_INODE_SIZE],
            csum,
        )
        if self.has_hi:
            offset = Inode.i_checksum_hi.offset
            csum = crc32c(data[self.EXT2_GOOD_OLD_INODE_SIZE : offset], csum)
            if self.fits_in_hi:
                csum = crc32c(b"\0" * Inode.i_checksum_hi.size, csum)
                offset += Inode.i_checksum_hi.size

            csum = crc32c(
                data[offset:],
                csum,
            )
            s_inode_size = assert_cast(self.superblock.s_inode_size, int)  # pyright: ignore[reportAny]
            if s_inode_size - len(data) > 0:
                csum = crc32c(self.extra_inode_data, csum)

        if not self.has_hi:
            csum &= 0xFFFF

        return csum

    @Ext4Struct.expected_checksum.getter
    def expected_checksum(self) -> int | None:
        s_creator_os = EXT4_OS(self.superblock.s_creator_os)  # pyright: ignore[reportAny]
        if s_creator_os != EXT4_OS.LINUX:
            return None

        provided_csum: int = 0
        l_i_checksum_lo = assert_cast(self.osd2.linux2.l_i_checksum_lo, int)  # pyright: ignore[reportAny]
        provided_csum |= l_i_checksum_lo
        if self.fits_in_hi:
            i_checksum_hi = assert_cast(self.i_checksum_hi, int)  # pyright: ignore[reportAny]
            provided_csum |= i_checksum_hi << 16

        return provided_csum

    @override
    def validate(self) -> None:
        super().validate()
        if self.tree is not None:
            self.tree.validate()

    def has_flag(self, flag: EXT4_FL | int) -> bool:
        i_flags = EXT4_FL(self.i_flags)  # pyright: ignore[reportAny]
        return (i_flags & flag) != 0

    @property
    def is_inline(self) -> bool:
        return self.has_flag(EXT4_FL.INLINE_DATA)

    @property
    def has_extents(self) -> bool:
        return self.has_flag(EXT4_FL.EXTENTS)

    @property
    def extents(self) -> list[Extent]:
        assert self.tree is not None
        return self.tree.extents

    @property
    def headers(self) -> list[ExtentHeader]:
        assert self.tree is not None
        return self.tree.headers

    @property
    def indices(self) -> list[ExtentIndex]:
        assert self.tree is not None
        return self.tree.indices

    def _open(
        self, mode: str = "rb", encoding: None = None, newline: None = None
    ) -> ReadableStream:
        if mode != "rb" or encoding is not None or newline is not None:
            raise NotImplementedError()

        if self.is_inline:
            _ = self.volume.seek(self.offset + Inode.i_block.offset)
            data = self.volume.read(self.i_size)
            return io.BytesIO(data)

        return BlockIO(self)

    def open(
        self,
        mode: str = "rb",  # pyright: ignore[reportUnusedParameter]
        encoding: None = None,  # pyright: ignore[reportUnusedParameter]
        newline: None = None,  # pyright: ignore[reportUnusedParameter]
    ) -> ReadableStream:
        raise NotImplementedError()

    @property
    def xattrs(
        self,
    ) -> Generator[tuple[str, bytes], None, None]:
        i_extra_isize = assert_cast(self.i_extra_isize, int)  # pyright: ignore[reportAny]
        inline_offset = self.offset + self.EXT2_GOOD_OLD_INODE_SIZE + i_extra_isize
        s_inode_size = assert_cast(self.superblock.s_inode_size, int)  # pyright: ignore[reportAny]
        inline_size = self.offset + s_inode_size - inline_offset
        if inline_size > sizeof(ExtendedAttributeIBodyHeader):
            try:
                header = ExtendedAttributeIBodyHeader(self, inline_offset, inline_size)
                header.verify()
                for name, value in header:
                    yield name, value

            except MagicError:
                pass

        if self.i_file_acl != 0:
            block_offset = self.i_file_acl * self.block_size
            try:
                header = ExtendedAttributeHeader(self, block_offset, self.block_size)
                header.verify()
                for name, value in header:
                    yield name, value

            except MagicError:
                if not self.superblock.ignore_magic:
                    raise


@final
class UnknownInode(Inode):
    __slots__ = ()


@final
class Fifo(Inode):
    __slots__ = ()


@final
class CharacterDevice(Inode):
    __slots__ = ()


@final
class BlockDevice(Inode):
    __slots__ = ()


@final
class Socket(Inode):
    __slots__ = ()


@final
class File(Inode):
    __slots__ = ()

    @override
    def open(
        self, mode: str = "rb", encoding: None = None, newline: None = None
    ) -> ReadableStream:
        return self._open(mode, encoding, newline)


@final
class SymbolicLink(Inode):
    __slots__ = ()

    @property
    def is_fast_symlink(self) -> bool:
        i_blocks_lo = assert_cast(self.i_blocks_lo, int)  # pyright: ignore[reportAny]
        return i_blocks_lo == 0 and not self.is_inline

    def readlink(self) -> bytes:
        if not self.is_fast_symlink:
            return self._open().read()

        if self.i_size > Inode.i_block.size:
            raise MalformedInodeError(
                f"Fast symlink target too large: {self.i_size} > {Inode.i_block.size}"
            )

        _ = self.volume.seek(self.offset + Inode.i_block.offset)
        return self.volume.read(self.i_size)


@final
class Directory(Inode):
    __slots__ = (
        "__dict__",
        "_dirents",
        "_inode_at_cache",
        "htree",
    )

    def __init__(self, volume: Volume, offset: int, i_no: int) -> None:
        super().__init__(volume, offset, i_no)
        self._inode_at_cache: LRUCache[str | bytes, Inode] = LRUCache(maxsize=32)
        self._dirents: None | list[DirectoryEntry | DirectoryEntry2] = None
        self.htree: DXRoot | None = None
        if self.is_htree:
            self.htree = DXRoot(self)

    @override
    def verify(self) -> None:
        super().verify()
        # TODO verify DirectoryEntryHash? Or should this be in validate?

    @override
    def validate(self) -> None:
        super().validate()
        # TODO validate each directory entry block with DirectoryEntryTail

    @property
    def has_filetype(self) -> bool:
        return self.superblock.feature_incompat & EXT4_FEATURE_INCOMPAT.FILETYPE != 0

    @property
    def is_htree(self) -> bool:
        return self.has_flag(EXT4_FL.INDEX)

    @property
    def is_casefolded(self) -> bool:
        return self.has_flag(EXT4_FL.CASEFOLD)

    @property
    def is_encrypted(self) -> bool:
        return self.has_flag(EXT4_FL.ENCRYPT)

    @property
    def hash_in_dirent(self) -> bool:
        return self.is_casefolded and self.is_encrypted

    def _opendir(
        self,
    ) -> Generator[DirectoryEntry | DirectoryEntry2, None, None]:
        if self._dirents is not None:
            for dirent in self._dirents:
                yield dirent

            return

        _type = DirectoryEntry2 if self.has_filetype else DirectoryEntry
        dirents: list[DirectoryEntry | DirectoryEntry2] = []
        offset: int = 0
        data = self._open().read()
        while offset < len(data):
            dirent = _type(self, offset)
            rec_len = assert_cast(dirent.rec_len, int)  # pyright: ignore[reportAny]
            if not rec_len:
                # How did this happen?
                offset += _type.name.offset  # + EXT4_DIR_ROUND
                continue

            name_len = assert_cast(dirent.name_len, int)  # pyright: ignore[reportAny]
            dirent_inode = assert_cast(dirent.inode, int)  # pyright: ignore[reportAny]
            if not dirent_inode or not name_len:
                offset += rec_len
                continue

            expected_rec_len: int = _type.name.offset + name_len + EXT4_DIR_ROUND
            if not dirent.is_fake_entry and self.hash_in_dirent:
                expected_rec_len += sizeof(DirectoryEntryHash)

            expected_rec_len &= ~EXT4_DIR_ROUND

            if rec_len < expected_rec_len:
                warnings.warn(
                    "Directory entry is too small for name length"
                    + f", expected={expected_rec_len}"
                    + f", actual={rec_len}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                break

            offset += rec_len
            dirents.append(dirent)
            yield dirent

        self._dirents = dirents

    def _is_valid_file_type(self, file_type: EXT4_FT) -> bool:
        return file_type != EXT4_FT.UNKNOWN and file_type < EXT4_FT.MAX

    def _get_file_type(self, dirent: DirectoryEntry | DirectoryEntry2) -> EXT4_FT:
        dirent_inode = assert_cast(dirent.inode, int)  # pyright: ignore[reportAny]
        offset = self.volume.inodes.offset(dirent_inode)
        file_type = self.get_file_type(self.volume, offset)
        if not self._is_valid_file_type(file_type):
            raise OpenDirectoryError(
                f"Unexpected file type {file_type} for inode {dirent_inode}"
            )

        return file_type

    def opendir(
        self,
    ) -> Generator[tuple[DirectoryEntry | DirectoryEntry2, EXT4_FT], Any, None]:  # pyright: ignore[reportExplicitAny]
        for dirent in self._opendir():
            if not isinstance(dirent, DirectoryEntry2):
                file_type = self._get_file_type(dirent)
                yield dirent, file_type
                continue

            file_type = EXT4_FT(dirent.file_type)  # pyright: ignore[reportAny]
            if file_type == EXT4_FT.DIR_CSUM:
                continue

            if self._is_valid_file_type(file_type):
                yield dirent, file_type
                continue

            raise OpenDirectoryError(f"Unexpected file type: {file_type}")

    @cachedmethod(lambda self: self._inode_at_cache)  # pyright: ignore[reportAny]
    def inode_at(self, path: str | bytes) -> Inode:
        if (isinstance(path, str) and path.startswith("/")) or (
            isinstance(path, bytes) and path.startswith(b"/")
        ):
            return self.volume.inode_at(path)

        if isinstance(path, bytes):
            path = path.decode("utf-8")

        paths = list(self.volume.path_tuple(f"/{path}"))
        cwd = self
        if not paths:
            return cwd

        while paths:
            if not isinstance(cwd, Directory):
                raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))

            name = paths.pop(0)
            inode = None

            if cwd.is_htree and cwd.htree is not None:
                try:
                    inode_no = cwd.htree.lookup(name)
                    if inode_no is not None:
                        inode = self.volume.inodes[inode_no]

                except HtreeHashError:
                    pass

            if inode is None:
                for dirent, _ in cwd.opendir():
                    if dirent.name_bytes == name:
                        dirent_inode = assert_cast(dirent.inode, int)  # pyright: ignore[reportAny]
                        inode = self.volume.inodes[dirent_inode]
                        break

            if inode is None:
                raise FileNotFoundError(path)

            cwd = inode

        return cwd
