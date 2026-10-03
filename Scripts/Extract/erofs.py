"""Standalone EROFS image extraction."""

from __future__ import annotations

import os
import re
from pathlib import Path

from Scripts.Primary.Utils import V, call
from Scripts.Primary.WorkSpace import record_global_info


# Validate partition paths and normalize extractor metadata.
class LayoutError(RuntimeError):
    """Raised when EROFS output layout or metadata is invalid."""


_SAFE_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def _validate_partition(partition: str) -> str:
    if not isinstance(partition, str) or not _SAFE_COMPONENT.fullmatch(partition):
        raise LayoutError(f'非法分区名称: {partition!r}')
    if partition in {'.', '..', 'config', 'INPUT', 'OUT', 'WORKSPACE'}:
        raise LayoutError(f'保留分区名称: {partition!r}')
    return partition


def _runtime_path(name: str, fallback: Path) -> Path:
    value = getattr(V, name, None)
    if not value or value == 'None':
        return fallback
    return Path(value)


def _metadata_path(config_dir: Path, partition: str, suffix: str) -> Path:
    return config_dir / f'{partition}{suffix}'


# Verify EROFS extractor metadata exists with the project's canonical names.
def _normalize_erofs_metadata(partition: str, config_dir: Path) -> bool:
    """Validate EROFS metadata files are present and regular.

    extract.erofs -x emits <partition>_file_contexts, <partition>_fs_config,
    and a <partition>_fs_options file; A.R.T doesn't consume the last one, so
    it is removed here. The two needed files are checked for presence and that
    they are not symlinks.
    """
    config_dir = config_dir.resolve()
    if config_dir.is_symlink() or not config_dir.is_dir():
        raise LayoutError(f'{partition} 的 EROFS metadata 目录无效: {config_dir}')
    # extract.erofs also emits <partition>_fs_options; drop it (unused).
    fsoptions = _metadata_path(config_dir, partition, '_fs_options')
    if fsoptions.exists() and fsoptions.is_file():
        try:
            fsoptions.unlink()
        except OSError:
            pass
    contexts = _metadata_path(config_dir, partition, '_file_contexts')
    fsconfig = _metadata_path(config_dir, partition, '_fs_config')
    if contexts.is_symlink() or fsconfig.is_symlink():
        raise LayoutError(f'{partition} 的 EROFS metadata 不能是符号链接')
    if not (contexts.is_file() and fsconfig.is_file()):
        print(f'> {partition} 的 EROFS metadata 不完整，已保留临时工作现场')
        return False
    return True


def _commit_extracted_partition(partition: str, config_dir: Path, required: set[str]) -> bool:
    available = {
        path.name for path in config_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    missing = required - available
    if missing:
        print(f'> {partition} 缺少必要 metadata: {", ".join(sorted(missing))}')
        return False
    return True


# Public EROFS extraction entry point.
def extract_erofs(working_source, partition, destination):
    """Extract EROFS into the requested partition directory."""
    partition = _validate_partition(partition)
    source = Path(working_source)
    destination = Path(destination)
    workspace = _runtime_path('workspace', destination.parent)
    config_dir = _runtime_path('config', workspace / 'config')
    print('Process is releasing the file...')
    try:
        if source.is_symlink() or not source.is_file():
            raise LayoutError(f'EROFS 输入镜像无效: {source}')
        config_dir.mkdir(parents=True, exist_ok=True)
        # Raw image size goes into the global info.json (keyed by partition),
        # not a per-partition _size.txt blob.
        # erofs has no volume label; record the partition name as label so a
        # later ext4 repack passes /<partition> to mke2fs -L/-M and e2fsdroid
        # -a, matching the fs_config/contexts prefix (WORKSPACE/<partition>/).
        record_global_info(
            config_dir, partition,
            {'size': source.stat().st_size, 'type': 'erofs', 'label': partition},
        )
        result = call(
            ['extract.erofs', '-i', str(source), '-o', str(workspace), '-x'],
            capture=True,
        )
        if result != 0:
            print('Failed !')
            print(f'Process log: {result}')
            return False
        print('Success !')
        if not _normalize_erofs_metadata(partition, config_dir):
            return False
        return _commit_extracted_partition(
            partition,
            config_dir,
            {
                f'{partition}_file_contexts',
                f'{partition}_fs_config',
                'info.json',
            },
        )
    except (LayoutError, OSError) as error:
        print(f'> EROFS 分解失败: {error}')
        return False
