"""Super image extraction — 解包 super.img 动态分区。"""

import os
import shutil
from pathlib import Path

from pys.utils import V, display
from pys import lpunpack
from pys.workspace import LayoutError
from pys.workspace import (
    workspace_partition, _super_images_to_process,
)


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
    from pys.unpack_dispatch import decompress_img

    display(f'正在分解: {os.path.basename(working_source)} <super>', 3)
    super_dir = os.path.join(V.workspace, 'super') + os.sep
    try:
        lpunpack.unpack(working_source, super_dir)
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
