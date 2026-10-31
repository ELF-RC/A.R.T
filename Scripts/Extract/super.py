"""Super image extraction, logical partitions, and selective extraction."""

import os
import shutil
from pathlib import Path

from Scripts.Primary.Utils import V, display_width, human_size, pad
from Scripts.Primary.ImageTools import expand_sparse_in_place
from Scripts.Primary.WorkSpace import workspace_partition
from Scripts.Primary.SuperTools import LpUnpack, LpUnpackError, unpack


# Normalize logical-partition image names before recursion.
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


def _move_super_images_to_out(super_dir, out_dir=None):
    """Move remaining .img files from super_dir to OUT."""
    out_dir = out_dir or V.out
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
            print(f'已输出: {name} -> {out_dir}')


def extract_super(working_source, partition):
    """Extract a super.img into WORKSPACE.

    Returns True if handled (either extracted or moved to OUT), False on failure.
    """
    from Scripts.Extract.image import decompress_img

    print(f'正在分解: {os.path.basename(working_source)} <super>')
    super_dir = os.path.join(V.workspace, 'super') + os.sep
    try:
        unpack(working_source, super_dir)
    except (LpUnpackError, OSError, ValueError, SystemExit) as error:
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
# Merged selective super-partition extraction UI.
YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'


# ---------------------------------------------------------------------------
# Resolve paths from V (shared runtime/project state), then fall back to scanning cwd.
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


# ---------------------------------------------------------------------------
# Core: read super metadata and return [(name, group, size_bytes), ...].
# ---------------------------------------------------------------------------
# Read metadata for the selective extraction screen.
def _list_partitions(super_img_path):
    """Parse super metadata and return (sorted_partition_info, effective_img_path)."""
    job = LpUnpack(SUPER_IMAGE=super_img_path, SHOW_INFO=False)
    effective_path = super_img_path
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
    job.close()
    result.sort(key=lambda x: (-x[2], x[0]))  # Sort by size descending, then by name.
    return result, effective_path


# ---------------------------------------------------------------------------
# UI: display the partition list and let the user select entries.
# ---------------------------------------------------------------------------
def _show_partitions(partitions):
    """Print the selectable partitions, return the chosen (name, group, size).

    Zero-byte partitions are dropped before the list is built: extracting one
    only produces an empty .img that the next stage skips, so offering it as a
    choice is a dead end. They are reported once instead, and the numbering
    covers only what can actually be extracted.
    """
    empty = [name for name, _group, size in partitions if size <= 0]
    selectable = [p for p in partitions if p[2] > 0]

    if empty:
        print(f'{YELLOW}> 跳过 {len(empty)} 个空分区（0 B）：{", ".join(empty)}{CLOSE}')
    if not selectable:
        print(f'{RED}> 未发现可提取的分区{CLOSE}')
        return []

    # Column widths follow the content: group names such as
    # 'qti_dynamic_partitions_a' are wider than any fixed guess, and a fixed
    # width pushes 大小 out of line with its separator.
    name_w = max([display_width(p[0]) for p in selectable] + [8])
    group_w = max([display_width(p[1]) for p in selectable] + [4])
    size_w = 9

    print(f'\n{BOLD}发现 {len(selectable)} 个分区：{CLOSE}\n')
    print(f'  {pad("序号", 4, "right")}  {pad("分区名", name_w)}  '
          f'{pad("组", group_w)} {pad("大小", size_w, "right")}')
    print(f'  {"-" * 4}  {"-" * name_w}  {"-" * group_w} {"-" * size_w}')
    for i, (name, group, size) in enumerate(selectable, 1):
        print(f'  {pad(str(i), 4, "right")}  {pad(name, name_w)}  '
              f'{pad(group, group_w)} {pad(human_size(size), size_w, "right")}')

    print(f'\n{YELLOW}请输入要提取的分区序号（多个用逗号分隔，如 1,3,5）：{CLOSE}')
    print(f'{YELLOW}  输入 0 跳过（不提取任何分区）{CLOSE}')
    print(f'{YELLOW}  输入 all 全选{CLOSE}')
    ans = input('> ').strip()
    if not ans or ans == '0':
        return []
    if ans.lower() == 'all':
        return list(selectable)

    selected = []
    for token in ans.replace('，', ',').split(','):
        token = token.strip()
        if not token:
            continue
        try:
            idx = int(token)
            if 1 <= idx <= len(selectable):
                entry = selectable[idx - 1]
                if entry not in selected:
                    selected.append(entry)
            else:
                print(f'  {RED}无效序号: {idx}{CLOSE}')
        except ValueError:
            print(f'  {RED}无法解析: {token}{CLOSE}')
    return selected


# ---------------------------------------------------------------------------
# Core: extract the selected partitions.
# ---------------------------------------------------------------------------
# Extract selected logical partitions.
def _extract_selected(super_img_path, stage_dir, selected):
    """Extract the chosen logical partitions into the WORKSPACE/super staging area.

    Returns the images actually written. They stay in the staging area until
    the caller either decomposes them or normalizes and moves them to OUT.
    """
    if not selected:
        print(f'{YELLOW}> 未选择任何分区，跳过提取{CLOSE}')
        return []

    names = [name for name, _group, _size in selected]
    print(f'\n{BOLD}> 开始提取 {len(names)} 个分区：{", ".join(names)}{CLOSE}')
    print()

    try:
        # Use the embedded LP unpacker with NAME filtering, SHOW_INFO=False, and OUTPUT_DIR.
        os.makedirs(stage_dir, exist_ok=True)
        job = LpUnpack(
            SUPER_IMAGE=super_img_path,
            OUTPUT_DIR=stage_dir,
            NAME=names,
            SHOW_INFO=False,
        )
        job.unpack()
        print(f'\n{GREEN}> 提取完成{CLOSE}\n')
    except (LpUnpackError, OSError, ValueError) as e:
        print(f'Process log: {e}')
        print('\x1b[1;31mFailed !\x1b[0m')
        return []

    # The unpacker writes <name>.img, and the name is validated but never
    # rewritten, so the file stem is the partition name.
    produced = []
    for name in names:
        image = os.path.join(stage_dir, f'{name}.img')
        if os.path.isfile(image) and os.path.getsize(image) > 0:
            produced.append((image, name))
    return produced


# ---------------------------------------------------------------------------
# Optional second stage: decompose the freshly extracted images in place.
# ---------------------------------------------------------------------------
# Decompose the staged logical-partition images into WORKSPACE.
def _decompose_extracted(stage_dir):
    """Normalize A/B names, then decompose every staged image into WORKSPACE."""
    from Scripts.Extract.image import decompress_img

    # Rename <part>_a/_b -> <part> first; decompress_img works on bare names.
    _cleanup_super_ab(stage_dir)
    for image in sorted(Path(stage_dir).glob('*.img')):
        if image.stat().st_size == 0:
            continue
        print(f'\n{BOLD}> 分解 {image.name} -> {image.stem}{CLOSE}')
        decompress_img(str(image), workspace_partition(image.stem))


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------
# Selective super extraction menu entry point.
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

    # Expand a sparse super.img before anything reads it, exactly as menu 4
    # does: the raw image replaces the sparse file in INPUT, so no later stage
    # has to know about sparse at all and nothing is left in WORKSPACE.
    try:
        super_path = expand_sparse_in_place(super_path)
    except (OSError, ValueError) as error:
        print(f'Process log: {error}')
        print('\x1b[1;31mFailed !\x1b[0m')
        return

    os.system("clear")
    print(f'\n{BOLD}> 正在读取 super 元数据...{CLOSE}')
    partitions, effective_path = _list_partitions(super_path)

    if not partitions:
        print(f'{RED}> super.img 内未发现分区或解析失败{CLOSE}')
        input('> 任意键继续')
        return

    selected = _show_partitions(partitions)
    if not selected:
        return

    # Stage into WORKSPACE first: extraction and decomposition need the same
    # scratch space, and only what survives normalization reaches OUT.
    stage_dir = os.path.join(V.workspace, 'super') + os.sep
    shutil.rmtree(stage_dir, ignore_errors=True)
    extracted = _extract_selected(effective_path, stage_dir, selected)
    if not extracted:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return

    print(f'{BOLD}> 已提取的镜像：{CLOSE}')
    for image, partition in extracted:
        print(f'  {os.path.basename(image)}  ->  {partition}  '
              f'({human_size(os.path.getsize(image))})')
    print()
    print(f'  镜像暂存于 {stage_dir}')

    if input('> 是否继续分解img [0/1]: ').strip() == '1':
        _decompose_extracted(stage_dir)
        shutil.rmtree(stage_dir, ignore_errors=True)
    else:
        # Normalize A/B names, then hand the plain images to OUT.
        _cleanup_super_ab(stage_dir)
        _move_super_images_to_out(stage_dir, out_dir)
        shutil.rmtree(stage_dir, ignore_errors=True)



def main():
    super_selective_main()

if __name__ == '__main__':
    main()
