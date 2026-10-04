from ctypes import (
    c_uint8,
    c_uint16,
    c_uint32,
)
from typing import (
    TYPE_CHECKING,
    Any,
    Self,
    cast,
    final,
)

from ._compat import override

if TYPE_CHECKING:
    from .struct import SimpleCData


def TypedEnumerationType(_type: type["SimpleCData"]):  # noqa: ANN201
    class EnumerationType(type(_type)):  # type: ignore  # pyright: ignore[reportGeneralTypeIssues, reportUntypedBaseClass]
        def __new__(
            cls,
            name: str,
            bases: tuple[type, ...],
            data: dict[str, Any],  # pyright: ignore[reportExplicitAny]
        ) -> Self:
            _members_: dict[str, Any]  # pyright: ignore[reportExplicitAny]
            if "_members_" not in data:
                _members_ = {}
                for key, value in data.items():  # pyright: ignore[reportAny]
                    if not key.startswith("_"):
                        _members_[key] = value

                data["_members_"] = _members_

            else:
                _members_ = cast(dict[str, Any], data["_members_"])  # pyright: ignore[reportExplicitAny]

            data["_reverse_map_"] = {v: k for k, v in _members_.items()}  # pyright: ignore[reportAny]
            return type(_type).__new__(cls, name, bases, data)  # pyright: ignore[reportCallIssue, reportUnknownVariableType]

        @override
        def __repr__(self) -> str:
            return f"<Enumeration {self.__name__}>"  # pyright: ignore[reportUnknownMemberType]

    return EnumerationType


def TypedCEnumeration(_type: type["SimpleCData"]):  # noqa: ANN201
    class CEnumeration(_type, metaclass=TypedEnumerationType(_type)):  # pyright: ignore[reportGeneralTypeIssues, reportUntypedBaseClass] # noqa: PLW1641
        _members_: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]  # noqa: RUF012
        basetype: type["SimpleCData"] = _type

        @override
        def __repr__(self) -> str:
            value = self.value  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return f"<{self.__class__.__name__}.{self._reverse_map_.get(value, '(unknown)')}: {value}>"  # pyright: ignore[reportUnknownMemberType]

        @override
        def __eq__(self, other: object) -> bool:
            if isinstance(other, int):
                return self.value == other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value == other.value  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]

        @override
        def __ne__(self, other: object) -> bool:
            if isinstance(other, int):
                return self.value != other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value != other.value  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]

        @override
        def __and__(self, other: Any) -> int | bool:  # pyright: ignore[reportExplicitAny, reportAny, reportGeneralTypeIssues]
            if isinstance(other, int):
                return self.value & other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value & other.value  # pyright: ignore[reportUnknownVariableType, reportAny, reportUnknownMemberType]

        @override
        def __gt__(self, other: Any) -> bool:  # pyright: ignore[reportGeneralTypeIssues, reportAny, reportExplicitAny]
            if isinstance(other, int):
                return self.value > other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value > other.value  # pyright: ignore[reportUnknownVariableType, reportAny, reportUnknownMemberType]

        @override
        def __lt__(self, other: Any) -> bool:  # pyright: ignore[reportGeneralTypeIssues, reportExplicitAny, reportAny]
            if isinstance(other, int):
                return self.value < other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value < other.value  # pyright: ignore[reportUnknownVariableType, reportAny, reportUnknownMemberType]

        @override
        def __ge__(self, other: Any) -> bool:  # pyright: ignore[reportGeneralTypeIssues, reportExplicitAny, reportAny]
            if isinstance(other, int):
                return self.value >= other  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

            return isinstance(self, type(other)) and self.value >= other.value  # pyright: ignore[reportUnknownVariableType, reportAny, reportUnknownMemberType]

        @override
        def __le__(self, other: Any) -> bool:  # pyright: ignore[reportGeneralTypeIssues, reportAny, reportExplicitAny]
            if isinstance(other, int):
                return self.value <= other  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

            return isinstance(self, type(other)) and self.value <= other.value  # pyright: ignore[reportUnknownVariableType, reportAny, reportUnknownMemberType]

        # TODO Add the rest
        #  See https://docs.python.org/3/reference/datamodel.html#emulating-numeric-types

    return CEnumeration


@final
class EXT4_INO(TypedCEnumeration(c_uint32)):
    BAD = 1  # Bad blocks inode
    ROOT = 2  # Root inode
    USR_QUOTA = 3  # User quota inode
    GRP_QUOTA = 4  # Group quota inode
    BOOT_LOADER = 5  # Boot loader inode
    UNDEL_DIR = 6  # Undelete directory inode
    RESIZE = 7  # Reserved group descriptors inode
    JOURNAL = 8  # Journal inode
    GOOD_OLD_FIRST = 11


@final
class EXT4_FS(TypedCEnumeration(c_uint16)):
    VALID = 0x0001  # Unmounted cleanly
    ERROR = 0x0002  # Errors detected
    ORPHAN = 0x0004  # Orphans being recovered
    FC_REPLAY = 0x0020  # Fast commit replay ongoing


@final
class EXT4_ERRORS(TypedCEnumeration(c_uint16)):
    DEFAULT = CONTINUE = 1  # Continue execution
    RO = 2  # Remount fs read-only
    PANIC = 3  # Panic


@final
class EXT4_OS(TypedCEnumeration(c_uint32)):
    LINUX = 0
    HURD = 1
    MASIX = 2
    FREEBSD = 3
    LITES = 4


@final
class EXT4_REV(TypedCEnumeration(c_uint32)):
    EXT4_GOOD_OLD_REV = 0  # The good old (original) format
    EXT4_DYNAMIC_REV = 1  # V2 format w/ dynamic inode sizes
    EXT4_MAX_SUPP_REV = EXT4_DYNAMIC_REV


@final
class EXT4_FEATURE_COMPAT(TypedCEnumeration(c_uint32)):
    DIR_PREALLOC = 0x0001
    IMAGIC_INODES = 0x0002
    HAS_JOURNAL = 0x0004
    EXT_ATTR = 0x0008
    RESIZE_INODE = 0x0010
    DIR_INDEX = 0x0020
    SPARSE_SUPER2 = 0x0200
    FAST_COMMIT = 0x0400
    STABLE_INODES = 0x0800
    ORPHAN_FILE = 0x1000  # Orphan file exists


@final
class EXT4_FEATURE_INCOMPAT(TypedCEnumeration(c_uint32)):
    COMPRESSION = 0x0001
    FILETYPE = 0x0002
    RECOVER = 0x0004  # Needs recovery
    JOURNAL_DEV = 0x0008  # Journal device
    META_BG = 0x0010
    EXTENTS = 0x0040  # extents support
    IS64BIT = 0x0080
    MMP = 0x0100
    FLEX_BG = 0x0200
    EA_INODE = 0x0400  # EA in inode
    DIRDATA = 0x1000  # data in dirent
    CSUM_SEED = 0x2000
    LARGEDIR = 0x4000  # >2GB or 3-lvl htree
    INLINE_DATA = 0x8000  # data in inode
    ENCRYPT = 0x10000
    CASEFOLD = 0x20000


@final
class EXT4_FEATURE_RO_COMPAT(TypedCEnumeration(c_uint32)):
    SPARSE_SUPER = 0x0001
    LARGE_FILE = 0x0002
    BTREE_DIR = 0x0004
    HUGE_FILE = 0x0008
    GDT_CSUM = 0x0010
    DIR_NLINK = 0x0020
    EXTRA_ISIZE = 0x0040
    QUOTA = 0x0100
    BIGALLOC = 0x0200
    METADATA_CSUM = 0x0400
    READONLY = 0x1000
    PROJECT = 0x2000
    VERITY = 0x8000
    ORPHAN_PRESENT = 0x10000  # Orphan file may be non-empty


@final
class DX_HASH(TypedCEnumeration(c_uint8)):
    LEGACY = 0
    HALF_MD4 = 1
    TEA = 2
    LEGACY_UNSIGNED = 3
    HALF_MD4_UNSIGNED = 4
    TEA_UNSIGNED = 5
    SIPHASH = 6


@final
class EXT4_DEFM(TypedCEnumeration(c_uint32)):
    DEBUG = 0x0001
    BSDGROUPS = 0x0002
    XATTR_USER = 0x0004
    ACL = 0x0008
    UID16 = 0x0010
    JMODE = 0x0060
    JMODE_DATA = 0x0020
    JMODE_ORDERED = 0x0040
    JMODE_WBACK = 0x0060
    NOBARRIER = 0x0100
    BLOCK_VALIDITY = 0x0200
    DISCARD = 0x0400
    NODELALLOC = 0x0800


@final
class EXT2_FLAGS(TypedCEnumeration(c_uint32)):
    SIGNED_HASH = 0x0001  # Signed dirhash in use
    UNSIGNED_HASH = 0x0002  # Unsigned dirhash in use
    TEST_FILESYS = 0x0004  # to test development code


@final
class EXT4_CHKSUM(TypedCEnumeration(c_uint8)):
    CRC32C = 1


@final
class EXT4_MOUNT(TypedCEnumeration(c_uint8)):
    NO_MBCACHE = 0x00001  # Do not use mbcache
    GRPID = 0x00004  # Create files with directory's group
    DEBUG = 0x00008  # Some debugging messages
    ERRORS_CONT = 0x00010  # Continue on errors
    ERRORS_RO = 0x00020  # Remount fs ro on errors
    ERRORS_PANIC = 0x00040  # Panic on errors
    ERRORS_MASK = 0x00070
    MINIX_DF = 0x00080  # Mimics the Minix statfs
    NOLOAD = 0x00100  # Don't use existing journa
    DAX_ALWAYS = 0x00200  # Direct Access
    DATA_FLAGS = 0x00C00  # Mode for data writes:
    JOURNAL_DATA = 0x00400  # Write data to journal
    ORDERED_DATA = 0x00800  # Flush data before commit
    WRITEBACK_DATA = 0x00C00  # No data ordering
    UPDATE_JOURNAL = 0x01000  # Update the journal format
    NO_UID32 = 0x02000  # Disable 32-bit UIDs
    XATTR_USER = 0x04000  # Extended user attributes
    POSIX_ACL = 0x08000  # POSIX Access Control Lists
    NO_AUTO_DA_ALLOC = 0x10000  # No auto delalloc mapping
    BARRIER = 0x20000  # Use block barriers
    QUOTA = 0x40000  # Some quota option set
    USRQUOTA = 0x80000  # "old" user quota, enable enforcement for hidden quota files
    GRPQUOTA = 0x100000  # "old" group quota, enable enforcement for hidden quota files
    PRJQUOTA = 0x200000  # Enable project quota enforcement
    DIOREAD_NOLOCK = 0x400000  # Enable support for dio read nolocking
    JOURNAL_CHECKSUM = 0x800000  # Journal checksums
    JOURNAL_ASYNC_COMMIT = 0x1000000  # Journal Async Commit
    WARN_ON_ERROR = 0x2000000  # Trigger WARN_ON on error
    NO_PREFETCH_BLOCK_BITMAPS = 0x4000000
    DELALLOC = 0x8000000  # Delalloc support
    DATA_ERR_ABORT = 0x10000000  # Abort on file data write
    BLOCK_VALIDITY = 0x20000000  # Block validity checking
    DISCARD = 0x40000000  # Issue DISCARD requests
    INIT_INODE_TABLE = 0x80000000  # Initialize uninitialized itables


@final
class EXT4_MOUNT2(TypedCEnumeration(c_uint8)):
    EXPLICIT_DELALLOC = 0x00000001  # User explicitly specified delalloc
    STD_GROUP_SIZE = 0x00000002  # We have standard group size of blocksize * 8 blocks
    HURD_COMPAT = 0x00000004  # Support HURD-castrated file systems
    EXPLICIT_JOURNAL_CHECKSUM = 0x00000008  # User explicitly specified journal checksum
    JOURNAL_FAST_COMMIT = 0x00000010  # Journal fast commit
    DAX_NEVER = 0x00000020  # Do not allow Direct Access
    DAX_INODE = 0x00000040  # For printing options only
    MB_OPTIMIZE_SCAN = 0x00000080  # Optimize group scanning in mballoc
    ABORT = 0x00000100  # Abort filesystem


@final
class FS_ENCRYPTION_MODE(TypedCEnumeration(c_uint8)):
    INVALID = 0  # never used
    AES_256_XTS = 1
    AES_256_GCM = 2  # never used
    AES_256_CBC = 3  # never used
    AES_256_CTS = 4
    AES_128_CBC = 5
    AES_128_CTS = 6
    ADIANTUM = 9


@final
class EXT4_BG(TypedCEnumeration(c_uint16)):
    INODE_UNINIT = 0x0001  # Inode table/bitmap not in use
    BLOCK_UNINIT = 0x0002  # Block bitmap not in use
    INODE_ZEROED = 0x0004  # On-disk itable initialized to zero


@final
class MODE(TypedCEnumeration(c_uint16)):
    IXOTH = 0x1  # (Others may execute)
    IWOTH = 0x2  # (Others may write)
    IROTH = 0x4  # (Others may read)
    IXGRP = 0x8  # (Group members may execute)
    IWGRP = 0x10  # (Group members may write)
    IRGRP = 0x20  # (Group members may read)
    IXUSR = 0x40  # (Owner may execute)
    IWUSR = 0x80  # (Owner may write)
    IRUSR = 0x100  # (Owner may read)
    ISVTX = 0x200  # (Sticky bit)
    ISGID = 0x400  # (Set GID)
    ISUID = 0x800  # (Set UID)
    # These are mutually-exclusive file types:
    IFIFO = 0x1000  # (FIFO)
    IFCHR = 0x2000  # (Character device)
    IFDIR = 0x4000  # (Directory)
    IFBLK = 0x6000  # (Block device)
    IFREG = 0x8000  # (Regular file)
    IFLNK = 0xA000  # (Symbolic link)
    IFSOCK = 0xC000  # (Socket)


@final
class EXT4_FL(TypedCEnumeration(c_uint32)):
    SECRM = 0x00000001  # Secure deletion
    UNRM = 0x00000002  # Undelete
    COMPR = 0x00000004  # Compress file
    SYNC = 0x00000008  # Synchronous updates
    IMMUTABLE = 0x00000010  # Immutable file
    APPEND = 0x00000020  # writes to file may only append
    NODUMP = 0x00000040  # do not dump file
    NOATIME = 0x00000080  # do not update atime
    DIRTY = 0x00000100
    COMPRBLK = 0x00000200  # One or more compressed clusters
    NOCOMPR = 0x00000400  # Don't compress
    ENCRYPT = 0x00000800  # encrypted file
    INDEX = 0x00001000  # hash-indexed directory
    IMAGIC = 0x00002000  # AFS directory
    JOURNAL_DATA = 0x00004000  # file data should be journaled
    NOTAIL = 0x00008000  # file tail should not be merged
    DIRSYNC = 0x00010000  # dirsync behaviour (directories only)
    TOPDIR = 0x00020000  # Top of directory hierarchie
    HUGE_FILE = 0x00040000  # Set to each huge file
    EXTENTS = 0x00080000  # Inode uses extents
    VERITY = 0x00100000  # Verity protected inode
    EA_INODE = 0x00200000  # Inode used for large EA
    DAX = 0x02000000  # Inode is DAX
    INLINE_DATA = 0x10000000  # Inode has inline data.
    PROJINHERIT = 0x20000000  # Create with parents projid
    CASEFOLD = 0x40000000  # Casefolded directory
    RESERVED = 0x80000000  # reserved for ext4 lib


@final
class EXT4_FT(TypedCEnumeration(c_uint8)):
    UNKNOWN = 0
    REG_FILE = 1
    DIR = 2
    CHRDEV = 3
    BLKDEV = 4
    FIFO = 5
    SOCK = 6
    SYMLINK = 7
    MAX = 8
    DIR_CSUM = 0xDE
