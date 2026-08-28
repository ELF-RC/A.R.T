"""EXT4 image extraction — 解包 EXT4 / sparse 镜像。"""

import os
from pathlib import Path

from rich import print as echo
from rich.console import Console

from pys.utils import V, display
from pys import imgextractor
from pys.workspace import LayoutError
from pys.workspace import (
    workspace_partition, create_partition_stage, metadata_path,
    ensure_contexts_file, _commit_extracted_partition,
)


def extract_ext4(working_source, partition, destination):
    """Extract an EXT4 image into WORKSPACE/<partition>/.

    Returns True on success, False on failure.
    """
    try:
        _, staged_partition, staged_config = create_partition_stage(partition, 'ext-extract')
        with Console().status(f"[yellow]正在提取{os.path.basename(working_source)}[/]"):
            imgextractor.ULTRAMAN().MONSTER(working_source, str(staged_partition))
        ensure_contexts_file(partition, staged_config)
        return _commit_extracted_partition(
            partition,
            staged_partition,
            {
                f'{partition}_contexts.txt',
                f'{partition}_fsconfig.txt',
                f'{partition}_info.txt',
            },
        )
    except Exception as error:
        print(f'> EXT4 分解失败: {error}')
        return False


def convert_sparse(working_source):
    """Convert a sparse image to raw format.

    Returns the path to the raw image, or None on failure.
    """
    display(f'正在转换: Unsparse Format [{os.path.basename(working_source)}] ...')
    raw_source = imgextractor.ULTRAMAN().APPLE(working_source)
    if raw_source and os.path.isfile(raw_source):
        return raw_source
    echo('[red][Failed][/]')
    return None
