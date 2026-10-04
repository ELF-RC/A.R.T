import errno
import warnings
from collections.abc import Iterator
from ctypes import (
    LittleEndianStructure,
    addressof,
    c_char,
    c_uint8,
    c_uint16,
    c_uint32,
    memmove,
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
from .block import BlockIO
from .enum import DX_HASH
from .struct import (
    Ext4Struct,
    MagicError,
)

if TYPE_CHECKING:
    from .inode import Directory
    from .volume import Volume


class HtreeHashError(Exception):
    pass


class LittleEndianStructureWithVolume(LittleEndianStructure):
    __slots__: tuple[str, ...] = ("_volume",)

    def __init__(self) -> None:
        super().__init__()
        self._volume: Volume | None = None

    @property
    def volume(self) -> "Volume":
        assert self._volume is not None
        return self._volume

    @volume.setter
    def volume(self, volume: "Volume") -> None:
        self._volume = volume


@final
class DotDirectoryEntry2(LittleEndianStructureWithVolume):
    __slots__ = ()

    _pack_ = 1
    # _anonymous_ = ()
    _fields_ = (
        ("inode", c_uint32),
        ("rec_len", c_uint16),
        ("name_len", c_uint8),
        ("file_type", c_uint8),
        ("name", c_char * 4),  # b".\0\0\0" or b"..\0\0"
    )

    def verify(self) -> None:
        name = assert_cast(self.name, bytes)  # pyright: ignore[reportAny]
        if name in (b".", b".."):
            return

        message = f"{self} dot or dotdot entry name invalid! actual={name}"
        assert self.volume is not None
        if not self.volume.ignore_magic:
            raise MagicError(message)

        warnings.warn(
            message,
            RuntimeWarning,
            stacklevel=2,
        )


@final
class DXRootInfo(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("reserved_zero")
    _fields_ = (
        ("reserved_zero", c_uint32),
        ("hash_version", DX_HASH.basetype),
        ("info_length", c_uint8),
        ("indirect_levels", c_uint8),
        ("unused_flags", c_uint8),
    )


class DXBase(Ext4Struct):
    __slots__: tuple[str, ...] = ("directory",)

    def __init__(self, directory: "Directory", offset: int) -> None:
        self.directory: Directory = directory
        super().__init__(directory.volume, offset)

    @override
    def read_from_volume(self) -> None:
        reader = self.directory._open()  # pyright: ignore[reportPrivateUsage]
        _ = reader.seek(self.offset)
        data = reader.read(sizeof(self))
        if len(data) != sizeof(self):
            raise OSError(
                errno.EIO,
                f"Short read for {type(self).__name__} at offset {self.offset}",
            )

        _ = memmove(addressof(self), data, sizeof(self))


@final
class DXEntry(DXBase):
    __slots__ = (
        "index",
        "parent",
    )

    _pack_ = 1
    # _anonymous_ = ("")
    _fields_ = (
        ("hash", c_uint32),
        ("block", c_uint32),
    )

    def __init__(self, parent: "DXEntriesBase", index: int) -> None:
        self.index: int = index
        self.parent: DXEntriesBase = parent
        super().__init__(
            parent.directory,
            parent.offset + parent.size + index * parent.info_length,
        )


@final
class DXEntries:
    __slots__ = (
        "_cache",
        "base",
    )

    def __init__(self, base: "DXEntriesBase") -> None:
        self.base: DXEntriesBase = base
        self._cache: dict[int, DXEntry] = {}

    def __contains__(self, index: int) -> bool:
        return 0 <= index < self.base.count  # pyright: ignore[reportAny]

    def __len__(self) -> int:
        """Length of entries minus the DXTail"""
        return self.base.count - 1  # pyright: ignore[reportAny]

    def __iter__(self) -> Iterator[DXEntry]:
        for index in range(len(self)):
            yield self[index]

    def __getitem__(self, index: int) -> DXEntry:
        if index not in self:
            raise KeyError()

        entry = self._cache.get(index, None)
        if entry is None:
            entry = DXEntry(self.base, index)
            self._cache[index] = entry

        return entry


class DXEntriesBase(DXBase):
    __slots__: tuple[str, ...] = (
        "entries",
        "parent",
    )

    def __init__(self, directory: "Directory", offset: int) -> None:
        super().__init__(directory, offset)
        self.parent: DXRoot | None = directory.htree
        self.entries: DXEntries = DXEntries(self)

    @property
    def info_length(self) -> int:
        parent = self
        while not isinstance(parent, DXRoot):
            parent = assert_cast(parent.parent, DXEntriesBase)

        dx_root_info = assert_cast(parent.dx_root_info, DXRootInfo)  # pyright: ignore[reportAny]
        return assert_cast(dx_root_info.info_length, int)  # pyright: ignore[reportAny]


def str2hashbuf(
    name: bytes, length: int, num: int, signed_hash: bool = False
) -> list[int]:
    """Convert name bytes to array of 32-bit words (LSB-first)."""
    pad: int = length | (length << 8)
    pad |= pad << 16
    pad &= 0xFFFFFFFF

    buf: list[int] = []
    val: int = pad
    length = min(length, num * 4)
    for i in range(length):
        byte_val = sign_extended_byte(signed_hash, name[i])
        val = (byte_val + (val << 8)) & 0xFFFFFFFF
        if i % 4 == 3:
            buf.append(val)
            val = pad

    if len(buf) < num:
        buf.append(val)

    while len(buf) < num:
        buf.append(pad)

    return buf[:num]


def sign_extended_byte(signed_hash: bool, value: int) -> int:
    if signed_hash and value > 127:
        return value - 256

    return value


def half_md4_transform(buf: list[int], inp: list[int]) -> None:
    """
    Args:
        buf: Input/output [a, b, c, d] (modified in place)
        inp: 8 input words from str2hashbuf
    """

    def F(x: int, y: int, z: int) -> int:
        return (z) ^ ((x) & ((y) ^ (z)))

    def G(x: int, y: int, z: int) -> int:
        return ((x) & (y)) + (((x) ^ (y)) & (z))

    def H(x: int, y: int, z: int) -> int:
        return (x) ^ (y) ^ (z)

    def rol32(val: int, shift: int) -> int:
        """32-bit rotate left."""
        shift &= 31
        return ((val << shift) | (val >> (32 - shift))) & 0xFFFFFFFF

    a: int = buf[0]
    b: int = buf[1]
    c: int = buf[2]
    d: int = buf[3]

    k: int = 0
    a = rol32((a + F(b, c, d) + inp[0] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + F(a, b, c) + inp[1] + k) & 0xFFFFFFFF, 7)
    c = rol32((c + F(d, a, b) + inp[2] + k) & 0xFFFFFFFF, 11)
    b = rol32((b + F(c, d, a) + inp[3] + k) & 0xFFFFFFFF, 19)
    a = rol32((a + F(b, c, d) + inp[4] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + F(a, b, c) + inp[5] + k) & 0xFFFFFFFF, 7)
    c = rol32((c + F(d, a, b) + inp[6] + k) & 0xFFFFFFFF, 11)
    b = rol32((b + F(c, d, a) + inp[7] + k) & 0xFFFFFFFF, 19)

    k = 0x5A827999
    a = rol32((a + G(b, c, d) + inp[1] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + G(a, b, c) + inp[3] + k) & 0xFFFFFFFF, 5)
    c = rol32((c + G(d, a, b) + inp[5] + k) & 0xFFFFFFFF, 9)
    b = rol32((b + G(c, d, a) + inp[7] + k) & 0xFFFFFFFF, 13)
    a = rol32((a + G(b, c, d) + inp[0] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + G(a, b, c) + inp[2] + k) & 0xFFFFFFFF, 5)
    c = rol32((c + G(d, a, b) + inp[4] + k) & 0xFFFFFFFF, 9)
    b = rol32((b + G(c, d, a) + inp[6] + k) & 0xFFFFFFFF, 13)

    k = 0x6ED9EBA1
    a = rol32((a + H(b, c, d) + inp[3] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + H(a, b, c) + inp[7] + k) & 0xFFFFFFFF, 9)
    c = rol32((c + H(d, a, b) + inp[2] + k) & 0xFFFFFFFF, 11)
    b = rol32((b + H(c, d, a) + inp[6] + k) & 0xFFFFFFFF, 15)
    a = rol32((a + H(b, c, d) + inp[1] + k) & 0xFFFFFFFF, 3)
    d = rol32((d + H(a, b, c) + inp[5] + k) & 0xFFFFFFFF, 9)
    c = rol32((c + H(d, a, b) + inp[0] + k) & 0xFFFFFFFF, 11)
    b = rol32((b + H(c, d, a) + inp[4] + k) & 0xFFFFFFFF, 15)

    buf[0] = (buf[0] + a) & 0xFFFFFFFF
    buf[1] = (buf[1] + b) & 0xFFFFFFFF
    buf[2] = (buf[2] + c) & 0xFFFFFFFF
    buf[3] = (buf[3] + d) & 0xFFFFFFFF


def tea_transform(buf: list[int], inp: list[int]) -> None:
    DELTA: int = 0x9E3779B9
    sum_val: int = 0
    b0: int = buf[0]
    b1: int = buf[1]
    a: int = inp[0]
    b: int = inp[1]
    c: int = inp[2]
    d: int = inp[3]

    for _ in range(16):
        sum_val = (sum_val + DELTA) & 0xFFFFFFFF
        b0 = (b0 + (((b1 << 4) + a) ^ (b1 + sum_val) ^ ((b1 >> 5) + b))) & 0xFFFFFFFF
        b1 = (b1 + (((b0 << 4) + c) ^ (b0 + sum_val) ^ ((b0 >> 5) + d))) & 0xFFFFFFFF

    buf[0] = (buf[0] + b0) & 0xFFFFFFFF
    buf[1] = (buf[1] + b1) & 0xFFFFFFFF


@final
class DXRoot(DXEntriesBase):
    __slots__ = ()
    _pack_ = 1

    def __init__(self, inode: "Directory") -> None:
        super().__init__(inode, 0)

    # _anonymous_ = ("")
    _fields_ = (
        ("dot", DotDirectoryEntry2),
        ("dotdot", DotDirectoryEntry2),
        ("dx_root_info", DXRootInfo),
        ("limit", c_uint16),
        ("count", c_uint16),
        ("block", c_uint32),
        # ("entries", DXEntry * self.count),
    )

    def compute_dx_hash(self, name: bytes, hash_version: int) -> tuple[int, int]:
        seed = self.directory.volume.superblock.s_hash_seed  # pyright: ignore[reportAny]
        buf: list[int] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
        if seed is not None and any(seed):  # pyright: ignore[reportAny]
            buf = list(seed)  # pyright: ignore[reportAny]

        match hash_version:
            case DX_HASH.LEGACY | DX_HASH.LEGACY_UNSIGNED:
                hash0: int = 0x12A3FE2D
                hash1: int = 0x37ABE8F9
                for i in range(len(name)):
                    byte_val = sign_extended_byte(
                        hash_version == DX_HASH.LEGACY, name[i]
                    )
                    hash_val: int = hash1 + (hash0 ^ (byte_val * 7152373))
                    if hash_val & 0x80000000:
                        hash_val -= 0x7FFFFFFF

                    hash1 = hash0
                    hash0 = hash_val & 0xFFFFFFFF

                hash_val = (hash0 << 1) & 0xFFFFFFFF
                minor_hash = 0

            case DX_HASH.HALF_MD4 | DX_HASH.HALF_MD4_UNSIGNED:
                signed_hash = hash_version == DX_HASH.HALF_MD4
                p = 0
                length = len(name)
                while length > 0:
                    inp = str2hashbuf(name[p:], length, 8, signed_hash)
                    half_md4_transform(buf, inp)
                    length -= 32
                    p += 32

                hash_val = buf[1]
                minor_hash = buf[2]

            case DX_HASH.TEA | DX_HASH.TEA_UNSIGNED:
                signed_hash = hash_version == DX_HASH.TEA
                p = 0
                length = len(name)
                while length > 0:
                    inp = str2hashbuf(name[p:], length, 4, signed_hash)
                    tea_transform(buf, inp)
                    length -= 16
                    p += 16

                hash_val = buf[0]
                minor_hash = buf[1]

            case DX_HASH.SIPHASH:
                raise HtreeHashError("SipHash not yet supported for htree lookup")

            case _:
                raise HtreeHashError(f"Unknown hash version: {hash_version}")

        hash_val &= ~1
        if hash_val == 0xFFFFFFFE:
            hash_val = 0xFFFFFFFC

        return hash_val, minor_hash

    def lookup(self, name: str | bytes) -> int | None:
        if isinstance(name, str):
            name = name.encode("utf-8")

        hash_version = assert_cast(self.dx_root_info.hash_version, int)  # pyright: ignore[reportAny]
        hash_val, _minor_hash = self.compute_dx_hash(name, hash_version)
        block_num: int = assert_cast(self.block, int)  # pyright: ignore[reportAny]  # default block
        entries = self.entries
        lo: int = 0
        hi: int = len(entries) - 1
        while lo <= hi:
            mid: int = (lo + hi) // 2
            if entries[mid].hash <= hash_val:  # pyright: ignore[reportAny]
                lo = mid + 1

            else:
                hi = mid - 1

        leaf_entries: DXEntries | None = None
        leaf_idx: int = -1
        if hi >= 0:
            block_num = entries[hi].block  # pyright: ignore[reportAny]
            leaf_entries = entries
            leaf_idx = hi

        for _ in range(assert_cast(self.dx_root_info.indirect_levels, int)):  # pyright: ignore[reportAny]
            node = DXNode(
                self.directory,
                block_num * self.directory.block_size,
            )
            node_entries = node.entries

            lo = 0
            hi = len(node_entries) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if node_entries[mid].hash <= hash_val:  # pyright: ignore[reportAny]
                    lo = mid + 1

                else:
                    hi = mid - 1

            block_num = (
                node_entries[hi].block  # pyright: ignore[reportAny]
                if hi >= 0
                else assert_cast(node.block, int)  # pyright: ignore[reportAny]
            )

            if hi >= 0:
                leaf_entries = node_entries
                leaf_idx = hi

            else:
                leaf_entries = None
                leaf_idx = -1

        blocks_to_scan: list[int] = []
        if leaf_entries is not None and leaf_idx >= 0:
            current_idx = leaf_idx
            while current_idx < len(leaf_entries):
                entry = leaf_entries[current_idx]
                blocks_to_scan.append(entry.block)  # pyright: ignore[reportAny]
                next_idx = current_idx + 1
                if (
                    next_idx >= len(leaf_entries)
                    or not (leaf_entries[next_idx].hash & 1)  # pyright: ignore[reportAny]
                    or (leaf_entries[next_idx].hash & ~1) != (hash_val & ~1)  # pyright: ignore[reportAny]
                ):
                    break

                current_idx = next_idx
        else:
            blocks_to_scan = [block_num]

        has_filetype = self.directory.has_filetype
        with BlockIO(self.directory) as blockio:
            for scan_block in blocks_to_scan:
                leaf_data = blockio.blocks[scan_block]
                offset: int = 0
                while offset + 8 <= len(leaf_data):
                    inode_val = int.from_bytes(leaf_data[offset : offset + 4], "little")
                    rec_len = int.from_bytes(
                        leaf_data[offset + 4 : offset + 6], "little"
                    )

                    if rec_len == 0:
                        break

                    name_len = (
                        leaf_data[offset + 6]
                        if has_filetype
                        else int.from_bytes(
                            leaf_data[offset + 6 : offset + 8], "little"
                        )
                    )

                    if inode_val == 0 or name_len == 0:
                        offset += rec_len
                        continue

                    name_start = offset + 8
                    if name_start + name_len > len(leaf_data):
                        break

                    entry_name = leaf_data[name_start : name_start + name_len]
                    if entry_name == name:
                        return inode_val

                    offset += rec_len

        return None


@final
class DXFake(LittleEndianStructure):
    __slots__ = ()
    _pack_ = 1
    # _anonymous_ = ("")
    _fields_ = (
        ("inode", c_uint32),  # 0
        ("rec_len", c_uint16),
    )

    @property
    def expected_magic(self) -> int:
        return 0

    @property
    def magic(self) -> int:
        inode = assert_cast(self.inode, int)  # pyright: ignore[reportAny]
        return inode


@final
class DXNode(DXEntriesBase):
    __slots__ = ()

    _pack_ = 1
    # _anonymous_ = ("")
    _fields_ = (
        ("fake", DXFake),
        ("name_len", c_uint8),
        ("file_type", c_uint8),
        ("limit", c_uint16),
        ("count", c_uint16),
        ("block", c_uint32),
        # ("entries", DXEntry * self.count),
    )

    def __init__(self, directory: "Directory", offset: int) -> None:
        super().__init__(directory, offset)


@final
class DXTail(DXBase):
    __slots__ = ("parent",)

    _pack_ = 1
    # _anonymous_ = ("dt_reserved")
    _fields_ = (
        ("dt_reserved", c_uint32),
        ("dt_checksum", c_uint16),
    )

    def __init__(self, parent: DXNode) -> None:
        self.parent = parent
        count = assert_cast(parent.count, int)  # pyright: ignore[reportAny]
        super().__init__(
            parent.directory,
            parent.offset + parent.size + (count + 1) * parent.info_length,
        )
