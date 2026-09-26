"""Super image extraction, logical partitions, and selective extraction."""

import os
import shutil
from pathlib import Path

from Scripts.Primary.Utils import V
from Scripts.Primary.Console import display
from Scripts.Primary.WorkSpace import workspace_partition
from Scripts.Primary.SuperTools import LpUnpack, LpUnpackError, _SparseRawCache, unpack


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
    from Scripts.Extract.image import decompress_img

    display(f'正在分解: {os.path.basename(working_source)} <super>', 3)
    super_dir = os.path.join(V.workspace, 'super') + os.sep
    try:
        unpack(working_source, super_dir, temp_dir=super_dir)
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
# Core: read super metadata and return [(name, group, size_bytes), ...].
# ---------------------------------------------------------------------------
# Read metadata for the selective extraction screen.
def _list_partitions(super_img_path):
    """Parse super metadata and return (sorted_partition_info, effective_img_path)."""
    job = LpUnpack(SUPER_IMAGE=super_img_path, SHOW_INFO=False, TEMP_DIR=V.workspace)
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
# Core: extract the selected partitions.
# ---------------------------------------------------------------------------
# Extract selected logical partitions.
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
        # Use the embedded LP unpacker with NAME filtering, SHOW_INFO=False, and OUTPUT_DIR.
        os.makedirs(out_dir, exist_ok=True)
        job = LpUnpack(
            SUPER_IMAGE=super_img_path,
            OUTPUT_DIR=out_dir,
            NAME=names,
            SHOW_INFO=False,
            TEMP_DIR=V.workspace,
        )
        job.unpack()
        print(f'\n{GREEN}> 提取完成！文件已输出到 {out_dir}{CLOSE}\n')
    except (LpUnpackError, OSError, ValueError) as e:
        print(f'{RED}> 提取失败: {e}{CLOSE}')


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

    # Clean up any shared sparse→raw temp files created during this session.
    _SparseRawCache.cleanup_all()



def main():
    super_selective_main()

if __name__ == '__main__':
    main()
