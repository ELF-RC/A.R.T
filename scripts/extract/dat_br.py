"""DAT / DAT.BR extraction for new.dat and new.dat.br packages."""

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.primary.utils import V, RED, GREEN, YELLOW, CLOSE, display, call
from scripts.primary.workspace import LayoutError
from scripts.primary.workspace import partition_name, workspace_partition


# Embedded DAT-to-image converter; this module no longer imports sdat2img.py.
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Safely reconstruct a raw image from a non-incremental Android DAT bundle."""


from pathlib import Path


BLOCK_SIZE = 4096


# Transfer-list parsing and DAT-to-image reconstruction.
class SdatError(RuntimeError):
    """Raised when a DAT bundle is incomplete or requires unsupported OTA state."""


# Parse inclusive block ranges from transfer.list syntax.
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


# Reconstruct one raw image from transfer commands.
def _sdat2img_main(transfer_list_file, new_data_file, output_image_file):
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



# Locate split DAT/DAT.BR fragments.
def _numbered_fragments(source):
    """Return contiguous .1/.2/... fragments or reject an incomplete bundle."""
    source_path = Path(source)
    if source_path.is_symlink() or not source_path.is_file():
        raise LayoutError(f'数据文件无效: {source_path}')
    fragments = {}
    prefix = f'{source_path.name}.'
    for candidate in source_path.parent.iterdir():
        if candidate.name == source_path.name or not candidate.name.startswith(prefix):
            continue
        suffix = candidate.name[len(prefix):]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        if index < 1 or candidate.is_symlink() or not candidate.is_file() or index in fragments:
            raise LayoutError(f'分段数据文件无效: {candidate}')
        fragments[index] = candidate
    if fragments:
        expected = set(range(1, max(fragments) + 1))
        missing = sorted(expected - set(fragments))
        if missing:
            raise LayoutError(f'分段数据缺失: {", ".join(map(str, missing))}')
    return [fragments[index] for index in sorted(fragments)]


def _combine_fragments(source):
    """Combine numbered fragments into a single file in WORKSPACE root, return the path."""
    source_path = Path(source)
    fragments = _numbered_fragments(source_path)
    if not fragments:
        return str(source)
    dest = Path(V.workspace) / source_path.name
    sources = [source_path, *fragments]
    with open(dest, 'xb') as destination_file:
        for index, fragment in enumerate(sources):
            if index:
                display(f'合并: {fragment.name} ...')
            with open(fragment, 'rb') as source_file:
                shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
    return str(dest)


# Decode one plain .new.dat partition.
def decompress_dat(transfer, source, distance=None, keep=0):
    """Convert DAT directly: read transfer.list + dat from INPUT, extract to partition."""
    from scripts.extract.image import decompress_img

    del distance, keep
    if not transfer or not os.path.isfile(transfer):
        print(f'> 未找到 {os.path.basename(source).split(".")[0]}.transfer.list')
        return

    combined = None
    raw_image = None
    try:
        import time as _time
        s_time = _time.time()
        partition = partition_name(source)
        combined = _combine_fragments(source)
        raw_image = os.path.join(V.workspace, f'{partition}.img')
        display(f"正在分解: {os.path.basename(combined)} ...", 3)
        _sdat2img_main(transfer, combined, raw_image)
        if not os.path.isfile(raw_image):
            raise SdatError('未生成 raw image')
        print("\x1b[1;32m [%ds]\x1b[0m" % (_time.time() - s_time))
        decompress_img(raw_image, workspace_partition(partition))
    except (LayoutError, OSError, ValueError, SdatError) as error:
        print(f'> DAT 分解失败: {error}')
    finally:
        for f in (combined, raw_image):
            if f and f != source and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


# Decode Brotli-compressed DAT fragments.
def decompress_bro(transfer, source, distance=None, keep=0):
    """Decompress BR directly from INPUT, then run DAT pipeline."""
    del distance, keep
    if not transfer or not os.path.isfile(transfer):
        print(f'> 未找到 {os.path.basename(source).split(".")[0]}.transfer.list')
        return

    combined = None
    staged_dat = None
    try:
        import time as _time
        s_time = _time.time()
        combined = _combine_fragments(source)
        if not combined.endswith('.br'):
            raise LayoutError(f'BROTLI 文件扩展名无效: {combined}')
        staged_dat = combined[:-3]
        display(f"正在分解: {os.path.basename(source)} ...", 3)
        if call(['brotli', '-df', combined, '-o', staged_dat]) != 0:
            raise LayoutError('brotli 解压失败')
        if not os.path.isfile(staged_dat):
            raise LayoutError('brotli 未生成 new.dat')
        print("\x1b[1;32m [%ds]\x1b[0m" % (_time.time() - s_time))
        decompress_dat(transfer, staged_dat)
    except (LayoutError, OSError) as error:
        print(f'> BROTLI 分解失败: {error}')
    finally:
        for f in (combined, staged_dat):
            if f and f != source and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def _list_dat_partitions(infile):
    """Scan dat.br/dat files, pair with transfer.list, return sorted list of dicts."""
    items = []
    for part in sorted(infile):
        if not os.path.isfile(part):
            continue
        transfer = os.path.join(os.path.dirname(part), os.path.basename(part).split('.')[0] + '.transfer.list')
        if not os.path.isfile(transfer):
            print(f'> 跳过 {os.path.basename(part)}：未找到 transfer.list')
            continue
        name = partition_name(part)
        size = os.path.getsize(part)
        items.append({"path": part, "transfer": transfer, "partition": name, "size": size})
    return items


def _decompress_single_partition(item, flag):
    """Worker for parallel dat.br/dat decomposition. Returns result dict."""
    name = item["partition"]
    try:
        if flag == 2:
            decompress_bro(item["transfer"], item["path"])
        elif flag == 3:
            decompress_dat(item["transfer"], item["path"])
        return {"partition": name, "success": True, "error": None}
    except (LayoutError, OSError, ValueError, SdatError) as error:
        return {"partition": name, "success": False, "error": str(error)}


# Batch entry point used by the image dispatcher.
def decompress_dat_batch(infile, flag):
    """Batch decompress dat.br or dat files with interactive selection and parallel execution."""
    items = _list_dat_partitions(infile)
    if not items:
        print(f'> 未发现可用的 {"dat.br" if flag == 2 else "dat"} 文件')
        return

    if not V.JM:
        label = "dat.br" if flag == 2 else "dat"
        print(f'\n> 发现以下 {label} 文件：\n')
        for i, it in enumerate(items, 1):
            print(f'  {YELLOW}[{i:>2}]{CLOSE}\t{GREEN}{os.path.basename(it["path"])}{CLOSE}')
        print(f'\n{YELLOW}请输入要分解的序号（多个用逗号分隔，0跳过）{CLOSE}')
        ans = input('> ').strip()
        if not ans or ans == '0':
            return
        selected = []
        for token in ans.replace('，', ',').split(','):
            token = token.strip()
            if not token:
                continue
            try:
                idx = int(token)
                if 1 <= idx <= len(items):
                    selected.append(items[idx - 1])
                else:
                    print(f'  {RED}无效序号: {idx}{CLOSE}')
            except ValueError:
                print(f'  {RED}无法解析: {token}{CLOSE}')
        if not selected:
            print('> 未选择任何分区')
            return
    else:
        selected = items

    workers = min(len(selected), os.cpu_count() or 2)
    print(f'\n> 并行分解中......\n')
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_decompress_single_partition, it, flag): it for it in selected}
        for future in as_completed(futures):
            result = future.result()
            if result["success"]:
                print(f'  {GREEN}✓{CLOSE} {result["partition"]}')
                ok += 1
            else:
                print(f'  {RED}✗{CLOSE} {result["partition"]}: {result["error"]}')
                fail += 1
    print(f'\n> 分解完成: {ok}/{ok + fail} 成功')
