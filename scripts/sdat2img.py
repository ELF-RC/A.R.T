#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Safely reconstruct a raw image from a non-incremental Android DAT bundle."""

from __future__ import print_function

from pathlib import Path


BLOCK_SIZE = 4096


class SdatError(RuntimeError):
    """Raised when a DAT bundle is incomplete or requires unsupported OTA state."""


def _rangeset(source):
    try:
        values = [int(item) for item in source.strip().split(',')]
    except ValueError as error:
        raise SdatError(f'无法解析块范围: {source!r}') from error
    if not values or len(values) != values[0] + 1:
        raise SdatError(f'块范围长度无效: {source!r}')

    ranges = []
    for index in range(1, len(values), 2):
        begin, end = values[index:index + 2]
        if begin < 0 or end < begin:
            raise SdatError(f'块范围无效: {source!r}')
        ranges.append((begin, end))
    return tuple(ranges)


def _parse_transfer_list(path):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            version = int(stream.readline().strip())
            new_blocks = int(stream.readline().strip())
            if new_blocks < 0:
                raise SdatError('transfer.list 的块数不能为负数')
            if version >= 2:
                stream.readline()
                stream.readline()

            commands = []
            for line_number, raw_line in enumerate(stream, start=5 if version >= 2 else 3):
                fields = raw_line.split()
                if not fields:
                    continue
                command = fields[0]
                if command in {'new', 'zero', 'erase'}:
                    if len(fields) != 2:
                        raise SdatError(f'transfer.list 第 {line_number} 行缺少范围')
                    commands.append((command, _rangeset(fields[1])))
                elif command[0].isdigit():
                    # transfer list comments/generated counters are not commands.
                    continue
                else:
                    raise SdatError(
                        f'不支持增量 OTA 命令 {command!r}；需要完整 new.dat 固件而非补丁包。'
                    )
    except OSError as error:
        raise SdatError(f'无法读取 transfer.list: {path}: {error}') from error
    except ValueError as error:
        raise SdatError(f'transfer.list 头部无效: {path}') from error
    return version, new_blocks, commands


def _write_zeroes(output, count):
    zeroes = b'\0' * min(BLOCK_SIZE, 1024 * 1024)
    remaining = count * BLOCK_SIZE
    while remaining:
        block = zeroes[:min(len(zeroes), remaining)]
        if output.write(block) != len(block):
            raise SdatError('写入零块失败')
        remaining -= len(block)


def main(transfer_list_file, new_data_file, output_image_file):
    """Build one raw image and remove an incomplete output on failure."""
    version, new_blocks, commands = _parse_transfer_list(transfer_list_file)
    print(f'sdat2img binary - version: 1.2\n')
    android_versions = {
        1: 'Android Lollipop 5.0',
        2: 'Android Lollipop 5.1',
        3: 'Android Marshmallow 6.x',
        4: 'Android Nougat 7.x / Oreo 8.x',
    }
    print(f'{android_versions.get(version, "Unknown Android")} detected!\n')

    output_path = Path(output_image_file)
    source_path = Path(new_data_file)
    if output_path.exists() or output_path.is_symlink():
        raise SdatError(f'输出镜像已存在，拒绝覆盖: {output_path}')

    largest_block = new_blocks
    for _, ranges in commands:
        for _, end in ranges:
            largest_block = max(largest_block, end)

    try:
        with open(source_path, 'rb') as new_data, open(output_path, 'xb') as output:
            for command, ranges in commands:
                for begin, end in ranges:
                    block_count = end - begin
                    output.seek(begin * BLOCK_SIZE)
                    if command == 'new':
                        print(f'\rCopying {block_count} blocks into position {begin}...', end='')
                        for _ in range(block_count):
                            block = new_data.read(BLOCK_SIZE)
                            if len(block) != BLOCK_SIZE:
                                raise SdatError('new.dat 在完整写入镜像前结束')
                            if output.write(block) != BLOCK_SIZE:
                                raise SdatError('写入 raw image 失败')
                    else:
                        _write_zeroes(output, block_count)
            output.truncate(largest_block * BLOCK_SIZE)
            output.flush()
    except Exception:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    print(f'Done! Output image: {output_path.resolve()}')
    return str(output_path)
