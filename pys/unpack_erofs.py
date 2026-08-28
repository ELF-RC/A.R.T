"""EROFS image extraction — 解包 EROFS 镜像。"""

import os
from pathlib import Path

from pys.utils import V, display, call
from pys.workspace import LayoutError
from pys.workspace import (
    workspace_partition, metadata_path,
    normalize_erofs_metadata, _commit_extracted_partition,
)


def extract_erofs(working_source, partition, destination):
    """Extract an EROFS image into WORKSPACE/<partition>/.

    Returns True on success, False on failure.
    """
    display(f'正在分解: {os.path.basename(working_source)} <erofs>', 3)
    try:
        staged_partition = Path(destination)
        staged_config = Path(V.config)
        staged_config.mkdir(parents=True, exist_ok=True)
        with open(metadata_path(staged_config, partition, '_size.txt'), 'w', encoding='utf-8') as size_file:
            size_file.write(str(os.path.getsize(working_source)))
        if call(['extract.erofs', '-i', working_source, '-o', V.workspace, '-x']) != 0:
            print('> EROFS 分解失败')
            return False
        if normalize_erofs_metadata(partition, staged_config):
            return _commit_extracted_partition(
                partition,
                staged_partition,
                {
                    f'{partition}_contexts.txt',
                    f'{partition}_fsconfig.txt',
                    f'{partition}_size.txt',
                },
            )
        return False
    except (LayoutError, OSError) as error:
        print(f'> EROFS 分解失败: {error}')
        return False
