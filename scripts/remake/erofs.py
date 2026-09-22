"""EROFS image repack — 合成 EROFS 分区镜像。"""

import os
import shutil
import time

from scripts.primary.utils import (
    CLOSE,
    RED,
    V,
    call,
    display,
    get_dir_size,
    patch_fsconfig,
)
from scripts.primary.workspace import load_image_json
from scripts.remake.dat_br import recompress_dat_br


def walk_contexts(path):
    """Deduplicate a generated fs config or SELinux contexts file."""
    with open(path, "r", encoding="UTF-8") as source:
        lines = list(set(source.readlines()))
    if os.path.isfile(path):
        os.remove(path)
    with open(path, "a+", encoding="UTF-8") as target:
        target.writelines(lines)


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
    if dumpinfo:
        _fsize, dsize, _inodes, _block_size, _blocks, _per_group, _mount_point = (
            load_image_json(dumpinfo, source)
        )
        size = dsize
    else:
        size = get_dir_size(source, 1.3)
        if int(size) <= 1048576:
            size = 1048576

    new_distance = os.path.join(V.out, f"{label}_new.img")
    if os.path.isfile(new_distance):
        os.remove(new_distance)
    return {
        "label": label,
        "distance": distance,
        "new_distance": new_distance,
        "timestamp": timestamp,
        "size": size,
    }


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
            f"--mount-point=/{label}",
            f"--product-out={V.workspace}",
            f"--fs-config-file={fsconfig}",
            f"--file-contexts={contexts}",
            new_distance,
            source,
        ]
    )

    if call(mkerofs_cmd) != 0:
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


def recompress_erofs(source, fsconfig, contexts, dumpinfo, flag=8):
    """Recompress a partition directory into an EROFS image or DAT package."""
    state = _prepare(source, fsconfig, contexts, dumpinfo)
    printinform = (
        f"Size:{state['size']}|FsT:erofs|FsR:ro|"
        f"Sparse:{V.SETUP_MANIFEST['REPACK_SPARSE_IMG']}"
    )
    if V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1":
        printinform += "|lz4hc"
    elif V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "2":
        printinform += "|lz4"
    display(printinform)
    display(f"重新合成: {state['label']}.img ...", 4)
    if _write_image(state, fsconfig, contexts, source, flag):
        if _update_dynamic_partitions(state["label"], state["distance"]) and flag > 9:
            recompress_dat_br(state["label"], state["distance"], flag)
