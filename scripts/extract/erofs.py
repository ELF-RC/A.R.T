"""Standalone EROFS image extraction."""

from __future__ import annotations

import os
import re
from pathlib import Path

from scripts.primary.utils import V, call, display


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


# Rename EROFS metadata into project naming.
def _normalize_erofs_metadata(partition: str, config_dir: Path) -> bool:
    """Rename extractor metadata to A.R.T's canonical names."""
    config_dir = config_dir.resolve()
    if config_dir.is_symlink() or not config_dir.is_dir():
        raise LayoutError(f'{partition} 的 EROFS metadata 目录无效: {config_dir}')
    raw_contexts = _metadata_path(config_dir, partition, '_file_contexts')
    raw_fsconfig = _metadata_path(config_dir, partition, '_fs_config')
    contexts = _metadata_path(config_dir, partition, '_contexts.txt')
    fsconfig = _metadata_path(config_dir, partition, '_fsconfig.txt')
    if raw_contexts.is_symlink() or raw_fsconfig.is_symlink():
        raise LayoutError(f'{partition} 的 EROFS metadata 不能是符号链接')
    if not (raw_contexts.is_file() and raw_fsconfig.is_file()):
        print(f'> {partition} 的 EROFS metadata 不完整，已保留临时工作现场')
        return False
    os.replace(raw_contexts, contexts)
    os.replace(raw_fsconfig, fsconfig)
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
    display(f'正在分解: {source.name} <erofs>', 3)
    try:
        if source.is_symlink() or not source.is_file():
            raise LayoutError(f'EROFS 输入镜像无效: {source}')
        config_dir.mkdir(parents=True, exist_ok=True)
        _metadata_path(config_dir, partition, '_size.txt').write_text(
            str(source.stat().st_size), encoding='utf-8'
        )
        if call(['extract.erofs', '-i', str(source), '-o', str(workspace), '-x']) != 0:
            print('> EROFS 分解失败')
            return False
        if not _normalize_erofs_metadata(partition, config_dir):
            return False
        return _commit_extracted_partition(
            partition,
            config_dir,
            {
                f'{partition}_contexts.txt',
                f'{partition}_fsconfig.txt',
                f'{partition}_size.txt',
            },
        )
    except (LayoutError, OSError) as error:
        print(f'> EROFS 分解失败: {error}')
        return False
