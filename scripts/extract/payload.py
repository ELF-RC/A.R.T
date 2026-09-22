"""Standalone payload.bin extraction and optional image decomposition."""

from __future__ import annotations

import bz2
import lzma
import os
import re
import shutil
import struct
from dataclasses import dataclass
from glob import glob
from pathlib import Path

from scripts.primary.utils import V, RED, GREEN, YELLOW, MAGENTA, CLOSE
from scripts.primary.workspace import (
    LayoutError, _stage_work_source, partition_name, workspace_partition,
)


class PayloadError(RuntimeError):
    """Raised when a payload cannot be parsed or extracted safely."""


@dataclass(frozen=True)
class _Extent:
    start_block: int
    num_blocks: int


@dataclass(frozen=True)
class _InstallOperation:
    operation_type: int
    data_offset: int
    data_length: int
    src_extents: tuple[_Extent, ...]
    dst_extents: tuple[_Extent, ...]


@dataclass(frozen=True)
class _PartitionUpdate:
    partition_name: str
    size: int
    operations: tuple[_InstallOperation, ...]


_REPLACE = 0
_REPLACE_BZ = 1
_SOURCE_COPY = 4
_ZERO = 6
_REPLACE_XZ = 8


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 70:
            break
    raise PayloadError('Payload protobuf varint 无效或不完整')


def _iter_fields(data: bytes):
    """Read protobuf fields needed by update_engine payload manifests."""
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if field_number == 0:
            raise PayloadError('Payload protobuf 字段编号无效')
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise PayloadError('Payload protobuf 固定字段越界')
            value, offset = data[offset:end], end
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise PayloadError('Payload protobuf 消息越界')
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise PayloadError('Payload protobuf 固定字段越界')
            value, offset = data[offset:end], end
        else:
            raise PayloadError(f'不支持的 Payload protobuf wire type: {wire_type}')
        yield field_number, wire_type, value


def _parse_extent(data: bytes) -> _Extent:
    start_block = num_blocks = 0
    for field, wire, value in _iter_fields(data):
        if wire != 0:
            continue
        if field == 1:
            start_block = value
        elif field == 2:
            num_blocks = value
    return _Extent(start_block, num_blocks)


def _parse_operation(data: bytes) -> _InstallOperation:
    operation_type = _REPLACE
    data_offset = data_length = 0
    src_extents, dst_extents = [], []
    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 0:
            operation_type = value
        elif field == 2 and wire == 0:
            data_offset = value
        elif field == 3 and wire == 0:
            data_length = value
        elif field == 4 and wire == 2:
            src_extents.append(_parse_extent(value))
        elif field == 6 and wire == 2:
            dst_extents.append(_parse_extent(value))
    return _InstallOperation(
        operation_type, data_offset, data_length,
        tuple(src_extents), tuple(dst_extents),
    )


def _parse_partition(data: bytes) -> _PartitionUpdate:
    name = ''
    size = 0
    operations = []
    for field, wire, value in _iter_fields(data):
        if field == 1 and wire == 2:
            name = value.decode('utf-8', 'strict')
        elif field == 7 and wire == 2:
            for nested_field, nested_wire, nested_value in _iter_fields(value):
                if nested_field == 1 and nested_wire == 0:
                    size = nested_value
        elif field == 8 and wire == 2:
            operations.append(_parse_operation(value))
    if not name:
        raise PayloadError('Payload manifest 中存在空分区名')
    return _PartitionUpdate(name, size, tuple(operations))


def _parse_manifest(data: bytes) -> tuple[int, tuple[_PartitionUpdate, ...]]:
    block_size = 4096
    partitions = []
    for field, wire, value in _iter_fields(data):
        if field == 3 and wire == 0:
            block_size = value
        elif field == 13 and wire == 2:
            partitions.append(_parse_partition(value))
    if block_size <= 0:
        raise PayloadError('Payload block_size 无效')
    return block_size, tuple(partitions)


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise PayloadError('Payload 文件在读取头部时提前结束')
    return data


def _read_payload_header(payload_path: str):
    with open(payload_path, 'rb') as payload_file:
        if _read_exact(payload_file, 4) != b'CrAU':
            raise PayloadError('不是有效的 payload.bin')
        version = struct.unpack('>Q', _read_exact(payload_file, 8))[0]
        if version != 2:
            raise PayloadError(f'不支持的 payload 格式版本: {version}')
        manifest_size = struct.unpack('>Q', _read_exact(payload_file, 8))[0]
        signature_size = struct.unpack('>I', _read_exact(payload_file, 4))[0]
        manifest = _read_exact(payload_file, manifest_size)
        _read_exact(payload_file, signature_size)
        data_offset = payload_file.tell()
    block_size, partitions = _parse_manifest(manifest)
    return data_offset, block_size, partitions


def _validate_partition(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise PayloadError(f'非法 payload 分区名称: {name!r}')
    if name in {'.', '..', 'config', 'INPUT', 'OUT', 'WORKSPACE'}:
        raise PayloadError(f'保留 payload 分区名称: {name!r}')
    return name


class _LimitedReader:
    def __init__(self, stream, length: int, chunk_size: int):
        self.stream = stream
        self.remaining = length
        self.chunk_size = chunk_size

    def read(self, size=-1):
        if self.remaining == 0:
            return b''
        size = self.chunk_size if size < 0 else min(size, self.chunk_size)
        data = self.stream.read(min(size, self.remaining))
        if not data:
            raise PayloadError('Payload 压缩数据提前结束')
        self.remaining -= len(data)
        return data


class _ExtentWriter:
    def __init__(self, output_file, extents, block_size):
        self.output_file = output_file
        self.extents = tuple(extents)
        self.block_size = block_size
        self.expected = sum(item.num_blocks * block_size for item in self.extents)
        self.written = 0
        self.index = 0
        self.remaining = 0

    def _advance(self):
        while self.remaining == 0 and self.index < len(self.extents):
            extent = self.extents[self.index]
            self.index += 1
            self.remaining = extent.num_blocks * self.block_size
            if self.remaining:
                self.output_file.seek(extent.start_block * self.block_size)
        if self.remaining == 0:
            raise PayloadError('Payload 操作写出了目标 extents')

    def write(self, data):
        view = memoryview(data)
        while view:
            if self.remaining == 0:
                self._advance()
            size = min(len(view), self.remaining)
            written = self.output_file.write(view[:size])
            if written is None:
                written = size
            if written != size:
                raise PayloadError('Payload extents 写入不完整')
            self.remaining -= written
            self.written += written
            view = view[written:]

    def write_zeroes(self):
        zero_block = b'\0' * min(self.block_size, 1024 * 1024)
        remaining = self.expected
        while remaining:
            size = min(remaining, len(zero_block))
            self.write(zero_block[:size])
            remaining -= size

    def finish(self):
        if self.written != self.expected:
            raise PayloadError(
                f'Payload 操作写入 {self.written} 字节，预期 {self.expected} 字节'
            )


class _PayloadDumper:
    def __init__(self, payload_path, output_dir, images=(), buffer_size=1024 * 1024):
        self.payload_path = str(payload_path)
        self.output_dir = Path(output_dir)
        self.images = tuple(images)
        self.buffer_size = buffer_size
        self.data_offset, self.block_size, self.partitions = _read_payload_header(
            self.payload_path
        )

    def run(self) -> bool:
        wanted = set(self.images)
        selected = [
            item for item in self.partitions
            if not wanted or item.partition_name in wanted
        ]
        for name in sorted(wanted - {item.partition_name for item in selected}):
            print(f'> Payload 中未找到分区: {name}')
        if not selected:
            print('> Payload 没有可分解的分区')
            return False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for partition in selected:
            self._dump_partition(partition)
        return True

    def _dump_partition(self, partition):
        name = _validate_partition(partition.partition_name)
        output_path = self.output_dir / f'{name}.img'
        operations = tuple(
            (self.data_offset + operation.data_offset, operation)
            for operation in partition.operations
        )
        total_size = partition.size
        for _, operation in operations:
            for extent in operation.dst_extents:
                total_size = max(
                    total_size,
                    (extent.start_block + extent.num_blocks) * self.block_size,
                )
        with open(output_path, 'wb') as output_file:
            output_file.truncate(total_size)
        with open(self.payload_path, 'rb') as payload_file, open(output_path, 'r+b') as output_file:
            for data_offset, operation in operations:
                self._write_operation(data_offset, operation, payload_file, output_file)
        print(f'> Payload 分解完成: {name}.img')

    def _read_data(self, stream, size):
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, self.buffer_size))
            if not chunk:
                raise PayloadError('Payload 操作数据提前结束')
            remaining -= len(chunk)
            yield chunk

    def _write_compressed(self, decoder, payload_file, data_length, writer):
        for compressed in self._read_data(payload_file, data_length):
            pending = compressed
            while True:
                data = decoder.decompress(pending, max_length=self.buffer_size)
                if data:
                    writer.write(data)
                if decoder.eof or decoder.needs_input:
                    break
                if not data:
                    raise PayloadError('Payload 压缩解码器没有继续处理')
                pending = b''
        if not decoder.eof:
            raise PayloadError('Payload 压缩操作未正常结束')

    def _write_operation(self, data_offset, operation, payload_file, output_file):
        payload_file.seek(data_offset)
        writer = _ExtentWriter(output_file, operation.dst_extents, self.block_size)
        if operation.operation_type == _REPLACE_XZ:
            self._write_compressed(
                lzma.LZMADecompressor(), payload_file, operation.data_length, writer
            )
        elif operation.operation_type == _REPLACE_BZ:
            self._write_compressed(
                bz2.BZ2Decompressor(), payload_file, operation.data_length, writer
            )
        elif operation.operation_type == _REPLACE:
            header = payload_file.read(min(4, operation.data_length))
            payload_file.seek(data_offset)
            if header == b'\x28\xb5\x2f\xfd':
                try:
                    import zstandard
                except ImportError as error:
                    raise PayloadError(
                        'Payload 使用 Zstandard，但未安装 zstandard'
                    ) from error
                limited = _LimitedReader(
                    payload_file, operation.data_length, self.buffer_size
                )
                decoder = zstandard.ZstdDecompressor().stream_reader(
                    limited, read_size=self.buffer_size
                )
                try:
                    while True:
                        data = decoder.read(self.buffer_size)
                        if not data:
                            break
                        writer.write(data)
                finally:
                    decoder.close()
                if limited.remaining:
                    raise PayloadError('Zstandard 操作未消耗完整输入')
            else:
                for data in self._read_data(payload_file, operation.data_length):
                    writer.write(data)
        elif operation.operation_type == _SOURCE_COPY:
            raise PayloadError('不支持没有旧镜像的 SOURCE_COPY 操作')
        elif operation.operation_type == _ZERO:
            writer.write_zeroes()
        else:
            raise PayloadError(f'不支持的 Payload 操作类型: {operation.operation_type}')
        writer.finish()


def info(payload_file):
    """Return payload partition names and sizes for the interactive selector."""
    _, _, partitions = _read_payload_header(str(payload_file))
    return [(item.partition_name, item.size) for item in partitions]


def run(payload_file, output_dir, partition):
    """Extract one payload partition into output_dir."""
    return _PayloadDumper(payload_file, output_dir, images=(partition,)).run()


def main(payload_file, output_dir):
    """Extract all payload partitions into output_dir."""
    return _PayloadDumper(payload_file, output_dir).run()


def _human_size(size):
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    if size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    return f'{size / (1024 * 1024 * 1024):.2f} GB'


def _runtime_path(name, fallback):
    value = getattr(V, name, None)
    if not value or value == 'None':
        return str(fallback)
    return str(value)


def _decompress_payload_images(payload, payload_dir, mode):
    """Extract payload partitions, then optionally dispatch each image."""
    payload_partitions = info(payload)
    if not payload_partitions:
        raise PayloadError('Payload 不包含分区')

    if mode == '1':
        print(f'> {YELLOW}包含的所有镜像文件: {CLOSE}\n')
        name_width = max(len(name) for name, _ in payload_partitions) + 4
        for index, (name, size) in enumerate(payload_partitions):
            print(
                f'  {GREEN}{name}{CLOSE}'.ljust(
                    name_width + len(GREEN) + len(CLOSE)
                ),
                f'{_human_size(size):>10}',
                end='' if index % 2 == 0 else '\n',
            )
        if len(payload_partitions) % 2:
            print()
        names = {name for name, _ in payload_partitions}
        selected = input(
            f'> {RED}根据以上信息输入一个或多个镜像，以空格分开{CLOSE}\n> {MAGENTA}'
        ).split()
        for partition in selected:
            if partition in names:
                run(payload, payload_dir, partition)
            else:
                print(f'> 跳过未知 Payload 分区: {partition}')
    else:
        print(f'> {YELLOW}提取【{os.path.basename(payload)}】所有镜像文件:{CLOSE}\n')
        main(payload, payload_dir)

    images = sorted(glob(os.path.join(payload_dir, '*.img')))
    if input('> 是否继续分解img [0/1]: ').strip() != '1':
        out_dir = _runtime_path('out', Path(payload_dir).parent / 'OUT')
        os.makedirs(out_dir, exist_ok=True)
        for image in images:
            destination = os.path.join(out_dir, os.path.basename(image))
            if os.path.isfile(destination):
                os.remove(destination)
            shutil.move(image, destination)
            print(f'> {os.path.basename(image)} -> {out_dir}')
        return

    # Only after confirmation do we connect to the image dispatcher.
    from scripts.extract.image import decompress_img

    for image in images:
        try:
            destination = workspace_partition(partition_name(image))
        except (AttributeError, LayoutError):
            destination = os.path.join(payload_dir, partition_name(image))
            os.makedirs(destination, exist_ok=True)
        decompress_img(image, destination)
    for image in images:
        if os.path.isfile(image):
            os.remove(image)


def decompress_bin(infile, outdir=None, flag='1'):
    """Extract payload.bin without importing another Payload implementation."""
    os.system('clear')
    try:
        payload = _stage_work_source(infile, 'payload')
        payload_dir = _runtime_path('workspace', outdir or Path(infile).parent)
        _decompress_payload_images(payload, payload_dir, flag)
    except (LayoutError, OSError, PayloadError, ValueError) as error:
        print(f'> Payload 分解失败: {error}')
        input('> 任意键继续')
