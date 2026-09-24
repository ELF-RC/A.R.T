"""EXT4 image repacker."""

import os
import shutil
import time

from Scripts.Primary.Utils import (
    CLOSE,
    RED,
    V,
    call,
    ceil,
    get_dir_size,
)
from Scripts.Primary.FileConfigPatcher import patch_fsconfig
from Scripts.Primary.Console import display
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


# Prepare sizes, timestamps, metadata, and output paths.
def _prepare(source, fsconfig, contexts, dumpinfo):
    label = os.path.basename(source)
    os.makedirs(V.out, exist_ok=True)
    distance = os.path.join(V.out, f"{label}.img")
    if os.path.isfile(distance):
        os.remove(distance)

    patch_fsconfig(source, fsconfig)
    walk_contexts(fsconfig)
    walk_contexts(contexts)

    timestamp = (
        int(time.time())
        if V.SETUP_MANIFEST["UTC"].lower() == "live"
        else V.SETUP_MANIFEST["UTC"]
    )
    fsize = None
    if dumpinfo:
        fsize, dsize, _inodes, _old_block_size, _old_blocks, _per_group, mount_point = (
            load_image_json(dumpinfo, source)
        )
        size = dsize
    else:
        size = get_dir_size(source, 1.3)
        if int(size) <= 1048576:
            size = 1048576
        mount_point = "/" if os.path.isfile(
            os.path.join(source, "system", "build.prop")
        ) else f"/{label}"

    block_size = 4096
    blocks = ceil(int(size) / block_size)
    read_mode = "ro" if fsize else "rw"
    new_distance = os.path.join(V.out, f"{label}_new.img")
    if os.path.isfile(new_distance):
        os.remove(new_distance)
    return {
        "label": label,
        "distance": distance,
        "new_distance": new_distance,
        "timestamp": timestamp,
        "size": size,
        "read_mode": read_mode,
        "mount_point": mount_point,
        "blocks": blocks,
    }


# Build EXT4 and optionally convert to sparse/DAT.
def _write_image(state, fsconfig, contexts, source, flag):
    label = state["label"]
    distance = state["distance"]
    new_distance = state["new_distance"]
    mke2fs_cmd = [
        "mke2fs",
        "-O",
        "^has_journal,^metadata_csum,extent,huge_file,^flex_bg,^64bit,uninit_bg,dir_nlink,extra_isize",
        "-L",
        label,
        "-I",
        "256",
        "-M",
        state["mount_point"],
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
        f"/{label}",
        "-f",
        source,
        new_distance,
    ]

    call(mke2fs_cmd)
    if os.path.isfile(new_distance) and call(e2fsdroid_cmd) != 0:
        try:
            os.remove(new_distance)
        except OSError:
            pass
    if not os.path.isfile(new_distance):
        return False

    print(" Done")
    if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" or flag > 9:
        display("开始转换: sparse format ...")
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
        content = "remove_all_groups\n"
        for slot in ("_a", "_b"):
            content += (
                f"add_group qti_dynamic_partitions{slot} "
                f"{V.SETUP_MANIFEST['SUPER_SIZE']}\n"
            )
        for partition in ("system", "system_ext", "product", "vendor", "odm"):
            for slot in ("_a", "_b"):
                content += f"add {partition}{slot} qti_dynamic_partitions{slot}\n"
        for partition in ("system_a", "system_ext_a", "product_a", "vendor_a", "odm_a"):
            content += f"resize {partition} 2\n"
        with open(new_op_list, "w", encoding="UTF-8", newline="\n") as target:
            target.write(content)

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
    resize = (
        1
        if V.SETUP_MANIFEST["RESIZE_IMG"] == "1"
        and V.SETUP_MANIFEST["REPACK_TO_RW"] == "1"
        else 0
    )
    display(
        f"Size:{state['size']}|FsT:ext4|FsR:{state['read_mode']}|"
        f"Sparse:{V.SETUP_MANIFEST['REPACK_SPARSE_IMG']}|Resize:{resize}"
    )
    display(f"重新合成: {state['label']}.img ...", 4)
    if _write_image(state, fsconfig, contexts, source, flag):
        if _update_dynamic_partitions(state["label"], state["distance"]) and flag > 9:
            recompress_dat_br(state["label"], state["distance"], flag)
