"""Standalone .win archive extraction."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

from scripts.primary.utils import V, get_file_type


# Validate, stage, and safely unpack WIN content.
class LayoutError(RuntimeError):
    """Raised when a WIN archive or its output layout is invalid."""


_SAFE_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def _validate_component(name: str, label: str = '分区') -> str:
    if not isinstance(name, str) or not _SAFE_COMPONENT.fullmatch(name):
        raise LayoutError(f'非法{label}名称: {name!r}')
    if name in {'.', '..', 'config', 'INPUT', 'OUT', 'WORKSPACE'}:
        raise LayoutError(f'保留{label}名称: {name!r}')
    return name


def _workspace_partition(partition: str) -> Path:
    partition = _validate_component(partition)
    workspace_value = getattr(V, 'workspace', None)
    if not workspace_value or workspace_value == 'None':
        raise LayoutError('工作目录尚未初始化')
    workspace = Path(workspace_value)
    if workspace.is_symlink() or not workspace.is_dir():
        raise LayoutError(f'工作目录无效: {workspace}')
    path = workspace / partition
    if path.resolve().parent != workspace.resolve():
        raise LayoutError(f'分区目录越界: {path}')
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LayoutError(f'分区目录无效: {path}')
    return path


def _create_partition_stage(partition: str) -> Path:
    partition_dir = _workspace_partition(partition)
    if partition_dir.exists():
        shutil.rmtree(partition_dir)
    partition_dir.mkdir(parents=True, exist_ok=True)
    return partition_dir


# Restrict TAR extraction to regular files inside the stage.
def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract only regular files/directories within destination."""
    destination = destination.resolve()
    if destination.is_symlink() or not destination.is_dir():
        raise LayoutError(f'TAR 输出目录无效: {destination}')
    for member in archive:
        if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
            raise LayoutError(f'TAR 不支持的条目类型: {member.name}')
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise LayoutError(f'TAR 包含越界路径: {member.name}') from error
        if sys.version_info >= (3, 12):
            archive.extract(member, path=destination, filter='fully_trusted')
        else:
            archive.extract(member, path=destination)


def _win_partition(source: str) -> str:
    return _validate_component(os.path.basename(source).split('.', 1)[0])


# Merge WIN fragments and dispatch image or TAR content.
def decompress_win(infile_list):
    """Extract image-form or TAR-form WIN archives into WORKSPACE."""
    groups = {}
    for source in infile_list:
        if not os.path.isfile(source):
            continue
        try:
            groups.setdefault(_win_partition(source), []).append(source)
        except LayoutError as error:
            print(f'> 跳过 {source}: {error}')

    workspace_value = getattr(V, 'workspace', None)
    if not workspace_value or workspace_value == 'None':
        print('> 工作目录尚未初始化')
        return
    for partition, fragments in groups.items():
        staged_win = Path(workspace_value) / f'{partition}.win'
        fragments.sort(key=lambda item: (not item.endswith('.win'), os.path.basename(item)))
        try:
            with open(staged_win, 'wb') as destination_file:
                for fragment in fragments:
                    print(f'合并 {fragment} 到 {staged_win}')
                    with open(fragment, 'rb') as source_file:
                        shutil.copyfileobj(source_file, destination_file)
            file_type = get_file_type(str(staged_win))
            if file_type in {'erofs', 'ext', 'sparse', 'super', 'boot', 'vendor_boot'}:
                from scripts.extract.image import decompress_img
                decompress_img(str(staged_win), str(_workspace_partition(partition)))
            elif tarfile.is_tarfile(staged_win):
                staged_partition = _create_partition_stage(partition)
                with tarfile.open(staged_win, 'r') as archive:
                    _safe_extract_tar(archive, staged_partition)
                print(f'> {partition} TAR 分解完成')
            else:
                input('未知格式')
        except (LayoutError, OSError, tarfile.TarError) as error:
            print(f'> {partition} WIN 分解失败: {error}')
        finally:
            if staged_win.is_file():
                staged_win.unlink()