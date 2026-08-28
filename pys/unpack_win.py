"""Win archive extraction — 解包 .win 格式镜像包。"""

import os
import shutil
import tarfile
from pathlib import Path

from pys.utils import V, display, safe_extract_tar
from pys.utils import gettype, findfile
from pys.workspace import LayoutError, ProjectLayout
from pys.workspace import (
    workspace_partition, create_partition_stage,
    _commit_extracted_partition,
)


def _win_partition(source):
    name = os.path.basename(source)
    return ProjectLayout.validate_component(name.split('.', 1)[0], "分区")


def decompress_win(infile_list):
    """Extract .win archives (image or tar format) into WORKSPACE."""
    from pys.unpack_dispatch import decompress_img

    groups = {}
    for source in infile_list:
        if os.path.isfile(source):
            try:
                groups.setdefault(_win_partition(source), []).append(source)
            except LayoutError as error:
                print(f'> 跳过 {source}: {error}')

    for partition, fragments in groups.items():
        staged_win = os.path.join(V.workspace, f'{partition}.win')
        fragments.sort(key=lambda item: (not item.endswith('.win'), os.path.basename(item)))
        with open(staged_win, 'wb') as destination_file:
            for fragment in fragments:
                print(f'合并 {fragment} 到 {staged_win}')
                with open(fragment, 'rb') as source_file:
                    shutil.copyfileobj(source_file, destination_file)

        try:
            if gettype(staged_win) in ['erofs', 'ext', 'sparse', 'super', 'boot', 'vendor_boot']:
                decompress_img(staged_win, workspace_partition(partition))
            elif tarfile.is_tarfile(staged_win):
                _, staged_partition, _ = create_partition_stage(partition, 'tar-extract')
                with tarfile.open(staged_win, 'r') as archive:
                    safe_extract_tar(archive, staged_partition)
                if not _commit_extracted_partition(partition, staged_partition, set()):
                    continue
                print(f'> {partition} TAR 分解完成')
            else:
                input("未知格式")
        finally:
            if os.path.isfile(staged_win):
                os.remove(staged_win)
