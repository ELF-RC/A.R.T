"""Super image extraction — 解包 super.img 动态分区及选择性提取逻辑分区。"""

import os
import shutil
from pathlib import Path

from scripts.primary.utils import V, display, is_sparse_image, sparse_to_raw
from scripts.primary.workspace import workspace_partition


def _super_images_to_process(super_dir):
    """Normalize A/B logical partition images and return images to process."""
    images = sorted(Path(super_dir).glob('*.img'))
    a_parts = {}
    b_parts = {}
    other_parts = {}
    for image in images:
        stem = image.stem
        if stem.endswith('_a'):
            a_parts[stem[:-2]] = image
        elif stem.endswith('_b'):
            b_parts[stem[:-2]] = image
        elif image.stat().st_size > 0:
            other_parts[stem] = image

    if not a_parts and not b_parts:
        return [(str(image), image.stem) for image in images if image.stat().st_size > 0]

    selected = []
    for part in sorted(set(a_parts) | set(b_parts)):
        image_a = a_parts.get(part)
        image_b = b_parts.get(part)
        size_a = image_a.stat().st_size if image_a and image_a.exists() else 0
        size_b = image_b.stat().st_size if image_b and image_b.exists() else 0
        if size_a == 0 and size_b == 0:
            for image in (image_a, image_b):
                if image and image.exists():
                    image.unlink()
        elif size_a > 0 and size_b > 0:
            selected.extend(((str(image_a), f'{part}_a'), (str(image_b), f'{part}_b')))
        else:
            selected_image = image_a if size_a > 0 else image_b
            unused_image = image_b if size_a > 0 else image_a
            if unused_image and unused_image.exists():
                unused_image.unlink()
            destination = Path(super_dir) / f'{part}.img'
            if destination.exists():
                destination.unlink()
            selected_image.rename(destination)
            selected.append((str(destination), part))

    selected.extend((str(image), part) for part, image in sorted(other_parts.items()))
    return selected


def _cleanup_super_ab(super_dir):
    """Clean up _a/_b suffixes in super_dir when user chooses not to continue extracting."""
    files = {Path(f).stem: Path(super_dir) / f for f in os.listdir(super_dir) if f.endswith('.img')}
    a_parts = {s[:-2]: p for s, p in files.items() if s.endswith('_a') and p.exists()}
    b_parts = {s[:-2]: p for s, p in files.items() if s.endswith('_b') and p.exists()}
    for part in sorted(set(a_parts) | set(b_parts)):
        pa = a_parts.get(part)
        pb = b_parts.get(part)
        size_a = pa.stat().st_size if pa and pa.exists() else 0
        size_b = pb.stat().st_size if pb and pb.exists() else 0
        if size_a == 0 and size_b == 0:
            for p in (pa, pb):
                if p and p.exists():
                    p.unlink()
        elif size_a > 0 and size_b > 0:
            pass
        elif size_a > 0:
            if pb and pb.exists():
                pb.unlink()
            dest = Path(super_dir) / f'{part}.img'
            if dest.exists():
                dest.unlink()
            pa.rename(dest)
        else:
            if pa and pa.exists():
                pa.unlink()
            dest = Path(super_dir) / f'{part}.img'
            if dest.exists():
                dest.unlink()
            pb.rename(dest)


def _move_super_images_to_out(super_dir):
    """Move remaining .img files from super_dir to OUT."""
    out_dir = V.out
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    remaining = [f for f in os.listdir(super_dir) if f.endswith('.img')]
    if remaining:
        for name in sorted(remaining):
            src = Path(super_dir) / name
            dst = Path(out_dir) / name
            if dst.exists():
                dst.unlink()
            os.replace(str(src), str(dst))
            display(f'已输出: {name} -> {out_dir}')


def extract_super(working_source, partition):
    """Extract a super.img into WORKSPACE.

    Returns True if handled (either extracted or moved to OUT), False on failure.
    """
    from scripts.extract.image import decompress_img

    display(f'正在分解: {os.path.basename(working_source)} <super>', 3)
    super_dir = os.path.join(V.workspace, 'super') + os.sep
    try:
        unpack(working_source, super_dir)
    except (Exception, SystemExit) as error:
        print(f'> super 分解失败: {error}')
        return False

    if input('> 是否继续分解img [0/1]: ') != '1':
        _cleanup_super_ab(super_dir)
        _move_super_images_to_out(super_dir)
        shutil.rmtree(super_dir, ignore_errors=True)
        return True

    for image, image_partition in _super_images_to_process(super_dir):
        decompress_img(image, workspace_partition(image_partition))
    shutil.rmtree(super_dir, ignore_errors=True)
    return True


# Embedded logical-partition unpacker; super extraction is self-contained.
# pylint: disable=line-too-long
import argparse
import copy
import enum
import io
import json
import os
import re
import struct
from dataclasses import dataclass, field
from string import Template
from timeit import default_timer as dti
from typing import IO, Dict, List, TypeVar, cast, BinaryIO, Tuple

LP_PARTITION_RESERVED_BYTES = 4096
LP_METADATA_GEOMETRY_MAGIC = 0x616c4467
LP_METADATA_GEOMETRY_SIZE = 4096
LP_METADATA_HEADER_MAGIC = 0x414C5030
LP_SECTOR_SIZE = 512

LP_TARGET_TYPE_LINEAR = 0
LP_TARGET_TYPE_ZERO = 1

LP_PARTITION_ATTR_READONLY = (1 << 0)
LP_PARTITION_ATTR_SLOT_SUFFIXED = (1 << 1)
LP_PARTITION_ATTR_UPDATED = (1 << 2)
LP_PARTITION_ATTR_DISABLED = (1 << 3)

LP_BLOCK_DEVICE_SLOT_SUFFIXED = (1 << 0)

LP_GROUP_SLOT_SUFFIXED = (1 << 0)

PLAIN_TEXT_TEMPLATE = """Slot 0:
Metadata version: $metadata_version
Metadata size: $metadata_size bytes
Metadata max size: $metadata_max_size bytes
Metadata slot count: $metadata_slot_count
Header flags: $header_flags
Partition table:
------------------------
$partitions
------------------------
Super partition layout:
------------------------
$layouts
------------------------
Block device table:
------------------------
$blocks
------------------------
Group table:
------------------------
$groups
"""


def build_attribute_string(attributes: int) -> str:
    if attributes & LP_PARTITION_ATTR_READONLY:
        result = "readonly"
    elif attributes & LP_PARTITION_ATTR_SLOT_SUFFIXED:
        result = "slot-suffixed"
    elif attributes & LP_PARTITION_ATTR_UPDATED:
        result = "updated"
    elif attributes & LP_PARTITION_ATTR_DISABLED:
        result = "disabled"
    else:
        result = "none"
    return result


def build_block_device_flag_string(flags: int) -> str:
    return "slot-suffixed" if (flags & LP_BLOCK_DEVICE_SLOT_SUFFIXED) else "none"


def build_group_flag_string(flags: int) -> str:
    return "slot-suffixed" if (flags & LP_GROUP_SLOT_SUFFIXED) else "none"


class FormatType(enum.Enum):
    TEXT = "text"
    JSON = "json"


class EnumAction(argparse.Action):
    """Argparse action for handling Enums"""

    def __init__(self, **kwargs):
        enum_type = kwargs.pop("type", None)
        if enum_type is None:
            raise ValueError("Type must be assigned an Enum when using EnumAction")

        if not issubclass(enum_type, enum.Enum):
            raise TypeError("Type must be an Enum when using EnumAction")

        kwargs.setdefault("choices", tuple(e.value for e in enum_type))

        super(EnumAction, self).__init__(**kwargs)
        self._enum = enum_type

    def __call__(self, parser, namespace, values, option_string=None):
        value = self._enum(values)
        setattr(namespace, self.dest, value)


class ShowJsonInfo(json.JSONEncoder):
    def __init__(self, ignore_keys: List[str], **kwargs):
        super().__init__(**kwargs)
        self._ignore_keys = ignore_keys

    def _remove_ignore_keys(self, data: Dict):
        _data = copy.deepcopy(data)
        for field_key, v in data.items():
            if field_key in self._ignore_keys:
                _data.pop(field_key)
                continue

            if v == 0:
                _data.pop(field_key)
                continue

            if isinstance(v, int) and not isinstance(v, bool):
                _data.update({field_key: str(v)})
        return _data

    def encode(self, data: Dict) -> str:
        result = {
            "partitions": list(map(self._remove_ignore_keys, data["partition_table"])),
            "groups": list(map(self._remove_ignore_keys, data["group_table"])),
            "block_devices": list(map(self._remove_ignore_keys, data["block_devices"]))
        }
        return super().encode(result)


class LpMetadataBase:
    _fmt = None

    @classmethod
    @property
    def size(cls) -> int:
        return struct.calcsize(cls._fmt)


class LpMetadataGeometry(LpMetadataBase):
    """
    Offset 0: Magic signature

    Offset 4: Size of the `LpMetadataGeometry`

    Offset 8: SHA256 checksum

    Offset 40: Maximum amount of space a single copy of the metadata can use

    Offset 44: Number of copies of the metadata to keep

    Offset 48: Logical block size
    """

    _fmt = '<2I32s3I'

    def __init__(self, buffer):
        (
            self.magic,
            self.struct_size,
            self.checksum,
            self.metadata_max_size,
            self.metadata_slot_count,
            self.logical_block_size

        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])
        # self.size


class LpMetadataTableDescriptor(LpMetadataBase):
    """
    Offset 0: Location of the table, relative to end of the metadata header.

    Offset 4: Number of entries in the table.

    Offset 8: Size of each entry in the table, in bytes.
    """

    _fmt = '<3I'

    def __init__(self, buffer):
        (
            self.offset,
            self.num_entries,
            self.entry_size

        ) = struct.unpack(self._fmt, buffer[:struct.calcsize(self._fmt)])


class LpMetadataPartition(LpMetadataBase):
    """
    Offset 0: Name of this partition in ASCII characters. Any unused characters in
              the buffer must be set to 0. Characters may only be alphanumeric or _.
              The name must include at least one ASCII character, and it must be unique
              across all partition names. The length (36) is the same as the maximum
              length of a GPT partition name.

    Offset 36: Attributes for the partition (see LP_PARTITION_ATTR_* flags above).

    Offset 40: Index of the first extent owned by this partition. The extent will
               start at logical sector 0. Gaps between extents are not allowed.

    Offset 44: Number of extents in the partition. Every partition must have at least one extent.

    Offset 48: Group this partition belongs to.
    """

    _fmt = '<36s4I'

    def __init__(self, buffer):
        (
            self.name,
            self.attributes,
            self.first_extent_index,
            self.num_extents,
            self.group_index

        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])

        self.name = self.name.decode("utf-8").strip('\x00')

    @property
    def filename(self) -> str:
        return f'{self.name}.img'


class LpMetadataExtent(LpMetadataBase):
    """
    Offset 0: Length of this extent, in 512-byte sectors.

    Offset 8: Target type for device-mapper (see LP_TARGET_TYPE_* values).

    Offset 12: Contents depends on target_type. LINEAR: The sector on the physical partition that this extent maps onto.
               ZERO: This field must be 0.

    Offset 20: Contents depends on target_type. LINEAR: Must be an index into the block devices table.
    """

    _fmt = '<QIQI'

    def __init__(self, buffer):
        (
            self.num_sectors,
            self.target_type,
            self.target_data,
            self.target_source

        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])


class LpMetadataHeader(LpMetadataBase):
    """
    +-----------------------------------------+
    | Header data - fixed size                |
    +-----------------------------------------+
    | Partition table - variable size         |
    +-----------------------------------------+
    | Partition table extents - variable size |
    +-----------------------------------------+

    Offset 0: Four bytes equal to `LP_METADATA_HEADER_MAGIC`

    Offset 4: Version number required to read this metadata. If the version is not
              equal to the library version, the metadata should be considered incompatible.

    Offset 6: Minor version. A library supporting newer features should be able to
              read metadata with an older minor version. However, an older library
              should not support reading metadata if its minor version is higher.

    Offset 8: The size of this header struct.

    Offset 12: SHA256 checksum of the header, up to |header_size| bytes, computed as if this field were set to 0.

    Offset 44: The total size of all tables. This size is contiguous; tables may not
               have gaps in between, and they immediately follow the header.

    Offset 48: SHA256 checksum of all table contents.

    Offset 80: Partition table descriptor.

    Offset 92: Extent table descriptor.

    Offset 104: Updateable group descriptor.

    Offset 116: Block device table.

    Offset 128: Header flags are independent of the version number and intended to be informational only.
                New flags can be added without bumping the version.

    Offset 132: Reserved (zero), pad to 256 bytes.
    """

    _fmt = '<I2hI32sI32s'

    partitions: LpMetadataTableDescriptor = field(default=None)
    extents: LpMetadataTableDescriptor = field(default=None)
    groups: LpMetadataTableDescriptor = field(default=None)
    block_devices: LpMetadataTableDescriptor = field(default=None)

    def __init__(self, buffer):
        (
            self.magic,
            self.major_version,
            self.minor_version,
            self.header_size,
            self.header_checksum,
            self.tables_size,
            self.tables_checksum

        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])
        self.flags = 0
        # self.size


class LpMetadataPartitionGroup(LpMetadataBase):
    """
    Offset 0: Name of this group. Any unused characters must be 0.

    Offset 36: Flags (see LP_GROUP_*).

    Offset 40: Maximum size in bytes. If 0, the group has no maximum size.
    """
    _fmt = '<36sIQ'

    def __init__(self, buffer):
        (
            self.name,
            self.flags,
            self.maximum_size
        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])

        self.name = self.name.decode("utf-8").strip('\x00')


class LpMetadataBlockDevice(LpMetadataBase):
    """
    Offset 0: First usable sector for allocating logical partitions. this will be
              the first sector after the initial geometry blocks, followed by the
              space consumed by metadata_max_size*metadata_slot_count*2.

    Offset 8: Alignment for defining partitions or partition extents. For example,
              an alignment of 1MiB will require that all partitions have a size evenly
              divisible by 1MiB, and that the smallest unit the partition can grow by is 1MiB.

              Alignment is normally determined at runtime when growing or adding
              partitions. If for some reason the alignment cannot be determined, then
              this predefined alignment in the geometry is used instead. By default, it is set to 1MiB.

    Offset 12: Alignment offset for "stacked" devices. For example, if the "super"
               partition itself is not aligned within the parent block device's
               partition table, then we adjust for this in deciding where to place
               |first_logical_sector|.

               Similar to |alignment|, this will be derived from the operating system.
               If it cannot be determined, it is assumed to be 0.

    Offset 16: Block device size, as specified when the metadata was created.
               This can be used to verify the geometry against a target device.

    Offset 24: Partition name in the GPT. Any unused characters must be 0.

    Offset 60: Flags (see LP_BLOCK_DEVICE_* flags below).
    """

    _fmt = '<Q2IQ36sI'

    def __init__(self, buffer):
        (
            self.first_logical_sector,
            self.alignment,
            self.alignment_offset,
            self.block_device_size,
            self.partition_name,
            self.flags
        ) = struct.unpack(self._fmt, buffer[0:struct.calcsize(self._fmt)])

        self.partition_name = self.partition_name.decode("utf-8").strip('\x00')


@dataclass
class Metadata:
    header: LpMetadataHeader = field(default=None)
    geometry: LpMetadataGeometry = field(default=None)
    partitions: List[LpMetadataPartition] = field(default_factory=list)
    extents: List[LpMetadataExtent] = field(default_factory=list)
    groups: List[LpMetadataPartitionGroup] = field(default_factory=list)
    block_devices: List[LpMetadataBlockDevice] = field(default_factory=list)

    @property
    def info(self) -> Dict:
        return self._get_info()

    @property
    def metadata_region(self) -> int:
        if self.geometry is None:
            return 0

        return LP_PARTITION_RESERVED_BYTES + (
                LP_METADATA_GEOMETRY_SIZE + self.geometry.metadata_max_size * self.geometry.metadata_slot_count
        ) * 2

    def _get_extents_string(self, partition: LpMetadataPartition) -> List[str]:
        result = []
        first_sector = 0
        for extent_number in range(partition.num_extents):
            index = partition.first_extent_index + extent_number
            extent = self.extents[index]

            _base = f"{first_sector} .. {first_sector + extent.num_sectors - 1}"
            first_sector += extent.num_sectors

            if extent.target_type == LP_TARGET_TYPE_LINEAR:
                result.append(
                    f"{_base} linear {self.block_devices[extent.target_source].partition_name} {extent.target_data}"
                )
            elif extent.target_type == LP_TARGET_TYPE_ZERO:
                result.append(f"{_base} zero")

        return result

    def _get_partition_layout(self) -> List[str]:
        result = []

        for partition in self.partitions:
            for extent_number in range(partition.num_extents):
                index = partition.first_extent_index + extent_number
                extent = self.extents[index]

                block_device_name = ""

                if extent.target_type == LP_TARGET_TYPE_LINEAR:
                    block_device_name = self.block_devices[extent.target_source].partition_name

                result.append(
                    f"{block_device_name}: {extent.target_data} .. {extent.target_data + extent.num_sectors}: "
                    f"{partition.name} ({extent.num_sectors} sectors)"
                )

        return result

    def get_offsets(self, slot_number: int = 0) -> List[int]:
        base = LP_PARTITION_RESERVED_BYTES + (LP_METADATA_GEOMETRY_SIZE * 2)
        _tmp_offset = self.geometry.metadata_max_size * slot_number
        primary_offset = base + _tmp_offset
        backup_offset = base + self.geometry.metadata_max_size * self.geometry.metadata_slot_count + _tmp_offset
        return [primary_offset, backup_offset]

    def _get_info(self) -> Dict:
        # TODO 25.01.2023: Liblp version 1.2 build_header_flag_string check header version 1.2
        result = {}
        try:
            result = {
                "metadata_version": f"{self.header.major_version}.{self.header.minor_version}",
                "metadata_size": self.header.header_size + self.header.tables_size,
                "metadata_max_size": self.geometry.metadata_max_size,
                "metadata_slot_count": self.geometry.metadata_slot_count,
                "header_flags": "none",
                "block_devices": [
                    {
                        "name": item.partition_name,
                        "first_sector": item.first_logical_sector,
                        "size": item.block_device_size,
                        "block_size": self.geometry.logical_block_size,
                        "flags": build_block_device_flag_string(item.flags),
                        "alignment": item.alignment,
                        "alignment_offset": item.alignment_offset
                    } for item in self.block_devices
                ],
                "group_table": [
                    {
                        "name": self.groups[index].name,
                        "maximum_size": self.groups[index].maximum_size,
                        "flags": build_group_flag_string(self.groups[index].flags)
                    } for index in range(0, self.header.groups.num_entries)
                ],
                "partition_table": [
                    {
                        "name": item.name,
                        "group_name": self.groups[item.group_index].name,
                        "is_dynamic": True,
                        "size": self.extents[item.first_extent_index].num_sectors * LP_SECTOR_SIZE,
                        "attributes": build_attribute_string(item.attributes),
                        "extents": self._get_extents_string(item)
                    } for item in self.partitions
                ],
                "partition_layout": self._get_partition_layout()
            }
        except Exception:
            ...
        finally:
            return result

    def to_json(self) -> str:
        data = self._get_info()
        if not data:
            return ""

        return json.dumps(
            data,
            indent=1,
            cls=ShowJsonInfo,
            ignore_keys=[
                'metadata_version', 'metadata_size', 'metadata_max_size', 'metadata_slot_count', 'header_flags',
                'partition_layout',
                'attributes', 'extents', 'flags', 'first_sector'
            ])

    def __str__(self):
        data = self._get_info()
        if not data:
            return ""

        template = Template(PLAIN_TEXT_TEMPLATE)
        layouts = "\n".join(data["partition_layout"])
        partitions = "------------------------\n".join(
            [
                "  Name: {}\n  Group: {}\n  Attributes: {}\n  Extents:\n    {}\n".format(item["name"],
                                                                                         item["group_name"],
                                                                                         item["attributes"],
                                                                                         "\n".join(item["extents"])) for
                item in data["partition_table"]
            ]
        )[:-1]
        blocks = "\n".join(
            [
                f"  Partition name: {item['name']}\n  First sector: {item['first_sector']}\n  Size: {item['size']} bytes\n  Flags: {item['flags']}"
                for item in data["block_devices"]
            ]
        )
        groups = "------------------------\n".join(
            [
                f"  Name: {item['name']}\n  Maximum size: {item['maximum_size']} bytes\n  Flags: {item['flags']}\n" for
                item in data["group_table"]
            ]
        )[:-1]
        return template.substitute(partitions=partitions, layouts=layouts, blocks=blocks, groups=groups, **data)


class LpUnpackError(Exception):
    """Raised any error unpacking"""

    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


_SAFE_PARTITION_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def validate_partition_name(name):
    if not isinstance(name, str) or not _SAFE_PARTITION_NAME.fullmatch(name):
        raise LpUnpackError(f'Invalid logical partition name: {name!r}')
    return name


@dataclass
class UnpackJob:
    name: str
    geometry: LpMetadataGeometry
    parts: List[Tuple[int, int]] = field(default_factory=list)
    total_size: int = field(default=0)


T = TypeVar('T')


class LpUnpack:
    def __init__(self, **kwargs):
        self._partition_name = kwargs.get('NAME')
        self._show_info = kwargs.get('SHOW_INFO', True)
        self._show_info_format = kwargs.get('SHOW_INFO_FORMAT', FormatType.TEXT)
        self._config = kwargs.get('CONFIG', None)
        self._slot_num = None
        super_image = kwargs.get('SUPER_IMAGE')
        if is_sparse_image(super_image):
            print('Sparse image detected.')
            print('Process conversion to non sparse image...')
            super_image = sparse_to_raw(super_image)
            print('Result:[ok]')
        self._super_image = super_image
        self._fd: BinaryIO = open(super_image, 'rb')
        self._out_dir = kwargs.get('OUTPUT_DIR', None)

    def _check_out_dir_exists(self):
        if self._out_dir is None:
            return
        output_dir = os.path.abspath(self._out_dir)
        if os.path.islink(output_dir):
            raise LpUnpackError(f'Output directory cannot be a symbolic link: {output_dir}')
        if os.path.exists(output_dir) and not os.path.isdir(output_dir):
            raise LpUnpackError(f'Output path is not a directory: {output_dir}')
        os.makedirs(output_dir, exist_ok=True)
        self._out_dir = output_dir

    def _extract_partition(self, unpack_job: UnpackJob):
        self._check_out_dir_exists()
        name = validate_partition_name(unpack_job.name)
        start = dti()
        print(f'Extracting partition [{name}]')
        output_dir = os.path.abspath(self._out_dir)
        out_file = os.path.abspath(os.path.join(output_dir, f'{name}.img'))
        if os.path.commonpath((output_dir, out_file)) != output_dir:
            raise LpUnpackError(f'Partition output escapes destination: {name!r}')
        if os.path.lexists(out_file) and os.path.islink(out_file):
            raise LpUnpackError(f'Partition output cannot be a symbolic link: {out_file}')
        with open(out_file, 'wb') as out:
            for part in unpack_job.parts:
                offset, size = part
                self._write_extent_to_file(out, offset, size, unpack_job.geometry.logical_block_size)

        print(f'Done:[{dti() - start}]')

    def _extract(self, partition, metadata):
        unpack_job = UnpackJob(name=partition.name, geometry=metadata.geometry)

        if partition.num_extents != 0:
            for extent_number in range(partition.num_extents):
                index = partition.first_extent_index + extent_number
                extent = metadata.extents[index]

                if extent.target_type != LP_TARGET_TYPE_LINEAR:
                    raise LpUnpackError(f'Unsupported target type in extent: {extent.target_type}')

                offset = extent.target_data * LP_SECTOR_SIZE
                size = extent.num_sectors * LP_SECTOR_SIZE
                unpack_job.parts.append((offset, size))
                unpack_job.total_size += size

        self._extract_partition(unpack_job)

    def _get_data(self, count: int, size: int, clazz: T) -> List[T]:
        result = []
        while count > 0:
            result.append(clazz(self._fd.read(size)))
            count -= 1
        return result

    def _read_metadata_header(self, metadata: Metadata):
        offsets = metadata.get_offsets()
        for index, offset in enumerate(offsets):
            self._fd.seek(offset, io.SEEK_SET)
            header = LpMetadataHeader(self._fd.read(80))
            header.partitions = LpMetadataTableDescriptor(self._fd.read(12))
            header.extents = LpMetadataTableDescriptor(self._fd.read(12))
            header.groups = LpMetadataTableDescriptor(self._fd.read(12))
            header.block_devices = LpMetadataTableDescriptor(self._fd.read(12))

            if header.magic != LP_METADATA_HEADER_MAGIC:
                check_index = index + 1
                if check_index > len(offsets):
                    raise LpUnpackError('Logical partition metadata has invalid magic value.')
                else:
                    print(f'Read Backup header by offset 0x{offsets[check_index]:x}')
                    continue

            metadata.header = header
            self._fd.seek(offset + header.header_size, io.SEEK_SET)

    def _read_metadata(self):
        self._fd.seek(LP_PARTITION_RESERVED_BYTES, io.SEEK_SET)
        metadata = Metadata(geometry=self._read_primary_geometry())

        if metadata.geometry.magic != LP_METADATA_GEOMETRY_MAGIC:
            raise LpUnpackError('Logical partition metadata has invalid geometry magic signature.')

        if metadata.geometry.metadata_slot_count == 0:
            raise LpUnpackError('Logical partition metadata has invalid slot count.')

        if metadata.geometry.metadata_max_size % LP_SECTOR_SIZE != 0:
            raise LpUnpackError('Metadata max size is not sector-aligned.')

        self._read_metadata_header(metadata)

        metadata.partitions = self._get_data(
            metadata.header.partitions.num_entries,
            metadata.header.partitions.entry_size,
            LpMetadataPartition
        )

        metadata.extents = self._get_data(
            metadata.header.extents.num_entries,
            metadata.header.extents.entry_size,
            LpMetadataExtent
        )

        metadata.groups = self._get_data(
            metadata.header.groups.num_entries,
            metadata.header.groups.entry_size,
            LpMetadataPartitionGroup
        )

        metadata.block_devices = self._get_data(
            metadata.header.block_devices.num_entries,
            metadata.header.block_devices.entry_size,
            LpMetadataBlockDevice
        )

        try:
            super_device: LpMetadataBlockDevice = cast(LpMetadataBlockDevice, iter(metadata.block_devices).__next__())
            if metadata.metadata_region > super_device.first_logical_sector * LP_SECTOR_SIZE:
                raise LpUnpackError('Logical partition metadata overlaps with logical partition contents.')
        except StopIteration:
            raise LpUnpackError('Metadata does not specify a super device.')

        return metadata

    def _read_primary_geometry(self) -> LpMetadataGeometry:
        geometry = LpMetadataGeometry(self._fd.read(LP_METADATA_GEOMETRY_SIZE))
        if geometry is not None:
            return geometry
        else:
            return LpMetadataGeometry(self._fd.read(LP_METADATA_GEOMETRY_SIZE))

    def _write_extent_to_file(self, fd: IO, offset: int, size: int, block_size: int):
        self._fd.seek(offset)
        remaining = size
        while remaining:
            block = self._fd.read(min(block_size, remaining))
            if not block:
                raise LpUnpackError('Super image ended before an extent was complete.')
            fd.write(block)
            remaining -= len(block)

    def get_info(self):
        try:
            self._fd.seek(0)
            metadata = self._read_metadata()

            filter_partition = []
            for partition in metadata.partitions:
                filter_partition.append(partition.name)

            if not filter_partition:
                raise LpUnpackError(f'Could not find partition: {self._partition_name}')

            return filter_partition

        except LpUnpackError:
            raise
        finally:
            self._fd.close()

    def unpack(self):
        try:
            self._fd.seek(0)
            metadata = self._read_metadata()

            if self._partition_name:
                filter_partition = []
                for partition in metadata.partitions:
                    if partition.name in self._partition_name:
                        filter_partition.append(partition)

                if not filter_partition:
                    raise LpUnpackError(f'Could not find partition: {self._partition_name}')

                metadata.partitions = filter_partition

            if self._slot_num:
                if self._slot_num > metadata.geometry.metadata_slot_count:
                    raise LpUnpackError(f'Invalid metadata slot number: {self._slot_num}')

            if self._show_info:
                if self._show_info_format == FormatType.TEXT:
                    print(metadata)
                elif self._show_info_format == FormatType.JSON:
                    print(f"{metadata.to_json()}\n")

            if not self._show_info and self._out_dir is None:
                raise LpUnpackError(message='Not specified directory for extraction')

            if self._out_dir:
                for partition in metadata.partitions:
                    self._extract(partition, metadata)

        except LpUnpackError:
            raise
        finally:
            self._fd.close()


def unpack(file: str, out: str, parts: list = None):
    namespace = argparse.Namespace(SUPER_IMAGE=file, OUTPUT_DIR=out, SHOW_INFO=False, NAME=parts)
    if not os.path.exists(namespace.SUPER_IMAGE):
        raise FileNotFoundError(f"{namespace.SUPER_IMAGE} Cannot Find")
    else:
        LpUnpack(**vars(namespace)).unpack()


# Merged selective super-partition extraction UI.
YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'


# ---------------------------------------------------------------------------
# 路径获取：优先使用 V（来自统一工具/工程状态），否则回退到 cwd 扫描
# ---------------------------------------------------------------------------
def _get_input_dir():
    if V and getattr(V, 'input', None):
        return V.input
    for root in (os.getcwd(), os.path.dirname(os.getcwd())):
        for name in ("INPUT", "input"):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                return p + os.sep
    return ""


def _get_out_dir():
    if V and getattr(V, 'out', None):
        return V.out
    for root in (os.getcwd(), os.path.dirname(os.getcwd())):
        for name in ("OUT", "out"):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                return p + os.sep
    return ""


def _human_size(b):
    """Convert bytes to human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# 核心：读取 super 元数据，返回分区列表 [(name, group, size_bytes), ...]
# ---------------------------------------------------------------------------
def _list_partitions(super_img_path):
    """Parse super metadata and return (sorted_partition_info, effective_img_path)."""
    job = LpUnpack(SUPER_IMAGE=super_img_path, SHOW_INFO=False)
    effective_path = job._super_image
    job._fd.seek(0)
    metadata = job._read_metadata()
    result = []
    for p in metadata.partitions:
        size = 0
        for ext_idx in range(p.num_extents):
            idx = p.first_extent_index + ext_idx
            if idx < len(metadata.extents):
                size += metadata.extents[idx].num_sectors * 512
        group = ""
        if 0 <= p.group_index < len(metadata.groups):
            group = metadata.groups[p.group_index].name
        result.append((p.name, group, size))
    job._fd.close()
    result.sort(key=lambda x: (-x[2], x[0]))  # 大->小，同大小按名字
    return result, effective_path


# ---------------------------------------------------------------------------
# UI：显示分区列表，让用户勾选
# ---------------------------------------------------------------------------
def _show_partitions(partitions):
    """Print partition list, return indices of selected partitions."""
    if not partitions:
        print(f'{RED}> 未发现任何分区{CLOSE}')
        return []

    print(f'\n{BOLD}发现 {len(partitions)} 个分区：{CLOSE}\n')
    print(f'  {"序号":>4}  {"分区名":<20} {"组":<16} {"大小":>1}')
    print(f'  {"----":>6}  {"-" * 20} {"-" * 18} {"-" * 10}')
    for i, (name, group, size) in enumerate(partitions, 1):
        print(f'  {i:>4}    {name:<20} {group:<18} {_human_size(size):>9}')

    print(f'\n{YELLOW}请输入要提取的分区序号（多个用逗号分隔，如 1,3,5）：{CLOSE}')
    print(f'{YELLOW}  输入 0 跳过（不提取任何分区）{CLOSE}')
    print(f'{YELLOW}  输入 all 全选{CLOSE}')
    ans = input('> ').strip()
    if not ans or ans == '0':
        return []
    if ans.lower() == 'all':
        return list(range(len(partitions)))

    selected = []
    for token in ans.replace('，', ',').split(','):
        token = token.strip()
        if not token:
            continue
        try:
            idx = int(token)
            if 1 <= idx <= len(partitions):
                selected.append(idx - 1)
            else:
                print(f'  {RED}无效序号: {idx}{CLOSE}')
        except ValueError:
            print(f'  {RED}无法解析: {token}{CLOSE}')
    return selected


# ---------------------------------------------------------------------------
# 核心：抽取选中的分区
# ---------------------------------------------------------------------------
def _extract_selected(super_img_path, out_dir, partitions, selected_indices):
    """Extract selected logical partitions with the embedded LP unpacker."""
    if not selected_indices:
        print(f'{YELLOW}> 未选择任何分区，跳过提取{CLOSE}')
        return

    names = [partitions[i][0] for i in selected_indices]
    print(f'\n{BOLD}> 开始提取 {len(names)} 个分区：{", ".join(names)}{CLOSE}')
    print(f'> 输出目录: {out_dir}')
    print()

    try:
        # 使用本文件内置的 LP 解包器，传入 NAME 过滤，SHOW_INFO=False，指定 OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        job = LpUnpack(
            SUPER_IMAGE=super_img_path,
            OUTPUT_DIR=out_dir,
            NAME=names,
            SHOW_INFO=False,
        )
        job.unpack()
        print(f'\n{GREEN}> 提取完成！文件已输出到 {out_dir}{CLOSE}\n')
    except Exception as e:
        print(f'{RED}> 提取失败: {e}{CLOSE}')


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def super_selective_main():
    os.system("clear")
    input_dir = _get_input_dir()
    out_dir = _get_out_dir()

    print('\n' * 8)
    print(f'{YELLOW}          super 分区选择性提取{CLOSE}')
    print()
    print(f'{YELLOW}          请将 super.img 放入 INPUT 目录{CLOSE}')
    print()
    input('          准备好后按回车继续...')

    if not input_dir:
        print(f'\n          {RED}[!] 未找到 INPUT 目录，请确认运行位置正确{CLOSE}')
        return

    super_path = os.path.join(input_dir, 'super.img')
    if not os.path.isfile(super_path):
        print(f'\n          {RED}INPUT 目录下未发现 super.img ！{CLOSE}\n')
        return

    if not out_dir:
        out_dir = input_dir.replace('INPUT', 'OUT') + os.sep

    os.system("clear")
    print(f'\n{BOLD}> 正在读取 super 元数据...{CLOSE}')
    partitions, effective_path = _list_partitions(super_path)

    if not partitions:
        print(f'{RED}> super.img 内未发现分区或解析失败{CLOSE}')
        input('> 任意键继续')
        return

    selected = _show_partitions(partitions)

    if selected:
        _extract_selected(effective_path, out_dir, partitions, selected)



def main():
    super_selective_main()

if __name__ == '__main__':
    main()
