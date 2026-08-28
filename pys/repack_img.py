"""Image repack — 打包 EXT4/EROFS 分区镜像。"""

import os
import time
from pathlib import Path

from pys.utils import (
    V, PWD_DIR, RED, GREEN, YELLOW, CLOSE, display, call, get_dir_size, ceil,
    CoastTime,
)
from pys import fspatch
from pys import img2sdat
from pys.workspace import LayoutError
from pys.workspace import load_image_json


def walk_contexts(contexts):
    with open(contexts, "r", encoding="utf-8") as f3:
        text_list = list(set(f3.readlines()))
    if os.path.isfile(contexts):
        os.remove(contexts)
    with open(contexts, "a+", encoding="utf-8") as f:
        f.writelines(text_list)


def recompress(source, fsconfig, contexts, dumpinfo, flag=8):
    """Recompress a partition directory into an image (EXT4 or EROFS based on config).

    flag: 8=img, 9=img(with boot), 10=new.dat, 11=new.dat.br
    """
    label = os.path.basename(source)
    if not os.path.isdir(V.out):
        os.makedirs(V.out)
    distance = V.out + label + ".img"
    if os.path.isfile(distance):
        os.remove(distance)
    fspatch.main(source, fsconfig)
    walk_contexts(fsconfig)
    walk_contexts(contexts)
    timestamp = int(time.time()) if V.SETUP_MANIFEST["UTC"].lower() == "live" else V.SETUP_MANIFEST["UTC"]
    read = "ro"
    fsize = None
    if dumpinfo:
        (fsize, dsize, inodes, block_size, blocks, per_group, mount_point) = load_image_json(dumpinfo, source)
        size = dsize
    else:
        size = get_dir_size(source, 1.3)
        if int(size) <= 1048576:
            size = 1048576
        mount_point = "/" + label
        if os.path.isfile(source + os.sep + "system" + os.sep + "build.prop"):
            mount_point = "/"
    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "0":
        fs_variant = "ext4"
        block_size = 4096
        blocks = ceil(int(size) / int(block_size))
        if not fsize:
            read = "rw"
        new_distance = V.out + label + "_new.img"
        if os.path.isfile(new_distance):
            os.remove(new_distance)
        mke2fs_a_cmd = [
            'mke2fs', '-O', '^has_journal,^metadata_csum,extent,huge_file,^flex_bg,^64bit,uninit_bg,dir_nlink,extra_isize',
            '-L', label, '-I', '256', '-M', mount_point, '-m', '0', '-t', 'ext4', '-b', str(block_size),
            new_distance, str(blocks),
        ]
        e2fsdroid_a_cmd = [
            'e2fsdroid', '-e', '-T', str(timestamp), '-S', contexts, '-C', fsconfig,
            '-a', f'/{label}', '-f', source, new_distance,
        ]
    else:
        fs_variant = "erofs"
        erofs_level = V.SETUP_MANIFEST.get("EROFS_LEVEL", "1")
        erofs_format = "lz4hc" if V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1" else "lz4"
        erofs_compress = f'{erofs_format},{erofs_level}' if erofs_format != 'lz4' else erofs_format
        mkerofs_cmd = ['mkfs.erofs']
        if V.SETUP_MANIFEST.get("EROFS_OLD_KERNEL", "0") == "1":
            mkerofs_cmd.extend(['-E', 'legacy-compress'])
        new_distance = V.out + label + "_new.img"
        if os.path.isfile(new_distance):
            os.remove(new_distance)
        mkerofs_cmd.extend([
            f'-z{erofs_compress}',
            '-T', str(timestamp),
            f'--mount-point=/{label}',
            f'--product-out={V.workspace}',
            f'--fs-config-file={fsconfig}',
            f'--file-contexts={contexts}',
            new_distance,
            source,
        ])
    printinform = f"Size:{size}|FsT:{fs_variant}|FsR:{read}|Sparse:{V.SETUP_MANIFEST['REPACK_SPARSE_IMG']}"
    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "0":
        if V.SETUP_MANIFEST["RESIZE_IMG"] == "1" and V.SETUP_MANIFEST["REPACK_TO_RW"] == "1":
            printinform += "|Resize:1"
        else:
            printinform += "|Resize:0"
    elif V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1":
        printinform += "|lz4hc"
    elif V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "2":
        printinform += "|lz4"
    display(printinform)
    display(f"重新合成: {label}.img ...", 4)

    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "1":
        if call(mkerofs_cmd) != 0:
            try:
                os.remove(new_distance)
            except:
                pass
        if os.path.isfile(new_distance):
            print(" Done")
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 9:
                display("开始转换: sparse format ...")
                call(['img2simg', new_distance, distance])
                try:
                    os.remove(new_distance)
                except:
                    pass
            else:
                if os.path.isfile(distance):
                    os.remove(distance)
                os.rename(new_distance, distance)
    else:
        call(mke2fs_a_cmd)
        if os.path.isfile(new_distance):
            if call(e2fsdroid_a_cmd) != 0:
                try:
                    os.remove(new_distance)
                except:
                    pass
        if os.path.isfile(new_distance):
            print(" Done")
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 9:
                display("开始转换: sparse format ...")
                call(['img2simg', new_distance, distance])
                try:
                    os.remove(new_distance)
                except:
                    pass
            else:
                if os.path.isfile(distance):
                    os.remove(distance)
                os.rename(new_distance, distance)
    if os.path.isfile(distance):
        op_list = V.input + "dynamic_partitions_op_list"
        new_op_list = V.out + "dynamic_partitions_op_list"
        if os.path.isfile(op_list) or os.path.isfile(new_op_list):
            if not os.path.isfile(new_op_list):
                import shutil
                shutil.copyfile(op_list, new_op_list)
        else:
            CONTENT = "remove_all_groups\n"
            for slot in ('_a', '_b'):
                CONTENT += f"add_group qti_dynamic_partitions{slot} {V.SETUP_MANIFEST['SUPER_SIZE']}\n"
            for partition in ('system', 'system_ext', 'product', 'vendor', 'odm'):
                for slot in ('_a', '_b'):
                    CONTENT += f"add {partition}{slot} qti_dynamic_partitions{slot}\n"
            for partition in ('system_a', 'system_ext_a', 'product_a', 'vendor_a', 'odm_a'):
                CONTENT += f"resize {partition} 2\n"
            with open(new_op_list, "w", encoding="UTF-8", newline="\n") as ST:
                ST.write(CONTENT)
        renew_size = os.path.getsize(distance)
        with open(new_op_list, "r", encoding="UTF-8") as f_r:
            data = f_r.readlines()
            with open(new_op_list, "w", encoding="UTF-8") as f_w:
                for line in data:
                    if f"resize {label} " in line:
                        line = f"resize {label} {renew_size}\n"
                    elif f"resize {label}_a " in line:
                        line = f"resize {label}_a {renew_size}\n"
                    f_w.write(line)
        if flag > 9:
            display(f"重新生成: {label}.new.dat ...", 3)
            img2sdat.main(distance, V.out, 4, label)
            newdat = V.out + label + ".new.dat"
            if os.path.isfile(newdat):
                print(" Done")
                os.remove(distance)
                if flag == 11:
                    level = V.SETUP_MANIFEST["REPACK_BR_LEVEL"]
                    display(f"重新生成: {label}.new.dat.br | Level={level} ...", 3)
                    newdat_brotli = newdat + ".br"
                    call(['brotli', f'-{level}jfo', newdat_brotli, newdat])
                    print(f" {GREEN}打包成功{CLOSE}" if os.path.isfile(newdat_brotli) else f" {RED}打包失败{CLOSE}")
            else:
                print(f" {RED}打包失败{CLOSE}")
    else:
        print(f" {RED}打包失败{CLOSE}")
