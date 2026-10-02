from __future__ import annotations

import errno
import io
import os
from pathlib import PurePosixPath
from uuid import UUID

from cachetools import (
    LRUCache,
    cachedmethod,
)

from ._compat import (
    PeekableStream,
    assert_cast,
)
from .blockdescriptor import BlockDescriptor
from .enum import EXT4_INO
from .inode import (
    Directory,
    Inode,
)
from .superblock import Superblock


class InvalidStreamException(Exception):
    pass


class Inodes:
    __slots__: tuple[str, ...] = (
        "__dict__",
        "_getitem_cache",
        "_group_cache",
        "_offset_cache",
        "volume",
    )

    def __init__(self, volume: Volume) -> None:
        self.volume: Volume = volume
        self._group_cache: dict[int, tuple[int, int]] = {}
        self._offset_cache: LRUCache[int, int] = LRUCache(maxsize=32)
        self._getitem_cache: LRUCache[int, Inode] = LRUCache(maxsize=32)

    @property
    def superblock(self) -> Superblock:
        return self.volume.superblock

    @property
    def block_size(self) -> int:
        return self.volume.block_size

    @cachedmethod(lambda self: self._group_cache)  # pyright: ignore[reportAny]
    def group(self, index: int) -> tuple[int, int]:
        s_inodes_per_group = assert_cast(self.superblock.s_inodes_per_group, int)  # pyright: ignore[reportAny]
        group_index = (index - 1) // s_inodes_per_group
        table_entry_index = (index - 1) % s_inodes_per_group
        return group_index, table_entry_index

    @cachedmethod(lambda self: self._offset_cache)  # pyright: ignore[reportAny]
    def offset(self, index: int) -> int:
        group_index, table_entry_index = self.group(index)
        table_offset = (
            self.volume.group_descriptors[group_index].bg_inode_table * self.block_size
        )
        s_inode_size = assert_cast(self.superblock.s_inode_size, int)  # pyright: ignore[reportAny]
        return table_offset + table_entry_index * s_inode_size

    @cachedmethod(lambda self: self._getitem_cache)  # pyright: ignore[reportAny]
    def __getitem__(self, index: int) -> Inode:
        offset = self.offset(index)
        return Inode(self.volume, offset, index)


class Volume:
    __slots__: tuple[str, ...] = (
        "cursor",
        "group_descriptors",
        "ignore_attr_name_index",
        "ignore_checksum",
        "ignore_flags",
        "ignore_magic",
        "inodes",
        "offset",
        "stream",
        "superblock",
    )

    def __init__(  # noqa: PLR0917
        self,
        stream: PeekableStream,
        offset: int = 0,
        ignore_flags: bool = False,
        ignore_magic: bool = False,
        ignore_checksum: bool = False,
        ignore_attr_name_index: bool = False,
    ) -> None:
        errors: list[str] = []
        for name in ("read", "peek", "tell", "seek"):
            if not hasattr(stream, name):
                errors.append(f"{name} method missing")

            elif not callable(getattr(stream, name)):  # pyright: ignore[reportAny]
                errors.append(f"{name} is not a method")

        if errors:
            raise InvalidStreamException(", ".join(errors))

        self.stream: PeekableStream = stream
        self.offset: int = offset
        self.cursor: int = 0
        self.ignore_flags: bool = ignore_flags
        self.ignore_magic: bool = ignore_magic
        self.ignore_checksum: bool = ignore_checksum
        self.ignore_attr_name_index: bool = ignore_attr_name_index
        self.superblock: Superblock = Superblock(self)
        self.superblock.verify()
        self.group_descriptors: list[BlockDescriptor] = []
        block_size = self.block_size
        table_offset = (self.superblock.offset // block_size + 1) * block_size
        s_inodes_count = assert_cast(self.superblock.s_inodes_count, int)  # pyright: ignore[reportAny]
        s_inodes_per_group = assert_cast(self.superblock.s_inodes_per_group, int)  # pyright: ignore[reportAny]
        for index in range(
            (s_inodes_count + s_inodes_per_group - 1) // s_inodes_per_group,
        ):
            descriptor = BlockDescriptor(
                self,
                table_offset + (index * self.superblock.desc_size),
                index,
            )
            descriptor.verify()
            self.group_descriptors.insert(index, descriptor)

        self.inodes: Inodes = Inodes(self)

    def __len__(self) -> int:
        _ = self.stream.seek(0, io.SEEK_END)
        return self.stream.tell() - self.offset

    @property
    def bad_blocks(self) -> Inode:
        return self.inodes[EXT4_INO.BAD]

    @property
    def root(self) -> Directory:
        return assert_cast(self.inodes[EXT4_INO.ROOT], Directory)

    @property
    def user_quota(self) -> Inode:
        return self.inodes[EXT4_INO.USR_QUOTA]

    @property
    def group_quota(self) -> Inode:
        return self.inodes[EXT4_INO.GRP_QUOTA]

    @property
    def boot_loader(self) -> Inode:
        return self.inodes[EXT4_INO.BOOT_LOADER]

    @property
    def undelete_directory(self) -> Inode:
        return self.inodes[EXT4_INO.UNDEL_DIR]

    @property
    def journal(self) -> Inode:
        return self.inodes[EXT4_INO.JOURNAL]

    @property
    def has_hi(self) -> int:
        return self.superblock.has_hi

    @property
    def uuid(self) -> UUID:
        s_uuid = assert_cast(bytes(self.superblock.s_uuid), bytes)  # pyright: ignore[reportAny]
        return UUID(bytes=s_uuid)

    @property
    def seed(self) -> int:
        return self.superblock.seed

    @property
    def block_size(self) -> int:
        s_log_block_size = assert_cast(self.superblock.s_log_block_size, int)  # pyright: ignore[reportAny]
        return int(
            2
            ** (  # pyright: ignore[reportAny]
                10 + s_log_block_size
            )
        )

    def seek(self, offset: int, mode: int = io.SEEK_SET) -> int:
        if mode == io.SEEK_SET:
            seek = offset

        elif mode == io.SEEK_CUR:
            seek = self.cursor + offset

        elif mode == io.SEEK_END:
            seek = len(self) + offset

        else:
            raise NotImplementedError(f"Seek mode {mode} not implemented")

        if seek < 0 or seek > len(self):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))

        self.cursor = seek
        return self.cursor

    def read(self, size: int) -> bytes:
        _ = self.stream.seek(self.offset + self.cursor)
        remaining = size
        data = bytearray()
        while remaining > 0:
            chunk = self.stream.read(remaining)
            if not chunk:
                break

            data.extend(chunk)
            remaining -= len(chunk)

        data = data[:size]
        self.cursor += len(data)
        return bytes(data)

    def peek(self, size: int) -> bytes:
        _ = self.stream.seek(self.offset + self.cursor)
        remaining = size
        data = bytearray()
        while remaining > 0:
            chunk = self.stream.peek(remaining)
            if not chunk:
                break

            data.extend(chunk)
            remaining -= len(chunk)
            _ = self.stream.seek(len(chunk), io.SEEK_CUR)

        data = data[:size]
        _ = self.stream.seek(self.offset + self.cursor)
        return bytes(data)

    def tell(self) -> int:
        return self.cursor

    def block_read(self, index: int, count: int = 1) -> bytes:
        assert index >= 0
        assert count > 0
        block_size = self.block_size  # Only calculate once
        _ = self.seek(index * block_size)
        return self.read(count * block_size)

    @staticmethod
    def path_tuple(path: str | bytes) -> tuple[bytes, ...]:
        if isinstance(path, bytes):
            path = path.decode("utf-8")

        return tuple(x.encode("utf-8") for x in PurePosixPath(path).parts[1:])

    def inode_at(self, path: str | bytes) -> Inode:
        return self.root.inode_at(b"/".join(self.path_tuple(path)))
