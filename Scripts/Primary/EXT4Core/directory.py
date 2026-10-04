from ctypes import (
    addressof,
    c_char,
    c_uint8,
    c_uint16,
    c_uint32,
    memmove,
)
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import (
    assert_cast,
    override,
)
from .enum import EXT4_FT
from .struct import Ext4Struct

if TYPE_CHECKING:
    from .inode import Directory

EXT4_NAME_LEN = 255
EXT4_DIR_PAD = 4
EXT4_DIR_ROUND = EXT4_DIR_PAD - 1
EXT4_MAX_REC_LEN = (1 << 16) - 1


class DirectoryEntryStruct(Ext4Struct):
    __slots__: tuple[str, ...] = ("directory",)

    def __init__(self, directory: "Directory", offset: int) -> None:
        self.directory: Directory = directory
        super().__init__(directory.volume, offset)

    @override
    def read_from_volume(self) -> None:
        reader = self.directory._open()  # pyright: ignore[reportPrivateUsage]
        _ = reader.seek(self.offset)
        data = reader.read(self.size)
        # Do not guard against short reads, this happens for the last entry
        _ = memmove(addressof(self), data, len(data))


class DirectoryEntryBase(DirectoryEntryStruct):
    __slots__: tuple[str, ...] = ()

    @property
    def name_bytes(self) -> bytes:
        return bytes(self.name)[: self.name_len]  # pyright: ignore[reportAny]

    @property
    def name_str(self) -> str:
        return self.name_bytes.decode("utf-8")

    @property
    def is_fake_entry(self) -> bool:
        name_len = assert_cast(self.name_len, int)  # pyright: ignore[reportAny]
        return 0 < name_len <= 2 and self.name_bytes in (b".", b"..")


@final
class DirectoryEntry(DirectoryEntryBase):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("l_i_reserved",)
    _fields_ = (
        ("inode", c_uint32),
        ("rec_len", c_uint16),
        ("name_len", c_uint16),
        ("name", c_char * EXT4_NAME_LEN),
    )


@final
class DirectoryEntry2(DirectoryEntryBase):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("l_i_reserved",)
    _fields_ = (
        ("inode", c_uint32),
        ("rec_len", c_uint16),
        ("name_len", c_uint8),
        ("file_type", EXT4_FT.basetype),
        ("name", c_char * EXT4_NAME_LEN),
    )

    @DirectoryEntryBase.is_fake_entry.getter
    def is_fake_entry(self) -> bool:
        file_type = EXT4_FT(self.file_type)  # pyright: ignore[reportAny]
        return super().is_fake_entry or file_type == EXT4_FT.DIR_CSUM


@final
class DirectoryEntryTail(DirectoryEntryStruct):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("det_reserved_zero1", "det_reserved_zero2",)
    _fields_ = (
        ("det_reserved_zero1", c_uint32),
        ("det_rec_len", c_uint16),
        ("det_reserved_zero2", c_uint8),
        ("det_reserved_ft", c_uint8),  # EXT4_FT.DIR_CSUM
        ("det_checksum", c_uint32),
    )

    @Ext4Struct.magic.getter
    def magic(self) -> int:
        det_reserved_ft = assert_cast(self.det_reserved_ft, int)  # pyright: ignore[reportAny]
        return det_reserved_ft

    @Ext4Struct.expected_magic.getter
    def expected_magic(self) -> int:
        return int(EXT4_FT.DIR_CSUM)


@final
class DirectoryEntryHash(DirectoryEntryStruct):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("det_reserved_zero1", "det_reserved_zero2",)
    _fields_ = (
        ("hash", c_uint32),
        ("minor_hash", c_uint32),
    )
