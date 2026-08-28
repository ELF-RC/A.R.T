"""Payload.bin extraction — 解包 payload.bin 卡刷包。"""

import os
import shutil
from glob import glob

from pys.utils import V, RED, GREEN, YELLOW, MAGENTA, CLOSE, _human_size, display
from pys import dumper as extract_payload
from pys.utils import gettype, findfile
from pys.workspace import LayoutError
from pys.workspace import partition_name, workspace_partition, _stage_work_source


def _decompress_payload_images(payload, payload_dir, mode):
    """Extract payload partitions based on user selection."""
    from pys.unpack_dispatch import decompress_img

    payload_partitions = extract_payload.info(payload)
    if mode == '1':
        print(f"> {YELLOW}包含的所有镜像文件: {CLOSE}\n")
        name_w = max(len(name) for name, _ in payload_partitions) + 4
        size_w = 10
        cols = 2
        for i, (name, size) in enumerate(payload_partitions):
            print(f"  {GREEN}{name}{CLOSE}".ljust(name_w + len(GREEN) + len(CLOSE)),
                  f"{_human_size(size):>{size_w}}", end='')
            if (i + 1) % cols == 0:
                print()
        print()
        name_set = {name for name, _ in payload_partitions}
        partitions = input(
            f"> {RED}根据以上信息输入一个或多个镜像，以空格分开{CLOSE}\n> {MAGENTA}").split()
        for partition in partitions:
            if partition in name_set:
                extract_payload.run(payload, payload_dir, partition)
    else:
        print(f"> {YELLOW}提取【{os.path.basename(payload)}】所有镜像文件:{CLOSE}\n")
        extract_payload.main(payload, payload_dir)

    images = sorted(glob(os.path.join(payload_dir, '*.img')))
    if input('> 是否继续分解img [0/1]: ') != '1':
        os.makedirs(V.out, exist_ok=True)
        for image in images:
            dest = os.path.join(V.out, os.path.basename(image))
            if os.path.isfile(dest):
                os.remove(dest)
            shutil.move(image, dest)
            print(f'> {os.path.basename(image)} -> {V.out}')
        return
    for image in images:
        decompress_img(image, workspace_partition(partition_name(image)))
    for image in images:
        if os.path.isfile(image):
            os.remove(image)


def decompress_bin(infile, outdir=None, flag='1'):
    """Extract payload images directly to WORKSPACE."""
    del outdir
    os.system("clear")
    try:
        payload = _stage_work_source(infile, 'payload')
        _decompress_payload_images(payload, V.workspace, flag)
    except (LayoutError, OSError, AssertionError) as error:
        print(f'> Payload 分解失败: {error}')
        input('> 任意键继续')
