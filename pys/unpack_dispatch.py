"""Image extraction dispatcher — routes to format-specific extractors.

Format-specific logic lives in:
- pys.unpack_payload : payload.bin
- pys.unpack_dat     : new.dat / new.dat.br
- pys.unpack_ext4    : EXT4 / sparse images
- pys.unpack_erofs   : EROFS images
- pys.unpack_super   : super.img
- pys.unpack_boot    : boot / vendor_boot images
- pys.unpack_win     : .win archives
"""

import os
import time
from glob import glob

from pys.utils import V, RED, GREEN, YELLOW, CLOSE, display
from pys.utils import gettype, findfile
from pys.workspace import LayoutError
from pys.workspace import (
    partition_name, workspace_partition, workspace_temp,
    _destination_partition, _stage_work_source, envelop_project,
)


def decompress_img(source, distance=None, keep=1):
    """Extract one image directly into WORKSPACE/<partition>/.

    Dispatches to the appropriate format-specific extractor based on
    the image type detected by gettype.
    """
    del keep
    source_type = gettype(source)
    if source_type not in ('boot', 'vendor_boot', 'sparse', 'ext', 'erofs', 'super'):
        print(f'> 不支持的镜像类型: {source_type}')
        return
    if os.path.basename(source) in ('dsp.img', 'exaid.img', 'cust.img'):
        return

    try:
        working_source = _stage_work_source(source, 'image')
        partition = _destination_partition(distance, working_source)
    except (LayoutError, OSError) as error:
        print(f'> 无法准备镜像: {error}')
        return

    destination = workspace_partition(partition)
    s_time = time.time()
    file_type = gettype(working_source)
    committed = False

    if file_type in ('boot', 'vendor_boot'):
        from pys.unpack_boot import boot_unpack
        from pys.workspace import create_partition_stage, metadata_path, _commit_extracted_partition
        try:
            _, staged_partition, staged_config = create_partition_stage(partition, 'boot-extract')
            if not boot_unpack(working_source, str(staged_partition)):
                raise LayoutError(f'{partition} boot 解包失败')
            if not (os.path.join(staged_partition, 'boot_o.img')):
                raise LayoutError(f'{partition} boot 解包未生成 boot_o.img')
            metadata_path(staged_config, partition, '_kernel.txt').touch()
            committed = _commit_extracted_partition(
                partition, staged_partition, {f'{partition}_kernel.txt'})
        except (LayoutError, OSError) as error:
            print(f'> {partition} boot 分解失败: {error}')

    elif file_type == 'sparse':
        from pys.unpack_ext4 import convert_sparse
        raw_source = convert_sparse(working_source)
        if raw_source:
            decompress_img(raw_source, destination)
        return

    elif file_type == 'ext':
        from pys.unpack_ext4 import extract_ext4
        committed = extract_ext4(working_source, partition, destination)

    elif file_type == 'erofs':
        from pys.unpack_erofs import extract_erofs
        committed = extract_erofs(working_source, partition, destination)

    elif file_type == 'super':
        from pys.unpack_super import extract_super
        extract_super(working_source, partition)
        return

    if committed:
        print('\x1b[1;32m %ds Done\x1b[0m' % (time.time() - s_time))
    elif file_type not in ('boot', 'vendor_boot'):
        from rich import print as echo
        echo('[red][Failed][/]')


def decompress(infile, flag=4):
    """Batch decompress dispatcher for dat.br / dat / img files."""
    if flag in (2, 3):
        from pys.unpack_dat import decompress_dat_batch
        decompress_dat_batch(infile, flag)
        return

    # flag 4 (img): sequential with per-file confirmation
    for part in sorted(infile):
        if not os.path.isfile(part):
            continue
        try:
            if os.path.basename(part) in ('dsp.img', 'cust.img'):
                continue
            if gettype(part) not in ('ext', 'sparse', 'erofs', 'super', 'boot', 'vendor_boot'):
                continue
            if not V.JM:
                display(f'是否分解: {os.path.basename(part)} [1/0]: ', 2, '')
                if input() != '1':
                    continue
            decompress_img(part, workspace_partition(partition_name(part)))
        except LayoutError as error:
            print(f'> 跳过 {os.path.basename(part)}: {error}')


def extract_zrom(rom):
    """Extract a ROM zip or install a plugin."""
    import zipfile
    import shutil
    from pys.utils import rmdire, safe_extract_zip, change_permissions_recursive

    MOD_DIR = os.getcwd() + os.sep + "local/sub/"

    if not zipfile.is_zipfile(rom):
        input('> 破损的zip或不支持的zip类型')
        return

    with zipfile.ZipFile(rom) as archive:
        zip_lists = archive.namelist()
        if 'run.sh' in zip_lists:
            if not os.path.isdir(MOD_DIR):
                os.makedirs(MOD_DIR)
            mod_name = os.path.basename(rom).rsplit('.', 1)[0].replace(' ', '_')
            sub_dir = MOD_DIR + 'DNA_' + mod_name
            if not os.path.isdir(sub_dir):
                display(f'是否安装插件: {mod_name} ? [1/0]: ', 2, '')
            else:
                display(f'已安装插件: {mod_name}，是否删除原插件后安装 ? [0/1]: ', 2, '')
            if input() == '1':
                rmdire(sub_dir)
                os.makedirs(sub_dir, exist_ok=True)
                try:
                    safe_extract_zip(archive, sub_dir)
                except LayoutError as error:
                    rmdire(sub_dir)
                    input(f'> 插件安装失败: {error}')
                    return
                if os.path.isfile(sub_dir + os.sep + 'run.sh'):
                    change_permissions_recursive(sub_dir, 0o777)
                    print('\x1b[1;31m\n 安装完成 !!!\x1b[0m')
                else:
                    rmdire(sub_dir)
                    print('\x1b[1;31m\n 安装失败 !!!\x1b[0m')
            return

        V.project = 'DNA_' + os.path.basename(rom).rsplit('.', 1)[0]
        try:
            envelop_project()
        except (LayoutError, Exception) as error:
            input(f'> 无法创建或打开工程: {error}')
            return

        import_dir = workspace_temp('import')
        print(f'> 解压缩: {os.path.basename(rom)} 到 WORKSPACE')
        try:
            safe_extract_zip(archive, import_dir)
        except LayoutError as error:
            input(f'> ROM ZIP 解压失败: {error}')
            return

    payload_files = sorted(glob(os.path.join(import_dir, '**', 'payload.bin'), recursive=True))
    if payload_files:
        from pys.unpack_payload import decompress_bin
        decompress_bin(
            payload_files[0],
            flag=input(f'> {RED}选择提取方式:  [0]全盘提取  [1]指定镜像{CLOSE} >> '),
        )
        shutil.rmtree(import_dir, ignore_errors=True)
        return

    dat_br_files = sorted(glob(os.path.join(import_dir, '**', '*.new.dat.br'), recursive=True))
    dat_files = sorted(glob(os.path.join(import_dir, '**', '*.new.dat'), recursive=True))
    img_files = sorted(glob(os.path.join(import_dir, '**', '*.img'), recursive=True))

    if dat_br_files:
        infile, able = dat_br_files, 2
    elif dat_files:
        infile, able = dat_files, 3
    elif img_files:
        infile, able = img_files, 4
    else:
        input('> 仅支持含有payload.bin/*.new.dat/*.new.dat.br/*.img的zip固件')
        shutil.rmtree(import_dir, ignore_errors=True)
        return

    decompress(infile, able)
    shutil.rmtree(import_dir, ignore_errors=True)
