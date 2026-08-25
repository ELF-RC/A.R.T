#!/usr/bin/env python
import bz2
import lzma
import os
import struct
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count

import zstandard
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn

from pys import update_metadata_pb2 as um
from pys.project_layout import ProjectLayout

flatten = lambda l: [item for sublist in l for item in sublist]


def u32(x):
    return struct.unpack(">I", x)[0]


def u64(x):
    return struct.unpack(">Q", x)[0]


class PayloadError(RuntimeError):
    """Raised when a payload operation cannot be reconstructed safely."""


class _LimitedReader:
    """Expose exactly one payload operation to a streaming decoder."""

    def __init__(self, stream, length, chunk_size):
        self.stream = stream
        self.remaining = length
        self.chunk_size = chunk_size

    def read(self, size=-1):
        if self.remaining == 0:
            return b''
        if size < 0:
            size = self.chunk_size
        size = min(size, self.chunk_size, self.remaining)
        data = self.stream.read(size)
        if not data:
            raise PayloadError('Payload data ended before the operation was complete')
        self.remaining -= len(data)
        return data


class ExtentWriter:
    """Write a sequential byte stream across Android payload destination extents."""

    def __init__(self, out_file, extents, block_size, progress=None, task_id=None):
        self.out_file = out_file
        self.extents = tuple(extents)
        self.block_size = block_size
        self.expected = sum(extent.num_blocks * block_size for extent in self.extents)
        self.written = 0
        self.index = 0
        self.remaining = 0
        self.progress = progress
        self.task_id = task_id

    def _advance(self):
        while self.remaining == 0 and self.index < len(self.extents):
            extent = self.extents[self.index]
            self.index += 1
            self.remaining = extent.num_blocks * self.block_size
            if self.remaining:
                self.out_file.seek(extent.start_block * self.block_size)
        if self.remaining == 0:
            raise PayloadError('Payload operation exceeds destination extents')

    def write(self, data):
        view = memoryview(data)
        while view:
            if self.remaining == 0:
                self._advance()
            size = min(len(view), self.remaining)
            written = self.out_file.write(view[:size])
            if written is None:
                written = size
            if written != size:
                raise PayloadError('Failed to write complete payload extent data')
            self.remaining -= written
            self.written += written
            if self.progress and self.task_id is not None:
                self.progress.advance(self.task_id, written)
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
                f'Payload operation wrote {self.written} bytes for {self.expected} destination bytes'
            )


class Dumper:
    def __init__(
            self, payloadfile, out, diff=None, old=None, images="", workers=cpu_count(), buffsize=8192
    ):
        self.payloadpath = payloadfile
        payloadfile = self.open_payloadfile()
        self.payloadfile = payloadfile
        self.tls = threading.local()
        self.out = out
        self.diff = diff
        self.old = old
        self.images = images
        self.workers = workers
        self.buffsize = buffsize
        self.validate_magic()

    def open_payloadfile(self):
        return open(self.payloadpath, 'rb')

    def run(self, slow=False) -> bool:
        if self.images == "":
            partitions = self.dam.partitions
        else:
            partitions = []
            for image in self.images:
                found = False
                for dam_part in self.dam.partitions:
                    if dam_part.partition_name == image:
                        partitions.append(dam_part)
                        found = True
                        break
                if not found:
                    print(f"Partition {image} not found in image")

        if len(partitions) == 0:
            print("Not operating on any partitions")
            return False

        partitions_with_ops = []
        for partition in partitions:
            operations = []
            for operation in partition.operations:
                self.payloadfile.seek(self.data_offset + operation.data_offset)
                operations.append(
                    {
                        "data_offset": self.payloadfile.tell(),
                        "operation": operation,
                        "data_length": operation.data_length,
                    }
                )
            partitions_with_ops.append(
                {
                    "partition": partition,
                    "operations": operations,
                }
            )

        self.payloadfile.close()
        if slow:
            self.extract_slow(partitions_with_ops)
        else:
            self.multiprocess_partitions(partitions_with_ops)
        return True

    def extract_slow(self, partitions):
        with Progress(
            '[progress.description]{task.description}',
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            for part in partitions:
                task_id = progress.add_task(f'[cyan]{part["partition"].partition_name}[/]', total=None)
                self.dump_part(part, progress, task_id)
                progress.update(task_id, description=f'[green]{part["partition"].partition_name}[/]')

    def multiprocess_partitions(self, partitions):
        with Progress(
            '[progress.description]{task.description}',
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            tasks = {}
            for part in partitions:
                name = part['partition'].partition_name
                task_id = progress.add_task(f'[cyan]{name}[/]', total=None)
                tasks[part['partition'].partition_name] = task_id

            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(self.dump_part, part, progress, tasks[part['partition'].partition_name]): part
                    for part in partitions
                }
                for future in as_completed(futures):
                    partition_name = futures[future]['partition'].partition_name
                    task_id = tasks[partition_name]
                    try:
                        future.result()
                        progress.update(task_id, description=f'[green]{partition_name}[/]')
                    except Exception as exc:
                        progress.update(task_id, description=f'[red]{partition_name}[/]')
                        print(f"{partition_name} - processing generated an exception: {exc}")

    def validate_magic(self):
        magic = self.payloadfile.read(4)
        assert magic == b"CrAU"
        file_format_version = u64(self.payloadfile.read(8))
        assert file_format_version == 2
        manifest_size = u64(self.payloadfile.read(8))
        metadata_signature_size = 0
        if file_format_version > 1:
            metadata_signature_size = u32(self.payloadfile.read(4))
        manifest = self.payloadfile.read(manifest_size)
        self.metadata_signature = self.payloadfile.read(metadata_signature_size)
        self.data_offset = self.payloadfile.tell()
        self.dam = um.DeltaArchiveManifest()
        self.dam.ParseFromString(manifest)
        self.block_size = self.dam.block_size

    @staticmethod
    def _read_exact(stream, size, chunk_size):
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, chunk_size))
            if not chunk:
                raise PayloadError('Payload data ended before the operation was complete')
            remaining -= len(chunk)
            yield chunk

    def _write_compressed(self, decoder, payloadfile, data_length, writer):
        for compressed in self._read_exact(payloadfile, data_length, self.buffsize):
            pending = compressed
            while True:
                data = decoder.decompress(pending, max_length=self.buffsize)
                if data:
                    writer.write(data)
                if decoder.eof or decoder.needs_input:
                    break
                if not data:
                    raise PayloadError('Compressed payload decoder made no progress')
                pending = b''
        if not decoder.eof:
            raise PayloadError('Compressed payload operation did not reach end of stream')

    def data_for_op(self, operation, out_file, old_file, progress=None, task_id=None):
        payloadfile = self.tls.payloadfile
        payloadfile.seek(operation['data_offset'])
        data_length = operation['data_length']
        op = operation['operation']
        writer = ExtentWriter(out_file, op.dst_extents, self.block_size, progress, task_id)

        if op.type == op.REPLACE_XZ:
            self._write_compressed(lzma.LZMADecompressor(), payloadfile, data_length, writer)
        elif op.type == op.REPLACE_BZ:
            self._write_compressed(bz2.BZ2Decompressor(), payloadfile, data_length, writer)
        elif op.type == op.REPLACE:
            header = payloadfile.read(min(4, data_length))
            payloadfile.seek(operation['data_offset'])
            if header == b'\x28\xb5\x2f\xfd':
                limited = _LimitedReader(payloadfile, data_length, self.buffsize)
                decoder = zstandard.ZstdDecompressor().stream_reader(
                    limited,
                    read_size=self.buffsize,
                )
                try:
                    while True:
                        data = decoder.read(self.buffsize)
                        if not data:
                            break
                        writer.write(data)
                finally:
                    decoder.close()
                if limited.remaining:
                    raise PayloadError('Zstandard payload operation ended before all input was read')
            else:
                for data in self._read_exact(payloadfile, data_length, self.buffsize):
                    writer.write(data)
        elif op.type == op.SOURCE_COPY:
            if not self.diff or old_file is None:
                raise PayloadError('SOURCE_COPY requires a differential OTA source image')
            for extent in op.src_extents:
                old_file.seek(extent.start_block * self.block_size)
                for data in self._read_exact(old_file, extent.num_blocks * self.block_size, self.buffsize):
                    writer.write(data)
        elif op.type == op.ZERO:
            writer.write_zeroes()
        else:
            raise PayloadError(f'Unsupported payload operation type: {op.type:d}')
        writer.finish()

    def _dump_chunk(self, chunk_ops, output_path, progress=None, task_id=None):
        """Process a chunk of operations with independent file handles."""
        with self.open_payloadfile() as payloadfile:
            self.tls.payloadfile = payloadfile
            with open(output_path, 'r+b') as out_file:
                for op in chunk_ops:
                    self.data_for_op(op, out_file, None, progress, task_id)

    def dump_part(self, part, progress=None, task_id=None):
        name = ProjectLayout.validate_component(part["partition"].partition_name, 'payload 分区')
        output_path = Path(self.out) / f'{name}.img'
        operations = part["operations"]

        # Calculate total output size for pre-allocation
        total_size = 0
        for op in operations:
            for extent in op["operation"].dst_extents:
                end = (extent.start_block + extent.num_blocks) * self.block_size
                if end > total_size:
                    total_size = end

        # Pre-allocate output file
        with open(output_path, 'wb') as f:
            f.truncate(total_size)

        # Update progress bar description
        if progress and task_id is not None:
            size_mb = total_size / (1024 * 1024)
            progress.update(task_id, description=f'[cyan]{name:<16}[/] {size_mb:.0f}MB', total=total_size)

        # Determine optimal thread count: each thread processes at least 16MB, cap at 64
        num_chunks = min(64, self.workers, max(1, total_size // (16 * 1024 * 1024)))

        if num_chunks <= 1:
            with self.open_payloadfile() as payloadfile:
                self.tls.payloadfile = payloadfile
                with open(output_path, 'r+b') as out_file:
                    self.do_ops_for_part(part, out_file, None, progress, task_id)
            return

        chunk_size = (len(operations) + num_chunks - 1) // num_chunks
        chunks = [operations[i:i + chunk_size] for i in range(0, len(operations), chunk_size)]

        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            futures = [executor.submit(self._dump_chunk, chunk, str(output_path), progress, task_id) for chunk in chunks]
            for future in as_completed(futures):
                future.result()

    def do_ops_for_part(self, part, out_file, old_file, progress=None, task_id=None):
        for op in part["operations"]:
            self.data_for_op(op, out_file, old_file, progress, task_id)


def info(payloadfile):
    """Return list of (partition_name, size_bytes) for the interactive extractor."""
    dumper = Dumper(payloadfile, out="")
    try:
        return [(part.partition_name, part.new_partition_info.size) for part in dumper.dam.partitions]
    finally:
        dumper.payloadfile.close()


def info_names(payloadfile):
    """Return payload partition names as a space-separated string (legacy)."""
    return " ".join(name for name, _ in info(payloadfile))


def run(payloadfile, out, partition):
    """Extract one payload partition into the caller-provided staging directory."""
    os.makedirs(out, exist_ok=True)
    return Dumper(payloadfile, out, images=[partition]).run()


def main(payloadfile, out):
    """Extract all payload partitions into the caller-provided staging directory."""
    os.makedirs(out, exist_ok=True)
    return Dumper(payloadfile, out).run()
