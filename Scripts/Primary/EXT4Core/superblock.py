from ctypes import (
    c_ubyte,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
)
from typing import (
    TYPE_CHECKING,
    final,
)

from ._compat import assert_cast
from .enum import (
    DX_HASH,
    EXT2_FLAGS,
    EXT4_CHKSUM,
    EXT4_DEFM,
    EXT4_ERRORS,
    EXT4_FEATURE_COMPAT,
    EXT4_FEATURE_INCOMPAT,
    EXT4_FEATURE_RO_COMPAT,
    EXT4_FS,
    EXT4_MOUNT,
    EXT4_OS,
    EXT4_REV,
    FS_ENCRYPTION_MODE,
)
from .struct import (
    Ext4Struct,
    crc32c,
)

if TYPE_CHECKING:
    from .volume import Volume


@final
class Superblock(Ext4Struct):
    __slots__ = ()

    _pack_ = 1
    # _anonymous_ = (
    #     "s_reserved_pad",
    #     "s_reserved",
    # )
    _fields_ = (
        ("s_inodes_count", c_uint32),
        ("s_blocks_count_lo", c_uint32),
        ("s_r_blocks_count_lo", c_uint32),
        ("s_free_blocks_count_lo", c_uint32),
        ("s_free_inodes_count", c_uint32),
        ("s_first_data_block", c_uint32),
        ("s_log_block_size", c_uint32),
        ("s_log_cluster_size", c_uint32),
        ("s_blocks_per_group", c_uint32),
        ("s_clusters_per_group", c_uint32),
        ("s_inodes_per_group", c_uint32),
        ("s_mtime", c_uint32),
        ("s_wtime", c_uint32),
        ("s_mnt_count", c_uint16),
        ("s_max_mnt_count", c_uint16),
        ("s_magic", c_uint16),  # 0xEF53
        ("s_state", EXT4_FS.basetype),
        ("s_errors", EXT4_ERRORS.basetype),
        ("s_minor_rev_level", c_uint16),
        ("s_lastcheck", c_uint32),
        ("s_checkinterval", c_uint32),
        ("s_creator_os", EXT4_OS.basetype),
        ("s_rev_level", EXT4_REV.basetype),
        ("s_def_resuid", c_uint16),
        ("s_def_resgid", c_uint16),
        ("s_first_ino", c_uint32),
        ("s_inode_size", c_uint16),
        ("s_block_group_nr", c_uint16),
        ("s_feature_compat", EXT4_FEATURE_COMPAT.basetype),
        ("s_feature_incompat", EXT4_FEATURE_INCOMPAT.basetype),
        ("s_feature_ro_compat", EXT4_FEATURE_RO_COMPAT.basetype),
        ("s_uuid", c_uint8 * 16),
        ("s_volume_name", c_ubyte * 16),
        ("s_last_mounted", c_ubyte * 64),
        ("s_algorithm_usage_bitmap", c_uint32),
        ("s_prealloc_blocks", c_uint8),
        ("s_prealloc_dir_blocks", c_uint8),
        ("s_reserved_gdt_blocks", c_uint16),
        ("s_journal_uuid", c_uint8 * 16),
        ("s_journal_inum", c_uint32),
        ("s_journal_dev", c_uint32),
        ("s_last_orphan", c_uint32),
        ("s_hash_seed", c_uint32 * 4),
        ("s_def_hash_version", DX_HASH.basetype),
        ("s_jnl_backup_type", c_uint8),
        ("s_desc_size", c_uint16),
        ("s_default_mount_opts", EXT4_DEFM.basetype),
        ("s_first_meta_bg", c_uint32),
        ("s_mkfs_time", c_uint32),
        ("s_jnl_blocks", c_uint32 * 17),
        ("s_blocks_count_hi", c_uint32),
        ("s_r_blocks_count_hi", c_uint32),
        ("s_free_blocks_count_hi", c_uint32),
        ("s_min_extra_isize", c_uint16),
        ("s_want_extra_isize", c_uint16),
        ("s_flags", EXT2_FLAGS.basetype),
        ("s_raid_stride", c_uint16),
        ("s_mmp_interval", c_uint16),
        ("s_mmp_block", c_uint64),
        ("s_raid_stripe_width", c_uint32),
        ("s_log_groups_per_flex", c_uint8),
        ("s_checksum_type", EXT4_CHKSUM.basetype),
        ("s_reserved_pad", c_uint16),
        ("s_kbytes_written", c_uint64),
        ("s_snapshot_inum", c_uint32),
        ("s_snapshot_id", c_uint32),
        ("s_snapshot_r_blocks_count", c_uint64),
        ("s_snapshot_list", c_uint32),
        ("s_error_count", c_uint32),
        ("s_first_error_time", c_uint32),
        ("s_first_error_ino", c_uint32),
        ("s_first_error_block", c_uint64),
        ("s_first_error_func", c_uint8 * 32),
        ("s_first_error_line", c_uint32),
        ("s_last_error_time", c_uint32),
        ("s_last_error_ino", c_uint32),
        ("s_last_error_line", c_uint32),
        ("s_last_error_block", c_uint64),
        ("s_last_error_func", c_uint8 * 32),
        ("s_mount_opts", EXT4_MOUNT.basetype * 64),
        ("s_usr_quota_inum", c_uint32),
        ("s_grp_quota_inum", c_uint32),
        ("s_overhead_blocks", c_uint32),
        ("s_backup_bgs", c_uint32 * 2),
        ("s_encrypt_algos", FS_ENCRYPTION_MODE.basetype * 4),
        ("s_encrypt_pw_salt", c_uint8 * 16),
        ("s_lpf_ino", c_uint32),
        ("s_prj_quota_inum", c_uint32),
        ("s_checksum_seed", c_uint32),
        ("s_reserved", c_uint32 * 98),
        ("s_checksum", c_uint32),
    )

    def __init__(self, volume: "Volume", _=None) -> None:
        super().__init__(volume, 0x400)

    @property
    def has_hi(self) -> bool:
        return (self.feature_incompat & EXT4_FEATURE_INCOMPAT.IS64BIT) != 0

    @property
    def s_blocks_count(self) -> int:
        s_blocks_per_group = assert_cast(self.s_blocks_per_group, int)  # pyright: ignore[reportAny]
        s_reserved_gdt_blocks = assert_cast(self.s_reserved_gdt_blocks, int)  # pyright: ignore[reportAny]
        s_overhead_blocks = assert_cast(self.s_overhead_blocks, int)  # pyright: ignore[reportAny]
        return (
            (s_blocks_per_group) * len(self.volume.group_descriptors)
            - s_reserved_gdt_blocks
            - s_overhead_blocks
        )
        # s_blocks_count_lo = assert_cast(self.s_blocks_count_lo, int)
        # s_blocks_count_hi = assert_cast(self.s_blocks_count_hi, int)
        # if self.has_hi:
        #     return s_blocks_count_hi << 32 | s_blocks_count_lo

        # return s_blocks_count_lo

    @property
    def s_r_blocks_count(self) -> int:
        s_r_blocks_count_lo = assert_cast(self.s_r_blocks_count_lo, int)  # pyright: ignore[reportAny]
        s_r_blocks_count_hi = assert_cast(self.s_r_blocks_count_hi, int)  # pyright: ignore[reportAny]
        if self.has_hi:
            return s_r_blocks_count_hi << 32 | s_r_blocks_count_lo

        return s_r_blocks_count_lo

    @property
    def s_free_blocks_count(self) -> int:
        s_free_blocks_count_lo = assert_cast(self.s_free_blocks_count_lo, int)  # pyright: ignore[reportAny]
        s_free_blocks_count_hi = assert_cast(self.s_free_blocks_count_hi, int)  # pyright: ignore[reportAny]
        if self.has_hi:
            return s_free_blocks_count_hi << 32 | s_free_blocks_count_lo

        return s_free_blocks_count_lo

    @property
    def metadata_csum(self) -> bool:
        return self.feature_ro_compat & EXT4_FEATURE_RO_COMPAT.METADATA_CSUM != 0

    @Ext4Struct.expected_magic.getter
    def expected_magic(self) -> int:
        return 0xEF53

    @Ext4Struct.magic.getter
    def magic(self) -> int:
        s_magic = assert_cast(self.s_magic, int)  # pyright: ignore[reportAny]
        return s_magic

    @Ext4Struct.expected_checksum.getter
    def expected_checksum(self) -> int | None:
        s_checksum = assert_cast(self.s_checksum, int)  # pyright: ignore[reportAny]
        return s_checksum if self.metadata_csum else None

    @Ext4Struct.checksum.getter
    def checksum(self) -> int | None:
        return (
            crc32c(bytes(self)[: Superblock.s_checksum.offset])
            if self.metadata_csum
            else None
        )

    @property
    def feature_incompat(self) -> EXT4_FEATURE_INCOMPAT:
        return EXT4_FEATURE_INCOMPAT(self.s_feature_incompat)  # pyright: ignore[reportAny]

    @property
    def feature_compat(self) -> EXT4_FEATURE_COMPAT:
        return EXT4_FEATURE_COMPAT(self.s_feature_compat)  # pyright: ignore[reportAny]

    @property
    def feature_ro_compat(self) -> EXT4_FEATURE_RO_COMPAT:
        return EXT4_FEATURE_RO_COMPAT(self.s_feature_ro_compat)  # pyright: ignore[reportAny]

    @property
    def seed(self) -> int:
        if self.feature_incompat & EXT4_FEATURE_INCOMPAT.CSUM_SEED != 0:
            s_checksum_seed = assert_cast(self.s_checksum_seed, int)  # pyright: ignore[reportAny]
            return s_checksum_seed

        s_uuid = assert_cast(bytes(self.s_uuid), bytes)  # pyright: ignore[reportAny]
        return crc32c(s_uuid)

    @property
    def desc_size(self) -> int:
        if self.feature_incompat & EXT4_FEATURE_INCOMPAT.IS64BIT != 0:
            s_desc_size = assert_cast(self.s_desc_size, int)  # pyright: ignore[reportAny]
            return s_desc_size

        return 32
