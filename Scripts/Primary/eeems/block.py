import errno
import io
import os
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import override

if TYPE_CHECKING:
    from .extent import Extent
    from .inode import Inode
    from .volume import Volume


@final
class BlockIOBlocks:
    __slots__ = (
        "_null_block",
        "blockio",
    )

    def __init__(self, blockio: "BlockIO") -> None:
        self.blockio: BlockIO = blockio
        self._null_block: bytearray = bytearray(self.block_size)

    @property
    def block_size(self) -> int:
        return self.blockio.block_size

    @property
    def volume(self) -> "Volume":
        return self.blockio.inode.volume

    def __contains__(self, ee_block: int) -> bool:
        for extent in self.blockio.extents:
            if ee_block in extent.blocks:
                return True

        return False

    def __getitem__(self, ee_block: int) -> bytearray | bytes:
        for extent in self.blockio.extents:
            if ee_block not in extent.blocks:
                continue

            return extent.blocks[ee_block]

        return self._null_block


@final
class BlockIO(io.RawIOBase):
    __slots__ = (
        "blocks",
        "cursor",
        "inode",
    )

    def __init__(self, inode: "Inode") -> None:
        super().__init__()
        self.inode: Inode = inode
        self.cursor: int = 0
        self.blocks: BlockIOBlocks = BlockIOBlocks(self)

    def __len__(self) -> int:
        return self.inode.i_size

    @property
    def extents(self) -> "list[Extent]":
        return self.inode.extents

    @property
    def block_size(self) -> int:
        return self.inode.volume.block_size

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def seek(self, offset: int, mode: int = io.SEEK_SET) -> int:
        if mode == io.SEEK_CUR:
            offset += self.cursor

        elif mode == io.SEEK_END:
            offset += len(self)

        elif mode != io.SEEK_SET:
            raise NotImplementedError()

        if offset < 0:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))

        self.cursor = offset
        return offset

    @override
    def tell(self) -> int:
        return self.cursor

    @override
    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            size = len(self) - self.cursor

        data = self.peek(size)
        self.cursor += len(data)
        if size < len(data):
            raise OSError(errno.EIO, "Unexpected EOF")

        return data

    def peek(self, size: int = 0) -> bytes:
        if self.cursor >= len(self):
            return b""

        if self.cursor + size >= len(self):
            size = len(self) - self.cursor

        start_index = self.cursor // self.block_size
        end_index = (self.cursor + size - 1) // self.block_size
        start_offset = self.cursor % self.block_size
        end_offset = ((self.cursor + size - 1) % self.block_size) + 1
        blocks_list: list[memoryview] = []

        for i in range(start_index, end_index + 1):
            block: bytes | bytearray = self.blocks[i]
            view = memoryview(block)
            if i == start_index:
                view = view[start_offset:]

            if i == end_index:
                trim = end_offset - (start_offset if i == start_index else 0)
                view = view[:trim]

            blocks_list.append(view)

        return b"".join(blocks_list)
