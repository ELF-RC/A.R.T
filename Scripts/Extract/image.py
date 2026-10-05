"""Image extraction entry point and dispatcher — routes to format-specific extractors.

Format-specific logic lives in:
- Scripts.Extract.payload : payload.bin
- Scripts.Extract.dat_br     : new.dat / new.dat.br
- Scripts.Extract.ext4    : EXT4 / sparse images
- Scripts.Extract.erofs   : EROFS images
- Scripts.Extract.super   : super.img
- Scripts.Extract.boot    : boot / vendor_boot images
- Scripts.Extract.win     : .win archives
"""

import os

from glob import glob

from Scripts.Primary.Utils import V
from Scripts.Primary.ImageTools import get_file_type
from Scripts.Primary.WorkSpace import LayoutError
from Scripts.Primary.WorkSpace import (
    partition_name, workspace_partition, workspace_temp,
    _destination_partition, _stage_work_source, envelop_project,
    purge_partition,
)


# Single-image format dispatcher.
def decompress_img(source, distance=None, keep=1):
    """Extract one image directly into WORKSPACE/<partition>/.

    Dispatches to the appropriate format-specific extractor based on
    the image type detected by get_file_type.
    """
    del keep
    source_type = get_file_type(source)
    if source_type not in ('boot', 'vendor_boot', 'sparse', 'ext', 'erofs', 'super'):
        print(f'> 不支持的镜像类型: {source_type}')
        return

    try:
        working_source = _stage_work_source(source, 'image')
        partition = _destination_partition(distance, working_source)
    except (LayoutError, OSError) as error:
        print(f'> 无法准备镜像: {error}')
        return

    # Drop any prior extraction of this partition so re-extracting starts
    # clean: WORKSPACE/<partition>/, its fs_config/contexts/special, and the
    # info.json entry are all removed before the extractor runs.
    purge_partition(partition, V.config, V.layout)

    destination = workspace_partition(partition)
    file_type = get_file_type(working_source)
    committed = False

    if file_type in ('boot', 'vendor_boot'):
        from Scripts.Extract.boot import boot_unpack
        from Scripts.Primary.WorkSpace import create_partition_stage, _commit_extracted_partition, record_global_info
        try:
            _, staged_partition, staged_config = create_partition_stage(partition, 'boot-extract')
            if not boot_unpack(working_source, str(staged_partition)):
                raise LayoutError(f'{partition} boot 解包失败')
            if not os.path.isfile(os.path.join(staged_partition, 'boot_o.img')):
                raise LayoutError(f'{partition} boot 解包未生成 boot_o.img')
            record_global_info(staged_config, partition, {'type': 'kernel'})
            committed = _commit_extracted_partition(
                partition, staged_partition, {'info.json'})
        except (LayoutError, OSError) as error:
            print(f'> {partition} boot 分解失败: {error}')

    elif file_type == 'sparse':
        from Scripts.Primary.ImageTools import sparse_to_raw
        raw_source = os.path.join(V.workspace, f'.{partition}.unsparse.img')
        try:
            sparse_to_raw(working_source, raw_source, temp_dir=V.workspace)
            decompress_img(raw_source, destination)
        except (OSError, ValueError) as error:
            print(f'> Sparse 转换失败: {error}')
        finally:
            if os.path.isfile(raw_source):
                os.remove(raw_source)
        return

    elif file_type == 'ext':
        from Scripts.Extract.ext4 import extract_ext4
        committed = extract_ext4(working_source, partition, destination)

    elif file_type == 'erofs':
        from Scripts.Extract.erofs import extract_erofs
        committed = extract_erofs(working_source, partition, destination)

    elif file_type == 'super':
        from Scripts.Extract.super import extract_super
        extract_super(working_source, partition)
        return

    if committed:
        print('\x1b[1;32mSuccess !\x1b[0m')
    elif file_type not in ('boot', 'vendor_boot'):
        print('\x1b[1;31mFailed !\x1b[0m')


# Batch dispatcher for DAT.BR, DAT, and IMG menu options.
def decompress(infile, flag=4):
    """Batch extraction entry point for dat.br / dat / img files."""
    if flag in (2, 3):
        from Scripts.Extract.dat_br import decompress_dat_batch
        decompress_dat_batch(infile, flag)
        return

    # flag 4 (img)
    valid_imgs = [part for part in sorted(infile) if os.path.isfile(part)]

    # Single image: show a dedicated confirmation screen.
    if len(valid_imgs) == 1:
        only = valid_imgs[0]
        partition = partition_name(only)
        f_type = get_file_type(only)
        print('─' * 40)
        print(f' 分解: {os.path.basename(only)}')
        print('─' * 40)
        print(f'\n类型: {f_type}')
        print(f'输出: WORKSPACE/{partition}')
        if input('\n是否继续? [Y/n] ').strip().lower() in ('n', 'no'):
            return True
        try:
            decompress_img(only, workspace_partition(partition))
        except LayoutError as error:
            print(f'> 跳过 {os.path.basename(only)}: {error}')
        return

    # Multiple images: display a table, let the user pick by name or index.
    type_labels = {'ext': 'ext4', 'sparse': 'sparse'}
    print(f'发现 {len(valid_imgs)} 个镜像文件:\n')
    print(f'  {"序号":<6}{"文件名":<20}{"文件类型":<10}')
    print('  ' + '─' * 40)
    for idx, part in enumerate(valid_imgs, 1):
        ftype = type_labels.get(get_file_type(part), get_file_type(part))
        print(f'    {idx:<6}{os.path.basename(part):<24}{ftype:<10}')
    print()
    choice = input('请输入需要分解的 文件名/序号: ').strip()
    print()
    if not choice:
        return
    selected, seen = [], set()
    for token in choice.split():
        if token.isdigit():
            idx = int(token)
            if not 1 <= idx <= len(valid_imgs):
                continue
            target = valid_imgs[idx - 1]
        else:
            target = next((part for part in valid_imgs
                          if os.path.basename(part) == token), None)
        if target is not None and target not in seen:
            seen.add(target)
            selected.append(target)
    for part in selected:
        try:
            decompress_img(part, workspace_partition(partition_name(part)))
        except LayoutError as error:
            print(f'> 跳过 {os.path.basename(part)}: {error}')


# ROM ZIP import and plugin installation workflow.
def extract_zrom(rom):
    """Extract a ROM zip or install a plugin."""
    import zipfile
    import shutil
    from Scripts.Primary.Utils import rmdire, safe_extract_zip, change_permissions_recursive

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
                print(f'是否安装插件: {mod_name} ? [1/0]: ', end='')
            else:
                print(f'已安装插件: {mod_name}，是否删除原插件后安装 ? [0/1]: ', end='')
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
        except (LayoutError, OSError, ValueError) as error:
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
        from Scripts.Extract.payload import decompress_bin
        decompress_bin(payload_files[0], flag='1')
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
