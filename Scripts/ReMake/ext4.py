"""EXT4 image repacker."""

import os
import shutil
import time

from Scripts.Primary.Utils import (
    CLOSE,
    GREEN,
    RED,
    V,
    call,
    ceil,
    get_dir_size,
)
from Scripts.Primary.FileConfigPatcher import patch_fsconfig, patch_file_contexts, translate_contexts_to_ascii
from Scripts.Primary.WorkSpace import load_image_json
from Scripts.ReMake.dat_br import recompress_dat_br


# Metadata normalization, image construction, and DAT hand-off.
def walk_contexts(path):
    """Deduplicate a generated fs config or SELinux contexts file."""
    with open(path, "r", encoding="UTF-8") as source:
        lines = list(set(source.readlines()))
    if os.path.isfile(path):
        os.remove(path)
    with open(path, "a+", encoding="UTF-8") as target:
        target.writelines(lines)


# Compute an ext4 image size that fits the source tree plus metadata.
def _ext4_image_size(source, block_size=4096):
    """Size an ext4 image to fit the source tree plus real ext4 metadata.

    Models the on-disk layout the mke2fs flags below produce (^has_journal,
    ^metadata_csum, ^flex_bg, ^64bit -> 32-byte group descriptors, 256-B
    inodes). For each block group: a block bitmap, an inode bitmap, and an
    inode table whose width follows from inodes_per_group. Group 0 also
    carries the superblock and the group-descriptor table (plus a small
    resize_inode reservation). The block count determines the group count,
    which determines the per-group metadata, which feeds back into the block
    count — so it iterates to convergence (1-2 passes for typical sizes).

    No fixed floor: the lower bound is whatever the metadata itself needs,
    so a 315 KB tree is no longer padded to 1 MB.
    """
    # Layout constants matching _write_image's mke2fs flags.
    blocks_per_group = block_size * 8           # 32768 at 4K blocks
    inode_size = 256                             # -I 256
    desc_size = 32                               # ^64bit -> 32-B descriptors
    reserved_gdt = 2                             # resize_inode reservation (conservative)

    # Walk the source tree once.
    data_blocks = 0
    dir_count = 0
    entry_count = 1  # partition root
    for _root, dirs, files in os.walk(source):
        dir_count += len(dirs)
        entry_count += len(dirs) + len(files)
        for name in files:
            path = os.path.join(_root, name)
            if not os.path.islink(path):
                try:
                    data_blocks += ceil(os.path.getsize(path) / block_size)
                except OSError:
                    pass

    # Each directory occupies at least one block of directory entries.
    dir_blocks = dir_count
    # One inode per entry plus the configurable margin (default 64) for
    # lost+found and post-extract edits; matches the -N value _write_image
    # passes to mke2fs so the inode-table block estimate stays accurate.
    margin = int(V.SETUP_MANIFEST.get("INODE_MARGIN", "64"))
    inode_count = entry_count + margin
    base_blocks = data_blocks + dir_blocks

    # Iterate: total blocks -> group count -> per-group metadata -> total.
    total = base_blocks
    for _ in range(8):
        groups = max(1, ceil(total / blocks_per_group))
        inodes_per_group = ceil(inode_count / groups)
        inode_table_per_group = ceil(inodes_per_group * inode_size / block_size)
        per_group_meta = 2 + inode_table_per_group   # block bitmap + inode bitmap + inode table
        gdt_blocks = ceil(groups * desc_size / block_size)
        # Group 0: superblock + GDT (+ reservation) + its own bitmaps/inode table.
        # Sparse-super backups in groups 1/3^n/5^n/7^n add at most a few blocks,
        # absorbed by the margin below.
        total = base_blocks + groups * per_group_meta + 1 + gdt_blocks + reserved_gdt

    # 3% margin for extent trees, large directories, sparse-super backups,
    # and minor post-extract edits; +16 blocks for lost+found and rounding.
    total = ceil(total * 1.03) + 16
    return total * block_size


# Prepare sizes, timestamps, metadata, and output paths.
def _prepare(source, fsconfig, contexts, dumpinfo):
    # label (mount point, may be '/' for system-as-root) and partition (dir
    # name, used for filenames) come from info.json; the AOSP build passes the
    # same value to mke2fs -L and -M, so callers use label raw, not prefixed.
    info_label, info_size = load_image_json(dumpinfo, source) if dumpinfo else ('', 0)
    partition = os.path.basename(source)
    label = info_label if info_label and info_label != '/' else partition
    os.makedirs(V.out, exist_ok=True)
    distance = os.path.join(V.out, f"{partition}.img")
    if os.path.isfile(distance):
        os.remove(distance)

    if V.SETUP_MANIFEST.get("PATCH_FSCONFIG", "1") == "1":
        patch_fsconfig(source, fsconfig)
    if V.SETUP_MANIFEST.get("PATCH_CONTEXTS", "1") == "1":
        patch_file_contexts(source, contexts)
    walk_contexts(fsconfig)
    walk_contexts(contexts)
    # mke2fs auto-creates a lost+found directory that no source-tree walk
    # covers; e2fsdroid needs a contexts rule for it or it aborts with
    # "No such file or directory searching for label". The path is a regex,
    # so "+" is escaped; the prefix uses the partition name (contexts paths
    # are SELinux paths, not mount points).
    if os.path.isfile(contexts):
        existing = open(contexts, 'r', encoding='utf-8').read()
        if 'lost+found' not in existing and 'lost\\+found' not in existing:
            with open(contexts, 'a', encoding='utf-8', newline='\n') as f:
                f.write(f'/{partition}/lost\\+found u:object_r:system_file:s0\n')
    # e2fsdroid's libselinux rejects raw non-ASCII in contexts ("Non-ASCII
    # characters found"); mkfs.erofs accepts both forms. Normalize the
    # on-disk file to pure ASCII (\xNN byte escapes) in place so both
    # packers share one file and no separate copy is needed.
    translate_contexts_to_ascii(contexts, contexts)

    timestamp = (
        int(time.time())
        if V.SETUP_MANIFEST["UTC"].lower() == "live"
        else V.SETUP_MANIFEST["UTC"]
    )
    # IMAGE_SIZE=1: repack at the original footprint from info.json. If it
    # doesn't fit the content, mke2fs/e2fsdroid surfaces the error to the
    # user — no silent floor or padding here.
    # IMAGE_SIZE=0: size from the live source tree + ext4 metadata.
    if V.SETUP_MANIFEST["IMAGE_SIZE"] == "1" and info_size:
        size = info_size
    else:
        size = _ext4_image_size(source)

    block_size = 4096
    blocks = ceil(int(size) / block_size)
    read_mode = "ro" if V.SETUP_MANIFEST["REPACK_TO_RW"] == "0" else "rw"
    new_distance = os.path.join(V.out, f"{partition}_new.img")
    if os.path.isfile(new_distance):
        os.remove(new_distance)
    return {
        "label": label,
        "partition": partition,
        "distance": distance,
        "new_distance": new_distance,
        "timestamp": timestamp,
        "size": size,
        "read_mode": read_mode,
        "blocks": blocks,
    }


# Build EXT4 and optionally convert to sparse/DAT.
def _write_image(state, fsconfig, contexts, source, flag):
    label = state["label"]
    distance = state["distance"]
    new_distance = state["new_distance"]
    # Count inodes the new filesystem must hold: every file, directory, and
    # symlink under the source tree occupies one inode, plus the partition
    # root itself (os.walk never lists it as a member of dirs/files, so it
    # would be missed without the +1). Symlinks appear in dirs or files and
    # are not followed, so each is counted exactly once. A small margin
    # absorbs lost+found and minor post-extract edits.
    inode_count = 1
    for _root, dirs, files in os.walk(source):
        inode_count += len(dirs) + len(files)
    inode_count += int(V.SETUP_MANIFEST.get("INODE_MARGIN", "64"))
    # AOSP passes mount_point to mke2fs -L (volume label) and -M (last
    # mounted directory, superblock s_last_mount) and to e2fsdroid -a
    # (Android mount point). For system-as-root the value is '/', for other
    # partitions the partition name; -M and -a need the full path, so a
    # leading '/' is added unless the value already starts with one.
    mount = label if label.startswith('/') else '/' + label
    mke2fs_cmd = [
        "mke2fs",
        "-N",
        str(inode_count),
        "-O",
        "^has_journal,^metadata_csum,extent,huge_file,^flex_bg,^64bit,uninit_bg,dir_nlink,extra_isize",
        "-L",
        label,
        "-I",
        "256",
        "-M",
        mount,
        "-m",
        "0",
        "-t",
        "ext4",
        "-b",
        "4096",
        new_distance,
        str(state["blocks"]),
    ]
    e2fsdroid_cmd = [
        "e2fsdroid",
        "-e",
        "-T",
        str(state["timestamp"]),
        "-S",
        contexts,
        "-C",
        fsconfig,
        "-a",
        mount,
        "-f",
        source,
    ]
    # REPACK_TO_RW=0 (read-only): e2fsdroid -s squashes permissions to the
    # fs_config values, matching DNA's ro packaging.
    if V.SETUP_MANIFEST["REPACK_TO_RW"] == "0":
        e2fsdroid_cmd.append("-s")
    e2fsdroid_cmd.append(new_distance)

    print('Process remaking the file system...', end='', flush=True)
    mkfs_log = call(mke2fs_cmd, capture=True)
    fs_created = os.path.isfile(new_distance)
    if isinstance(mkfs_log, str):
        try:
            os.remove(new_distance)
        except (OSError, FileNotFoundError):
            pass
        print(f'\n{RED}Failed !{CLOSE}')
        print(f'Process log: {mkfs_log}')
        return False
    if fs_created:
        e2fs_result = call(e2fsdroid_cmd, capture=True)
        if isinstance(e2fs_result, str):
            # e2fsdroid returned an error log: drop the half-packed image.
            try:
                os.remove(new_distance)
            except (OSError, FileNotFoundError):
                pass
            print(f'\n{RED}Failed !{CLOSE}')
            print(f'Process log: {e2fs_result}')
            return False
        elif e2fs_result != 0:
            try:
                os.remove(new_distance)
            except (OSError, FileNotFoundError):
                pass
            print(f'\n{RED}Failed !{CLOSE}')
            print(f'Process log: 退出码 {e2fs_result}')
            return False
        if not os.path.isfile(new_distance):
            print(f'\n{RED}Failed !{CLOSE}')
            print('Process log: e2fsdroid 未生成目标镜像')
            return False

    print(f'\n{GREEN}Success !{CLOSE}')
    # RESIZE_IMG=1: shrink the image to its minimum footprint (resize2fs -M),
    # matching DNA's "压缩EXT4镜像空间" behaviour. The image is already valid
    # at this point, so a resize2fs failure is warned, not fatal.
    if V.SETUP_MANIFEST["RESIZE_IMG"] == "1":
        result = call(["resize2fs", "-M", new_distance], capture=True)
        if isinstance(result, str):
            print(f'\n{YELLOW}resize2fs 警告:{CLOSE} {result}')
    if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" or flag > 9:
        print("开始转换: sparse format ...")
        if call(["img2simg", new_distance, distance]) != 0:
            return False
        try:
            os.remove(new_distance)
        except OSError:
            pass
    else:
        if os.path.isfile(distance):
            os.remove(distance)
        os.rename(new_distance, distance)
    return True


# Update dynamic-partition operation-list sizes.
def _update_dynamic_partitions(label, distance):
    if not os.path.isfile(distance):
        print(f" {RED}打包失败{CLOSE}")
        return

    op_list = os.path.join(V.input, "dynamic_partitions_op_list")
    new_op_list = os.path.join(V.out, "dynamic_partitions_op_list")
    if os.path.isfile(op_list) or os.path.isfile(new_op_list):
        if not os.path.isfile(new_op_list):
            shutil.copyfile(op_list, new_op_list)
    else:
        return True
    renew_size = os.path.getsize(distance)
    with open(new_op_list, "r", encoding="UTF-8") as source:
        lines = source.readlines()
    with open(new_op_list, "w", encoding="UTF-8") as target:
        for line in lines:
            if f"resize {label} " in line:
                line = f"resize {label} {renew_size}\n"
            elif f"resize {label}_a " in line:
                line = f"resize {label}_a {renew_size}\n"
            target.write(line)

    return True


# Public EXT4 repack entry point.
def recompress_ext4(source, fsconfig, contexts, dumpinfo, flag=8):
    """Recompress a partition directory into an EXT4 image or DAT package."""
    state = _prepare(source, fsconfig, contexts, dumpinfo)
    sparse = "YES" if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" else "NO"
    resize = "YES" if V.SETUP_MANIFEST["RESIZE_IMG"] == "1" else "NO"
    print(
        f"EXT4FS: Label:{state['partition']} Size:{state['size']} "
        f"Mode:{state['read_mode']} Sparse:{sparse} Resize:{resize}"
    )
    if _write_image(state, fsconfig, contexts, source, flag):
        if _update_dynamic_partitions(state["partition"], state["distance"]) and flag > 9:
            recompress_dat_br(state["partition"], state["distance"], flag)
