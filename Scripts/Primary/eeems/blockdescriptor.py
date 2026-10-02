from ctypes import (
    c_uint16,
    c_uint32,
)
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import assert_cast
from .enum import EXT4_BG
from .struct import (
    Ext4Struct,
    crc32c,
)
from .superblock import Superblock

if TYPE_CHECKING:
    from .volume import Volume


@final
class BlockDescriptor(Ext4Struct):
    __slots__ = ("bg_no",)

    _pack_ = 1
    # _anonymous_ = ("bg_reserved",)
    _fields_ = (
        ("bg_block_bitmap_lo", c_uint32),
        ("bg_inode_bitmap_lo", c_uint32),
        ("bg_inode_table_lo", c_uint32),
        ("bg_free_blocks_count_lo", c_uint16),
        ("bg_free_inodes_count_lo", c_uint16),
        ("bg_used_dirs_count_lo", c_uint16),
        ("bg_flags", EXT4_BG.basetype),
        ("bg_exclude_bitmap_lo", c_uint32),
        ("bg_block_bitmap_csum_lo", c_uint16),
        ("bg_inode_bitmap_csum_lo", c_uint16),
        ("bg_itable_unused_lo", c_uint16),
        ("bg_checksum", c_uint16),
        ("bg_block_bitmap_hi", c_uint32),
        ("bg_inode_bitmap_hi", c_uint32),
        ("bg_inode_table_hi", c_uint32),
        ("bg_free_blocks_count_hi", c_uint16),
        ("bg_free_inodes_count_hi", c_uint16),
        ("bg_used_dirs_count_hi", c_uint16),
        ("bg_itable_unused_hi", c_uint16),
        ("bg_exclude_bitmap_hi", c_uint32),
        ("bg_block_bitmap_csum_hi", c_uint16),
        ("bg_inode_bitmap_csum_hi", c_uint16),
        ("bg_reserved", c_uint32),
    )

    def __init__(self, volume: "Volume", offset: int, bg_no: int) -> None:
        super().__init__(volume, offset)
        self.bg_no: int = bg_no

    @property
    def bg_block_bitmap(self) -> int:
        bg_block_bitmap_lo = assert_cast(self.bg_block_bitmap_lo, int)  # pyright: ignore[reportAny]
        bg_block_bitmap_hi = assert_cast(self.bg_block_bitmap_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_block_bitmap_hi << 32 | bg_block_bitmap_lo

        return bg_block_bitmap_lo

    @property
    def bg_inode_bitmap(self) -> int:
        bg_inode_bitmap_lo = assert_cast(self.bg_inode_bitmap_lo, int)  # pyright: ignore[reportAny]
        bg_inode_bitmap_hi = assert_cast(self.bg_inode_bitmap_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_inode_bitmap_hi << 32 | bg_inode_bitmap_lo

        return bg_inode_bitmap_lo

    @property
    def bg_free_blocks_count(self) -> int:
        bg_free_blocks_count_lo = assert_cast(self.bg_free_blocks_count_lo, int)  # pyright: ignore[reportAny]
        bg_free_blocks_count_hi = assert_cast(self.bg_free_blocks_count_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_free_blocks_count_hi << 16 | bg_free_blocks_count_lo

        return bg_free_blocks_count_lo

    @property
    def bg_free_inodes_count(self) -> int:
        bg_free_inodes_count_lo = assert_cast(self.bg_free_inodes_count_lo, int)  # pyright: ignore[reportAny]
        bg_free_inodes_count_hi = assert_cast(self.bg_free_inodes_count_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_free_inodes_count_hi << 16 | bg_free_inodes_count_lo

        return bg_free_inodes_count_lo

    @property
    def bg_exclude_bitmap(self) -> int:
        bg_exclude_bitmap_lo = assert_cast(self.bg_exclude_bitmap_lo, int)  # pyright: ignore[reportAny]
        bg_exclude_bitmap_hi = assert_cast(self.bg_exclude_bitmap_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_exclude_bitmap_hi << 32 | bg_exclude_bitmap_lo

        return bg_exclude_bitmap_lo

    @property
    def bg_used_dirs_count(self) -> int:
        bg_used_dirs_count_lo = assert_cast(self.bg_used_dirs_count_lo, int)  # pyright: ignore[reportAny]
        bg_used_dirs_count_hi = assert_cast(self.bg_used_dirs_count_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_used_dirs_count_hi << 16 | bg_used_dirs_count_lo

        return bg_used_dirs_count_lo

    @property
    def bg_block_bitmap_csum(self) -> int:
        bg_block_bitmap_csum_lo = assert_cast(self.bg_block_bitmap_csum_lo, int)  # pyright: ignore[reportAny]
        bg_block_bitmap_csum_hi = assert_cast(self.bg_block_bitmap_csum_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_block_bitmap_csum_hi << 16 | bg_block_bitmap_csum_lo

        return bg_block_bitmap_csum_lo

    @property
    def bg_inode_bitmap_csum(self) -> int:
        bg_inode_bitmap_csum_lo = assert_cast(self.bg_inode_bitmap_csum_lo, int)  # pyright: ignore[reportAny]
        bg_inode_bitmap_csum_hi = assert_cast(self.bg_inode_bitmap_csum_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_inode_bitmap_csum_hi << 16 | bg_inode_bitmap_csum_lo

        return bg_inode_bitmap_csum_lo

    @property
    def bg_itable_unused(self) -> int:
        bg_itable_unused_lo = assert_cast(self.bg_itable_unused_lo, int)  # pyright: ignore[reportAny]
        bg_itable_unused_hi = assert_cast(self.bg_itable_unused_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return bg_itable_unused_hi << 16 | bg_itable_unused_lo

        return bg_itable_unused_lo

    @property
    def bg_inode_table(self) -> int:
        bg_inode_table_lo = assert_cast(self.bg_inode_table_lo, int)  # pyright: ignore[reportAny]
        bg_inode_table_hi = assert_cast(self.bg_inode_table_hi, int)  # pyright: ignore[reportAny]
        if self.volume.has_hi:
            return (bg_inode_table_hi << 32) + bg_inode_table_lo

        return bg_inode_table_lo

    @property
    def superblock(self) -> Superblock:
        return self.volume.superblock

    @Ext4Struct.checksum.getter
    def checksum(self) -> int:
        csum = crc32c(self.bg_no.to_bytes(4, "little"), self.volume.seed)
        csum = crc32c(bytes(self)[: BlockDescriptor.bg_checksum.offset], csum)
        if self.volume.has_hi:
            csum = crc32c(b"\x00\x00", csum)
            csum = crc32c(
                bytes(self)[BlockDescriptor.bg_block_bitmap_hi.offset :], csum
            )
        return csum & 0xFFFF

    @Ext4Struct.expected_checksum.getter
    def expected_checksum(self) -> int:
        bg_checksum = assert_cast(self.bg_checksum, int)  # pyright: ignore[reportAny]
        return bg_checksum
