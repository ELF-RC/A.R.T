from collections.abc import Iterator
from ctypes import (
    c_uint16,
    c_uint32,
    sizeof,
)
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import assert_cast
from .struct import (
    Ext4Struct,
    crc32c,
)

if TYPE_CHECKING:
    from .inode import Inode
    from .volume import Volume


@final
class ExtentBlocks:
    __slots__: tuple[str, ...] = (
        "_null_block",
        "extent",
    )

    def __init__(self, extent: "Extent") -> None:
        self.extent: Extent = extent
        self._null_block: bytearray = bytearray(self.block_size)

    @property
    def block_size(self) -> int:
        return self.extent.block_size

    @property
    def volume(self) -> "Volume":
        return self.extent.volume

    @property
    def ee_start(self) -> int:
        return self.extent.ee_start

    @property
    def ee_block(self) -> int:
        ee_block = assert_cast(self.extent.ee_block, int)  # pyright: ignore[reportAny]
        return ee_block

    @property
    def ee_len(self) -> int:
        # Don't use ee_len as we want to know the value for
        # uninitialized blocks as well
        return self.extent.len

    @property
    def is_initialized(self) -> bool:
        return self.extent.is_initialized

    def __contains__(self, ee_block: int) -> bool:
        return self.ee_block <= ee_block < self.ee_block + self.ee_len

    def __getitem__(self, ee_block: int) -> bytearray | bytes:
        block_size = self.block_size
        if not self.is_initialized or ee_block not in self:
            # Uninitialized
            return self._null_block

        disk_block: int = self.ee_start + (ee_block - self.ee_block)
        _ = self.volume.seek(disk_block * block_size)
        return self.volume.read(block_size)

    def __iter__(self) -> Iterator[int]:
        return iter(range(self.ee_block, self.ee_block + self.ee_len))

    def __len__(self) -> int:
        return self.ee_len


@final
class ExtentHeader(Ext4Struct):
    __slots__ = (
        "extents",
        "indices",
        "tail",
        "tree",
    )

    _pack_ = 1
    # _anonymous_ = ()
    _fields_ = (
        ("eh_magic", c_uint16),
        ("eh_entries", c_uint16),
        ("eh_max", c_uint16),
        ("eh_depth", c_uint16),
        ("eh_generation", c_uint32),
    )

    def __init__(self, tree: "ExtentTree", offset: int) -> None:
        self.tree: ExtentTree = tree
        super().__init__(self.inode.volume, offset)

        self.indices: list[ExtentIndex] = []
        self.extents: list[Extent] = []

        offset = self.offset + self.size
        eh_entries = assert_cast(self.eh_entries, int)  # pyright: ignore[reportAny]
        for i in range(eh_entries):
            if self.eh_depth == 0:
                self.extents.append(Extent(self, offset, i))
                offset += sizeof(Extent)

            else:
                self.indices.append(ExtentIndex(self, offset, i))
                offset += sizeof(ExtentIndex)

        self.indices.sort(key=lambda entry: entry.ei_no)
        self.extents.sort(key=lambda entry: entry.ee_no)
        i_block = type(self.inode).i_block
        i_block_offset = self.inode.offset + i_block.offset
        self.tail = (
            ExtentTail(self, offset)
            if offset in range(i_block_offset, i_block_offset + i_block.size)
            else None
        )

    @property
    def inode(self) -> "Inode":
        return self.tree.inode

    @Ext4Struct.expected_magic.getter
    def expected_magic(self) -> int:
        return 0xF30A

    @Ext4Struct.magic.getter
    def magic(self) -> int:
        eh_magic = assert_cast(self.eh_magic, int)  # pyright: ignore[reportAny]
        return eh_magic

    @Ext4Struct.expected_checksum.getter
    def expected_checksum(self) -> int | None:
        if self.tail is None:
            return None

        et_checksum = assert_cast(self.tail.et_checksum, int)  # pyright: ignore[reportAny]
        if not et_checksum:
            return None

        return et_checksum

    @property
    def seed(self) -> int:
        return self.inode.seed

    @Ext4Struct.checksum.getter
    def checksum(self) -> int | None:
        if self.expected_checksum is None:
            return None

        assert self.tail is not None
        _ = self.volume.seek(self.offset)
        data = self.volume.read(self.tail.offset - self.offset)
        return crc32c(data, self.seed)


@final
class ExtentIndex(Ext4Struct):
    __slots__ = (
        "ei_no",
        "header",
    )

    _pack_ = 1
    # _anonymous_ = ("ei_unused",)
    _fields_ = (
        ("ei_block", c_uint32),
        ("ei_leaf_lo", c_uint32),
        ("ei_leaf_hi", c_uint16),
        ("ei_unused", c_uint16),
    )

    def __init__(self, header: ExtentHeader, offset: int, ei_no: int) -> None:
        self.ei_no: int = ei_no
        self.header: ExtentHeader = header
        super().__init__(self.inode.volume, offset)

    @property
    def ei_leaf(self) -> int:
        ei_leaf_lo = assert_cast(self.ei_leaf_lo, int)  # pyright: ignore[reportAny]
        ei_leaf_hi = assert_cast(self.ei_leaf_hi, int)  # pyright: ignore[reportAny]
        return ei_leaf_hi << 32 | ei_leaf_lo

    @property
    def tree(self) -> "ExtentTree":
        return self.header.tree

    @property
    def inode(self) -> "Inode":
        return self.tree.inode


@final
class Extent(Ext4Struct):
    __slots__ = (
        "blocks",
        "ee_no",
        "header",
    )

    _pack_ = 1
    # _anonymous_ = ("ei_unused",)
    _fields_ = (
        ("ee_block", c_uint32),
        ("ee_len", c_uint16),
        ("ee_start_hi", c_uint16),
        ("ee_start_lo", c_uint32),
    )

    def __init__(self, header: ExtentHeader, offset: int, ee_no: int) -> None:
        super().__init__(header.inode.volume, offset)
        self.ee_no: int = ee_no
        self.header: ExtentHeader = header
        self.blocks: ExtentBlocks = ExtentBlocks(self)

    @property
    def ee_start(self) -> int:
        ee_start_lo = assert_cast(self.ee_start_lo, int)  # pyright: ignore[reportAny]
        ee_start_hi = assert_cast(self.ee_start_hi, int)  # pyright: ignore[reportAny]
        return ee_start_hi << 32 | ee_start_lo

    @property
    def tree(self) -> "ExtentTree":
        return self.header.tree

    @property
    def is_initialized(self) -> bool:
        return self.ee_len < 32768  # pyright: ignore[reportAny]

    @property
    def len(self) -> int:
        return self.ee_len if self.is_initialized else self.ee_len - 32768  # pyright: ignore[reportAny]

    @property
    def inode(self) -> "Inode":
        return self.tree.inode

    @property
    def block_size(self) -> int:
        return self.volume.block_size

    def read(self) -> bytes:
        return b"".join(self.blocks[b] for b in self.blocks)


@final
class ExtentTail(Ext4Struct):
    __slots__ = ("header",)

    _pack_ = 1
    _fields_ = (("et_checksum", c_uint32),)

    def __init__(self, header: ExtentHeader, offset: int) -> None:
        self.header: ExtentHeader = header
        super().__init__(self.inode.volume, offset)

    @property
    def tree(self) -> "ExtentTree":
        return self.header.tree

    @property
    def inode(self) -> "Inode":
        return self.tree.inode


class ExtentTree:
    __slots__: tuple[str, ...] = (
        "headers",
        "inode",
    )

    def __init__(self, inode: "Inode") -> None:
        self.inode: Inode = inode
        self.headers: list[ExtentHeader] = []
        if not self.has_extents:
            return

        to_process = [self.offset]
        while to_process:
            header_offset = to_process.pop(0)
            header = ExtentHeader(self, header_offset)
            self.headers.append(header)
            for index in header.indices:
                to_process.append(index.ei_leaf * self.volume.block_size)

    @property
    def volume(self) -> "Volume":
        return self.inode.volume

    @property
    def offset(self) -> int:
        return self.inode.offset + type(self.inode).i_block.offset

    @property
    def has_extents(self) -> bool:
        return self.inode.has_extents

    def verify(self) -> None:
        pass

    def validate(self) -> None:
        for header in self.headers:
            header.validate()

    @property
    def extents(self) -> list[Extent]:
        extents: list[Extent] = []
        for header in self.headers:
            extents += header.extents

        return extents

    @property
    def indices(self) -> list[ExtentIndex]:
        indices: list[ExtentIndex] = []
        for header in self.headers:
            indices += header.indices

        return indices
