"""F2FS image extraction.

f2fs-tools only covers the pack direction (mkfs.f2fs / sload.f2fs); it has no
tool that exports a filesystem back to a directory tree. dump.f2fs can only
pull a single inode by number, so a full unpack needs a third-party reader.

imgkit_scuti is used here because it is the only one that emits the Android
metadata this project needs on the way out: fs_config and file_contexts. The
pack side uses the official AOSP tools, so the two halves stay independent —
a bug in one cannot mask a bug in the other.
"""

import os
import shutil
from pathlib import Path

from Scripts.Primary.Utils import call, failure_log

_TOOL = "imgkit_scuti"


# Binary availability.
def missing_tool():
    """Return the unpacker name when it is not on PATH, else ''. """
    return "" if shutil.which(_TOOL) else _TOOL


def _metadata_path(config_dir, partition, suffix):
    return Path(config_dir) / f"{partition}{suffix}"


# Drop the single wrapper directory a reader may nest the tree under.
def _flatten_single_root(output_dir):
    """Unwrap output_dir/<only-entry> when the reader nests the tree once.

    Readers disagree on whether -o names the tree itself or its parent; when
    the result is one directory and nothing else, its contents are the tree.
    """
    entries = list(output_dir.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return
    inner = entries[0]
    if inner.is_symlink() or not any(inner.iterdir()):
        return
    staged = output_dir.parent / f".{output_dir.name}.unwrap"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    inner.rename(staged)
    try:
        output_dir.rmdir()
    except OSError:
        staged.rename(inner)
        return
    staged.rename(output_dir)


def _record_info(config_dir, partition, size):
    from Scripts.Primary.WorkSpace import record_global_info

    record_global_info(
        config_dir, partition,
        {'size': size, 'type': 'f2fs', 'label': partition},
    )


def _verify_metadata(partition, config_dir):
    """Require both Android metadata files, as the ext4/erofs paths do."""
    contexts = _metadata_path(config_dir, partition, '_file_contexts')
    fsconfig = _metadata_path(config_dir, partition, '_fs_config')
    if contexts.is_symlink() or fsconfig.is_symlink():
        print(f'> {partition} 的 F2FS metadata 不能是符号链接')
        return False
    missing = [
        path.name for path in (contexts, fsconfig)
        if not path.is_file()
    ]
    if missing:
        print(f'> {partition} 缺少必要 metadata: {", ".join(missing)}')
        print('  imgkit_scuti 未按 --fs-config-path / --file-contexts-path 输出')
        return False
    return True


# Public F2FS extraction entry point.
def extract_f2fs(working_source, partition, destination):
    """Extract an F2FS image into destination, writing Android metadata."""
    if missing_tool():
        print(f'> 未找到 {_TOOL}，无法解包 F2FS 镜像')
        print(f'  请把 {_TOOL} 放进 art-res/bin-<arch>/')
        return False

    output_dir = Path(destination)
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        print(f'> F2FS 输出目录无效: {output_dir}')
        return False
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    config_dir = output_dir.parent / 'config'
    if config_dir.is_symlink() or (config_dir.exists() and not config_dir.is_dir()):
        print(f'> F2FS metadata 目录无效: {config_dir}')
        return False
    config_dir.mkdir(parents=True, exist_ok=True)

    contexts = _metadata_path(config_dir, partition, '_file_contexts')
    fsconfig = _metadata_path(config_dir, partition, '_fs_config')
    for stale in (contexts, fsconfig):
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass

    print('Process is releasing the file...')
    code, log = call(
        [
            _TOOL, 'unpack',
            '-i', str(working_source),
            '-o', str(output_dir),
            '--fs-config-path', str(fsconfig),
            '--file-contexts-path', str(contexts),
        ],
        capture=True,
    )
    if code != 0:
        print(f'Process log: {failure_log(log, code)}')
        return False

    _flatten_single_root(output_dir)
    if not _verify_metadata(partition, config_dir):
        return False

    try:
        size = os.path.getsize(working_source)
    except OSError:
        size = 0
    _record_info(config_dir, partition, size)
    return True
