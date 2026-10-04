import warnings
from collections.abc import Generator
from ctypes import (
    c_uint8,
    c_uint16,
    c_uint32,
    sizeof,
)
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import (
    assert_cast,
    override,
)
from .enum import (
    EXT4_FEATURE_INCOMPAT,
    EXT4_FL,
)
from .struct import (
    Ext4Struct,
    crc32c,
)

if TYPE_CHECKING:
    from .inode import Inode


class ExtendedAttributeError(Exception):
    pass


class ExtendedAttributeBase(Ext4Struct):
    __slots__: tuple[str, ...] = (
        "data_size",
        "inode",
    )

    def __init__(self, inode: "Inode", offset: int, size: int) -> None:
        self.inode: Inode = inode
        self.data_size: int = size
        super().__init__(inode.volume, offset)


class ExtendedAttributeIBodyHeader(ExtendedAttributeBase):
    __slots__: tuple[str, ...] = ()
    _pack_ = 1  # pyright: ignore[reportUnannotatedClassAttribute]
    # _anonymous_ = ()
    _fields_ = (  # pyright: ignore[reportUnannotatedClassAttribute]
        ("h_magic", c_uint32),  # 0xEA020000
    )

    @ExtendedAttributeBase.ignore_magic.getter
    def ignore_magic(self) -> bool:
        return False

    @ExtendedAttributeBase.magic.getter
    def magic(self) -> int:
        h_magic = assert_cast(self.h_magic, int)  # pyright: ignore[reportAny]
        return h_magic

    @ExtendedAttributeBase.expected_magic.getter
    def expected_magic(self) -> int:
        return 0xEA020000

    def value_offset(self, entry: "ExtendedAttributeEntry") -> int:
        e_value_offs = assert_cast(entry.e_value_offs, int)  # pyright: ignore[reportAny]
        return self.offset + sizeof(self) + e_value_offs

    def __iter__(self) -> Generator[tuple[str, bytes], None, None]:
        offset = self.offset + (4 * ((sizeof(self) + 3) // 4))
        i = 0
        while i < self.data_size:
            entry = ExtendedAttributeEntry(self.inode, offset + i, self.data_size - i)
            e_name_len = assert_cast(entry.e_name_len, int)  # pyright: ignore[reportAny]
            e_name_index = assert_cast(entry.e_name_index, int)  # pyright: ignore[reportAny]
            e_value_offs = assert_cast(entry.e_value_offs, int)  # pyright: ignore[reportAny]
            if (e_name_len | e_name_index | e_value_offs | entry.value_inum) == 0:
                break

            value: bytes
            e_value_size = assert_cast(entry.e_value_size, int)  # pyright: ignore[reportAny]
            if entry.value_inum != 0:
                inode = self.volume.inodes[entry.value_inum]
                if not inode.has_flag(EXT4_FL.EA_INODE):
                    message = f"Inode {inode.i_no:d} is not marked as large extended attribute value"
                    if not self.volume.ignore_flags:
                        raise ExtendedAttributeError(message)

                    warnings.warn(message, RuntimeWarning)

                # TODO determine if e_value_size or i_size are required to limit results?
                value = inode.open().read()

            elif e_value_size != 0:
                value_offset = self.value_offset(entry)
                if value_offset + e_value_size > self.offset + self.data_size:
                    value = b""

                else:
                    _ = self.volume.seek(value_offset)
                    value = self.volume.read(e_value_size)

            else:
                value = b""

            yield entry.name_str, value
            i += 4 * ((entry.size + 3) // 4)


@final
class ExtendedAttributeHeader(ExtendedAttributeIBodyHeader):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("h_reserved)
    _fields_ = (
        ("h_refcount", c_uint32),
        ("h_blocks", c_uint32),
        ("h_hash", c_uint32),
        ("h_checksum", c_uint32),
        ("h_reserved", c_uint32 * 3),
    )

    @override
    def verify(self) -> None:
        super().verify()
        h_blocks = assert_cast(self.h_blocks, int)  # pyright: ignore[reportAny]
        if h_blocks != 1:
            raise ExtendedAttributeError(
                f"Invalid number of xattr blocks at offset 0x{self.offset:X} of inode "
                + f"{self.inode.i_no:d}: {h_blocks:d} (expected 1)"
            )

    @override
    def value_offset(self, entry: "ExtendedAttributeEntry") -> int:
        e_value_offs = assert_cast(entry.e_value_offs, int)  # pyright: ignore[reportAny]
        return self.offset + e_value_offs

    @ExtendedAttributeIBodyHeader.expected_checksum.getter
    def expected_checksum(self) -> int | None:
        h_checksum = assert_cast(self.h_checksum, int)  # pyright: ignore[reportAny]
        if not h_checksum:
            return None

        return h_checksum

    @ExtendedAttributeIBodyHeader.checksum.getter
    def checksum(self) -> int | None:
        h_checksum = assert_cast(self.h_checksum, int)  # pyright: ignore[reportAny]
        if not h_checksum:
            return None

        csum = crc32c(
            bytes(self)[: ExtendedAttributeHeader.h_checksum.offset], self.volume.seed
        )
        csum = crc32c(b"\0" * ExtendedAttributeHeader.h_checksum.size, csum)
        return crc32c(bytes(self)[: ExtendedAttributeHeader.h_reserved.offset], csum)


@final
class ExtendedAttributeEntry(ExtendedAttributeBase):
    __slots__: tuple[str, ...] = ("e_name",)

    NAME_INDICES = (
        "",
        "user.",
        "system.posix_acl_access",
        "system.posix_acl_default",
        "trusted.",
        "",
        "security.",
        "system.",
        "system.richacl",
    )
    _pack_ = 1
    # _anonymous_ = ("h_reserved)
    _fields_ = (
        ("e_name_len", c_uint8),
        ("e_name_index", c_uint8),
        ("e_value_offs", c_uint16),
        ("e_value_inum", c_uint32),
        ("e_value_size", c_uint32),
        ("e_hash", c_uint32),
        # ("e_name", c_char * self.e_name_len),
    )

    def __init__(self, inode: "Inode", offset: int, size: int) -> None:
        self.e_name: bytes = b""
        super().__init__(inode, offset, size)

    @override
    def read_from_volume(self) -> None:
        super().read_from_volume()
        e_name_len = assert_cast(self.e_name_len, int)  # pyright: ignore[reportAny]
        self.e_name = self.volume.stream.read(e_name_len)

    @ExtendedAttributeBase.size.getter
    def size(self) -> int:
        e_name_len = assert_cast(self.e_name_len, int)  # pyright: ignore[reportAny]
        return sizeof(self) + e_name_len

    @property
    def name_str(self) -> str:
        e_name_index = assert_cast(self.e_name_index, int)  # pyright: ignore[reportAny]
        name_index = e_name_index
        if 0 > name_index or name_index >= len(ExtendedAttributeEntry.NAME_INDICES):
            msg = f"Unknown attribute prefix {e_name_index:d}"
            if self.volume.ignore_attr_name_index:
                warnings.warn(msg, RuntimeWarning)
                name_index = 0
            else:
                raise ExtendedAttributeError(msg)

        return ExtendedAttributeEntry.NAME_INDICES[name_index] + self.e_name.decode(
            "iso-8859-2"
        )

    @property
    def value_inum(self) -> int:
        e_value_inum = assert_cast(self.e_value_inum, int)  # pyright: ignore[reportAny]
        return (
            e_value_inum
            if (
                self.volume.superblock.feature_incompat & EXT4_FEATURE_INCOMPAT.EA_INODE
            )
            != 0
            else 0
        )
