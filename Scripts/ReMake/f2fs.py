"""F2FS image repacker.

Follows AOSP's system/extras/f2fs_utils/mkf2fsuserimg.sh: create the filesystem
with make_f2fs, then load the tree with sload_f2fs. Both tools come from
f2fs-tools, so the output matches what an Android build produces.

AOSP's exact invocation, from a real build:
    make_f2fs -g android -l odm -O project_quota,extra_attr \
              -O compression,extra_attr -O ro -b 4096 -w 4096 odm.img
    sload_f2fs -C fs_config -f system -s file_contexts -t /system odm.img

-g android is what makes the result Android-compatible: it forces -d1 -f -w
4096 -R 0:0, disables nat_bits/linear_lookup, and adds encrypt, project_quota
and extra_attr (plus verity on 4.14+) — unless -O ro is given, which is
handled first and suppresses all of them.
"""

import os
import time

from Scripts.Primary.Utils import (
    CLOSE,
    GREEN,
    RED,
    V,
    YELLOW,
    call,
    ceil,
    failure_log,
)
from Scripts.Primary.FileConfigPatcher import (
    patch_fsconfig,
    patch_file_contexts,
    translate_contexts_to_ascii,
)
from Scripts.Primary.WorkSpace import load_image_json
from Scripts.ReMake.dat_br import recompress_dat_br

_TOOLS = ("make_f2fs", "sload_f2fs")
_BLOCK_SIZE = 4096


# Metadata normalization, image construction, and DAT hand-off.
def walk_contexts(path):
    """Deduplicate a generated fs config or SELinux contexts file.

    Order-preserving: dict.fromkeys() drops duplicates while keeping first
    seen order, so the path order patch_fsconfig established survives. sload
    walks the same file and the ordering keeps its lookups predictable.
    """
    with open(path, "r", encoding="UTF-8") as source:
        lines = list(dict.fromkeys(source.readlines()))
    if os.path.isfile(path):
        os.remove(path)
    with open(path, "a+", encoding="UTF-8") as target:
        target.writelines(lines)


# Binary availability.
def missing_tools():
    """Return the f2fs-tools binaries that are not on PATH."""
    import shutil
    return [tool for tool in _TOOLS if not shutil.which(tool)]


# Size an image the way AOSP does: content plus a flat reserve.
def _f2fs_image_size(source):
    """Return the image size in bytes.

    build_image.py sizes a f2fs partition as `du -b -k -s` * 1024 plus a flat
    reserve (16 MiB unless the device overrides it), then rounds up to the
    block size. f2fs metadata (SIT/NAT/SSA plus 5% overprovision) is covered
    by that reserve; a percentage margin is added on top for very large trees,
    where 16 MiB alone would be thin.
    """
    reserve_mb = int(V.SETUP_MANIFEST.get('F2FS_RESERVE_MB', '16') or 16)
    content = 0
    for root, _dirs, files in os.walk(source):
        for name in files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                try:
                    content += os.path.getsize(path)
                except OSError:
                    pass
    size = content + reserve_mb * 1024 * 1024
    size = max(size, content + ceil(content * 0.02))
    return ceil(size / _BLOCK_SIZE) * _BLOCK_SIZE


def _timestamp():
    return (
        int(time.time())
        if V.SETUP_MANIFEST["UTC"].lower() == "live"
        else V.SETUP_MANIFEST["UTC"]
    )


def _flag(key, default="0"):
    return V.SETUP_MANIFEST.get(key, default) == "1"


# Prepare mount point, timestamp, metadata, and output paths.
def _prepare(source, fsconfig, contexts, dumpinfo):
    info_label, info_size = load_image_json(dumpinfo, source) if dumpinfo else ('', 0)
    partition = os.path.basename(source)
    label = info_label if info_label and info_label != '/' else partition
    # sload_f2fs wants an absolute mount point; AOSP normalises a bare name
    # by prepending '/'. system-as-root records '/' and is already absolute.
    mount = label if label.startswith('/') else '/' + label

    os.makedirs(V.out, exist_ok=True)
    distance = os.path.join(V.out, f"{partition}.img")
    if os.path.isfile(distance):
        os.remove(distance)
    new_distance = os.path.join(V.out, f"{partition}_new.img")
    if os.path.isfile(new_distance):
        os.remove(new_distance)

    if V.SETUP_MANIFEST.get("PATCH_FSCONFIG", "1") == "1":
        patch_fsconfig(source, fsconfig)
    if V.SETUP_MANIFEST.get("PATCH_CONTEXTS", "1") == "1":
        patch_file_contexts(source, contexts)
    walk_contexts(fsconfig)
    walk_contexts(contexts)
    # mkfs.f2fs creates lost+found itself, and no source-tree walk covers it,
    # so the contexts file needs a rule or the directory lands unlabeled. Same
    # guard the ext4 packer uses; the path is a regex, hence the escaped '+'.
    if os.path.isfile(contexts):
        existing = open(contexts, 'r', encoding='utf-8').read()
        if 'lost+found' not in existing and 'lost\\+found' not in existing:
            with open(contexts, 'a', encoding='utf-8', newline='\n') as handle:
                handle.write(f'/{partition}/lost\\+found u:object_r:system_file:s0\n')
    # sload_f2fs reads both files as UTF-8; the shared normalize keeps this
    # path identical to the ext4/erofs packers, which cannot take raw bytes.
    translate_contexts_to_ascii(contexts, contexts)

    if V.SETUP_MANIFEST["IMAGE_SIZE"] == "1" and info_size:
        size = info_size
    else:
        size = _f2fs_image_size(source)

    return {
        "label": label,
        "partition": partition,
        "mount": mount,
        "distance": distance,
        "new_distance": new_distance,
        "timestamp": _timestamp(),
        "size": size,
    }


# Build the make_f2fs argument list, mirroring mkf2fsuserimg.sh.
def _make_f2fs_cmd(state, image):
    cmd = ["make_f2fs", "-g", "android", "-l", state["label"]]
    # -O ro does not replace the other features: AOSP's own command carries
    # project_quota, compression and ro together. Inside -g android, ro only
    # suppresses the *implicit* encrypt/verity that the android profile would
    # otherwise add; explicitly requested -O flags still apply. The order
    # follows mkf2fsuserimg.sh (prjquota, casefold, compression, readonly).
    if _flag("F2FS_PRJQUOTA"):
        cmd += ["-O", "project_quota,extra_attr"]
    if _flag("F2FS_CASEFOLD"):
        cmd += ["-O", "casefold", "-C", "utf8"]
    if _flag("F2FS_COMPRESSION"):
        cmd += ["-O", "compression,extra_attr"]
    if _flag("F2FS_READONLY", "1"):
        cmd += ["-O", "ro"]
    cmd += ["-b", str(_BLOCK_SIZE), "-w", str(_BLOCK_SIZE)]
    cmd.append(image)
    return cmd


# Build the sload_f2fs argument list, mirroring mkf2fsuserimg.sh.
def _sload_f2fs_cmd(state, fsconfig, contexts, source, image):
    cmd = ["sload_f2fs"]
    if fsconfig and os.path.isfile(fsconfig):
        cmd += ["-C", fsconfig]
    cmd += ["-f", source]
    if contexts and os.path.isfile(contexts):
        cmd += ["-s", contexts]
    cmd += ["-t", state["mount"]]
    if state["timestamp"]:
        cmd += ["-T", str(state["timestamp"])]
    # --compression sets the feature bit; -c is what actually compresses on
    # the way in. AOSP pairs them, and a filesystem with the bit but no
    # compression is a silently wrong image, so both are driven by one switch.
    if _flag("F2FS_COMPRESSION"):
        cmd.append("-c")
        cmd += ["-a", V.SETUP_MANIFEST.get("F2FS_COMP_ALGORITHM", "lz4") or "lz4"]
        cluster = V.SETUP_MANIFEST.get("F2FS_COMP_CLUSTER", "2") or "2"
        cmd += ["-L", cluster]
    cmd.append(image)
    return cmd


# Build F2FS and optionally convert to sparse/DAT.
def _write_image(state, fsconfig, contexts, source, flag):
    new_distance = state["new_distance"]
    # make_f2fs needs an existing file of the exact size; a sparse truncate
    # keeps the workarea small until sload fills it.
    try:
        with open(new_distance, "wb") as handle:
            handle.truncate(state["size"])
    except OSError as error:
        print(f'\n{RED}Failed !{CLOSE}')
        print(f'Process log: 无法创建镜像文件: {error}')
        return False

    print('Process remaking the file system...', end='', flush=True)
    mkfs_code, mkfs_log = call(_make_f2fs_cmd(state, new_distance), capture=True)
    if mkfs_code != 0:
        try:
            os.remove(new_distance)
        except OSError:
            pass
        print(f'\n{RED}Failed !{CLOSE}')
        print(f'Process log: {failure_log(mkfs_log, mkfs_code)}')
        return False

    sload_code, sload_log = call(
        _sload_f2fs_cmd(state, fsconfig, contexts, source, new_distance),
        capture=True,
    )
    # sload_f2fs returns 1 when it repaired filesystem errors, which AOSP's
    # wrapper accepts: the image is usable, just not pristine.
    if sload_code not in (0, 1):
        try:
            os.remove(new_distance)
        except OSError:
            pass
        print(f'\n{RED}Failed !{CLOSE}')
        print(f'Process log: {failure_log(sload_log, sload_code)}')
        return False
    if sload_code == 1:
        print(f'\n{YELLOW}sload_f2fs 修正了文件系统错误{CLOSE}')

    if not os.path.isfile(new_distance):
        print(f'\n{RED}Failed !{CLOSE}')
        print('Process log: sload_f2fs 未生成目标镜像')
        return False

    print(f'\n{GREEN}Success !{CLOSE}')
    if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" or flag > 9:
        print("开始转换: sparse format ...")
        sparse_code, sparse_log = call(
            ["img2simg", new_distance, state["distance"]], capture=True)
        if sparse_code != 0:
            print(f'{RED}Failed !{CLOSE}')
            print(f'Process log: {failure_log(sparse_log, sparse_code)}')
            return False
        try:
            os.remove(new_distance)
        except OSError:
            pass
    else:
        if os.path.isfile(state["distance"]):
            os.remove(state["distance"])
        os.rename(new_distance, state["distance"])
    return True


# Public F2FS repack entry point.
def recompress_f2fs(source, fsconfig, contexts, dumpinfo, flag=8):
    """Recompress a partition directory into an F2FS image or DAT package."""
    missing = missing_tools()
    if missing:
        print(f'> 未找到 {", ".join(missing)}，无法打包 F2FS 镜像')
        print('  请把它们放进 art-res/bin-<arch>/')
        return

    state = _prepare(source, fsconfig, contexts, dumpinfo)
    sparse = "YES" if V.SETUP_MANIFEST["REPACK_SPARSE_IMG"] == "1" else "NO"
    comp = "YES" if _flag("F2FS_COMPRESSION") else "NO"
    print(
        f"F2FS: Label:{state['label']} Size:{state['size']} "
        f"Mount:{state['mount']} Sparse:{sparse} Compression:{comp}"
    )

    if _write_image(state, fsconfig, contexts, source, flag):
        from Scripts.ReMake.ext4 import _update_dynamic_partitions

        if _update_dynamic_partitions(state["partition"], state["distance"]) and flag > 9:
            recompress_dat_br(state["partition"], state["distance"], flag)
