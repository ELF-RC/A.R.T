"""EROFS image repacker."""

import os
import shutil
import time

from Scripts.Primary.Utils import (
    CLOSE,
    GREEN,
    RED,
    V,
    call,
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


# Prepare sizes, timestamps, metadata, and output paths.
def _prepare(source, fsconfig, contexts, dumpinfo):
    # label (mount point, may be '/' for system-as-root) drives mkfs.erofs
    # --mount-point; partition (dir name) is used for output filenames.
    info_label, _info_size = load_image_json(dumpinfo, source) if dumpinfo else ('', 0)
    partition = os.path.basename(source)
    label = info_label or partition
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
    # Normalize the on-disk contexts to pure ASCII (\xNN byte escapes).
    # e2fsdroid requires this (libselinux rejects raw non-ASCII); mkfs.erofs
    # accepts both, so both packers share the single normalized file.
    translate_contexts_to_ascii(contexts, contexts)

    timestamp = (
        int(time.time())
        if V.SETUP_MANIFEST["UTC"].lower() == "live"
        else V.SETUP_MANIFEST["UTC"]
    )
    # mkfs.erofs derives the output size from the content + compression; the
    # info.json size is the original compressed footprint and isn't a target.
    size = get_dir_size(source, 1.3)
    if int(size) <= 1048576:
        size = 1048576

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
    }


# Build EROFS and optionally convert to sparse/DAT.
def _write_image(state, fsconfig, contexts, source, flag):
    label = state["label"]
    distance = state["distance"]
    new_distance = state["new_distance"]
    level = V.SETUP_MANIFEST.get("EROFS_LEVEL", "1")
    erofs_format = (
        "lz4hc" if V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1" else "lz4"
    )
    erofs_compress = (
        f"{erofs_format},{level}" if erofs_format != "lz4" else erofs_format
    )
    mkerofs_cmd = ["mkfs.erofs"]
    if V.SETUP_MANIFEST.get("EROFS_OLD_KERNEL", "0") == "1":
        mkerofs_cmd.extend(["-E", "legacy-compress"])
    mkerofs_cmd.extend(
        [
            f"-z{erofs_compress}",
            "-T",
            str(state["timestamp"]),
            f"--mount-point={label}",
            f"--product-out={V.workspace}",
            f"--fs-config-file={fsconfig}",
            f"--file-contexts={contexts}",
            new_distance,
            source,
        ]
    )

    print('Process remaking the file system...', end='', flush=True)
    result = call(mkerofs_cmd, capture=True)
    if result != 0:
        try:
            os.remove(new_distance)
        except OSError:
            pass
        print(f'\n{RED}Failed !{CLOSE}')
        print(f'Process log: {result}')
        return False
    print(f'\n{GREEN}Success !{CLOSE}')
    if not os.path.isfile(new_distance):
        return False

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


# Public EROFS repack entry point.
def recompress_erofs(source, fsconfig, contexts, dumpinfo, flag=8):
    """Recompress a partition directory into an EROFS image or DAT package."""
    state = _prepare(source, fsconfig, contexts, dumpinfo)
    sparse = "YES" if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" else "NO"
    level = V.SETUP_MANIFEST.get("EROFS_LEVEL", "1")
    method = "lz4hc" if V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1" else "lz4"
    old_kernel = "YES" if V.SETUP_MANIFEST.get("EROFS_OLD_KERNEL", "0") == "1" else "NO"
    print(
        f"EROFS: Label:{state['partition']} Size:{state['size']} "
        f"Sparse:{sparse} Level:{level} Method:{method} OLD:{old_kernel}"
    )
    if _write_image(state, fsconfig, contexts, source, flag):
        if _update_dynamic_partitions(state["partition"], state["distance"]) and flag > 9:
            recompress_dat_br(state["partition"], state["distance"], flag)
