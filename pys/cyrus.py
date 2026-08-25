import json
import os
import tempfile
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from glob import glob
from pathlib import Path
from hashlib import sha1

import requests
from rich import print as echo
from rich.console import Console
from rich.progress import Progress

from pys import dumper as extract_payload
from pys import fspatch
from pys import img2sdat
from pys import imgextractor
from pys import sdat2img
from pys import gettype
from pys import lpunpack
from pys.project_layout import LayoutError, ProjectLayout, UnsupportedLayoutError

PWD_DIR = os.getcwd() + os.sep
MOD_DIR = PWD_DIR + "local/sub/"
ROM_DIR = PWD_DIR
SETUP_JSON = PWD_DIR + "local/set/setup.json"
BIN_PATH = PWD_DIR + "local/bin/Linux/x86_64/"
RED, WHITE, CYAN, YELLOW, MAGENTA, GREEN, BOLD, CLOSE = ['\x1b[91m',
                                                         '\x1b[97m', '\x1b[36m',
                                                         '\x1b[93m', '\x1b[1;35m',
                                                         '\x1b[1;32m',
                                                         '\x1b[1m', '\x1b[0m']


class GlobalValue(object):
    JM = False

    def __init__(self):
        self.programs = ["cpio", "brotli", "img2simg", "e2fsck", "resize2fs",
                         "mke2fs", "e2fsdroid", "mkfs.erofs", "lpmake", "extract.erofs", "magiskboot", "avbroot"]

    def __getattr__(self, item):
        try:
            return getattr(self, item)
        except (Exception, BaseException):
            return "None"


V = GlobalValue()


def change_permissions_recursive(path, mode):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(os.path.join(root, d), mode)
        for f in files:
            os.chmod(os.path.join(root, f), mode)
    os.chmod(path, mode)


if not os.path.isdir(BIN_PATH):
    print(f"Run err on: {platform.system()} {platform.machine()}")
    sys.exit()

os.environ["PATH"] += os.pathsep + BIN_PATH
change_permissions_recursive(BIN_PATH, 0o777)

for prog in V.programs:
    if not shutil.which(prog):
        sys.exit(f"[x] Not found: {prog}\n[i] Please install {prog} \n   Or add <{prog}> to {BIN_PATH}")


def call(exe, kz='Y', out=0, shstate=False, sp=0, env=None):
    """Run a command with MIO-compatible argv handling and no implicit shell."""
    del sp
    if isinstance(exe, (list, tuple)):
        # MIO drops optional empty arguments such as an unset boot flag.
        cmd = [str(item) for item in exe if item not in (None, '')]
        if kz == 'Y' and cmd and not os.path.isabs(cmd[0]):
            cmd[0] = os.path.join(BIN_PATH, cmd[0])
    elif shstate:
        cmd = f'{BIN_PATH}{exe}' if kz == 'Y' else exe
    else:
        cmd = shlex.split(str(exe))
        if kz == 'Y' and cmd and not os.path.isabs(cmd[0]):
            cmd[0] = os.path.join(BIN_PATH, cmd[0])

    try:
        process = subprocess.Popen(
            cmd,
            shell=shstate,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except OSError as error:
        print(f'> 启动命令失败: {error}')
        return 127

    if process.stdout:
        for line in iter(process.stdout.readline, b''):
            if out == 0:
                print(line.decode('utf-8', 'ignore').strip())
    return process.wait()


class CoastTime:

    def __init__(self):
        self.t = 0

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"> Coast Time:{time.perf_counter() - self.t:.8f} s")


def display(message, flag=1, end='\n'):
    flags = {1: "3", 2: "6", 3: "4", 4: "1"}
    print(f"\x1b[1;3{flags[flag]}m [ {time.strftime('%H:%M:%S', time.localtime())} ]\t {message} \x1b[0m", end=end)


def get_dir_size(ddir, max_=1.06):
    size = 0
    for (root, dirs, files) in os.walk(ddir):
        for name in files:
            if not os.path.islink(name):
                try:
                    size += os.path.getsize(os.path.join(root, name))
                except:
                    pass
    return int(size * max_)


def ceil(x):
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        int_part = int(x)
        if x > 0 and x > int_part:
            return int_part + 1
        return int_part
    return int(x)


def load_image_json(dumpinfo, source_dir):
    with open(dumpinfo, "a+", encoding="utf-8") as f:
        f.seek(0)
        info = json.load(f)
    inodes = info["a"]
    block_size = info["b"]
    per_group = info["c"]
    mount_point = info["d"]
    if mount_point != "/":
        mount_point = "/" + mount_point
    fsize = info["s"]
    blocks = ceil(int(fsize) / int(block_size))
    dsize = get_dir_size(source_dir)
    if dsize > int(fsize):
        minsize = dsize - int(fsize)
        if int(minsize) < 20971520:
            isize = int(dsize * 1.08)
            dsize = str(isize)
    else:
        dsize = fsize
    return fsize, dsize, inodes, block_size, blocks, per_group, mount_point


def load_setup_json():
    with open(SETUP_JSON, "r", encoding="utf-8") as manifest_file:
        V.SETUP_MANIFEST = json.load(manifest_file)
    set_default_env_setup()
    validate_default_env_setup(V.SETUP_MANIFEST)
    with open(SETUP_JSON, "w", encoding="utf-8") as f:
        json.dump(V.SETUP_MANIFEST, f, indent=4)


_SETUP_DEFAULTS = {
    'REPACK_EROFS_IMG': "1",
    'REPACK_TO_RW': "0",
    'RESIZE_IMG': "0",
    'RESIZE_EROFSIMG': "1",
    'EROFS_LEVEL': "1",
    'EROFS_OLD_KERNEL': "0",
    'REPACK_SPARSE_IMG': "0",
    'REPACK_BR_LEVEL': "3",
    'SUPER_SIZE': "9126805504",
    'GROUP_NAME': "qti_dynamic_partitions",
    'UTC': "LIVE",
    'UNPACK_SPLIT_DAT': "15",
    'BOOT_SKIP_RAMDISK': "0"}


def set_default_env_setup():
    """Merge defaults into existing manifest, preserving user values."""
    for key, value in _SETUP_DEFAULTS.items():
        V.SETUP_MANIFEST.setdefault(key, value)


def validate_default_env_setup(setup_manifest):
    for k in ('REPACK_EROFS_IMG', 'REPACK_SPARSE_IMG', 'REPACK_TO_RW',
              'RESIZE_IMG'):
        if setup_manifest[k] not in ('1', '0'):
            sys.exit(f"Invalid [{k}] - must be one of <1/0>")

    if setup_manifest["RESIZE_EROFSIMG"] not in ('1', '2', '0'):
        sys.exit("Invalid [RESIZE_EROFSIMG] - must be one of <1/2/0>")
    if not re.match("[0-9]", setup_manifest["REPACK_BR_LEVEL"]):
        sys.exit(f"Invalid [{setup_manifest['REPACK_BR_LEVEL']}] - must be one of <0-9>")
    if not re.match("\\d{1,3}", setup_manifest["UNPACK_SPLIT_DAT"]):
        sys.exit(
            f'Invalid ["UNPACK_SPLIT_DAT" : "{setup_manifest["UNPACK_SPLIT_DAT"]}"] - must be one of <1-999>')


def env_setup():
    # 分类后的设置项：(显示名, JSON key)
    categories = [
        ('EXT4', [
            ('合成EXT4动态分区状态[0:RO/1:RW]', 'REPACK_TO_RW'),
            ('合成EXT4压缩分区空间[0/1]', 'RESIZE_IMG'),
        ]),
        ('EROFS', [
            ('合成EROFS压缩算法[0:NO/1:LZ4HC/2:LZ4]', 'RESIZE_EROFSIMG'),
            ('EROFS压缩等级[1]', 'EROFS_LEVEL'),
            ('EROFS旧内核兼容[0/1]', 'EROFS_OLD_KERNEL'),
        ]),
        ('SUPER', [
            ('动态分区簇名称[qti_dynamic_partitions]', 'GROUP_NAME'),
            ('动态SUPER分区总大小[9126805504]', 'SUPER_SIZE'),
        ]),
        ('IMG', [
            ('合成镜像类型[0:EXT4/1:EROFS]', 'REPACK_EROFS_IMG'),
            ('合成镜像格式[0:RAW/1:SPARSE]', 'REPACK_SPARSE_IMG'),
        ]),
        ('BOOT', [
            ('跳过Ramdisk解包打包[0/1]', 'BOOT_SKIP_RAMDISK'),
        ]),
        ('Other', [
            ('压缩BROTLI等级[0-9|3]', 'REPACK_BR_LEVEL'),
            ('自定义UTC时间戳[live]', 'UTC'),
            ('分段DAT/IMG支持个数[15]', 'UNPACK_SPLIT_DAT'),
        ]),
    ]
    # 生成平铺映射：序号 → (显示名, JSON key)
    flat_map = {}
    for _, items in categories:
        for name, key in items:
            flat_map[len(flat_map) + 1] = (name, key)
    while True:
        os.system("clear")
        print(f"\n> {GREEN}设置文件{CLOSE}: {SETUP_JSON.replace(PWD_DIR, '')}")
        with open(SETUP_JSON, 'r', encoding='utf-8') as ss:
            data = json.load(ss)
        i = 1
        for category, items in categories:
            print(f"\n  {CYAN}[{category}]{CLOSE}")
            for name, key in items:
                print(f"  {YELLOW}[{'0' if i < 10 else ''}{i}]{CLOSE}\t{BOLD}{name}{CLOSE}: {GREEN}{data[key]}{CLOSE}")
                i += 1
        sum_ = input(f"\n请输入你要更改的序列，输入{YELLOW}00{CLOSE}为返回：")
        if sum_ in ["00", "0"]:
            return
        if not sum_.isdigit() or int(sum_) not in flat_map:
            continue
        name, key = flat_map[int(sum_)]
        data[key] = input(name + "：")
        validate_default_env_setup(data)
        with open(SETUP_JSON, 'w', encoding='utf-8') as ss:
            json.dump(data, ss, ensure_ascii=False, indent=4)


def check_permissions():
    if not os.path.isfile(SETUP_JSON):
        if not os.path.isdir(os.path.dirname(SETUP_JSON)):
            os.makedirs(os.path.dirname(SETUP_JSON))
        set_default_env_setup()
    menu_once()


def find_file(path, rule):
    for (root, lists, files) in os.walk(path):
        for file in files:
            if re.search(rule, os.path.basename(file)):
                yield os.path.join(root, file)


def partition_name(image_path):
    """Return the validated partition name represented by an image path."""
    name = os.path.basename(image_path)
    for suffix in ('.unsparse.img', '.new.dat.br', '.new.dat', '.img', '.win'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return ProjectLayout.validate_component(name, "分区")


def workspace_partition(partition):
    return str(V.layout.partition_dir(partition))


def workspace_temp(category):
    """Create a temporary directory inside WORKSPACE for intermediate files."""
    return str(V.layout.create_stage_dir(category)) + os.sep


def partition_metadata_names(partition):
    return (
        f'{partition}_contexts.txt',
        f'{partition}_fsconfig.txt',
        f'{partition}_info.txt',
        f'{partition}_space.txt',
        f'{partition}_size.txt',
        f'{partition}_kernel.txt',
        f'{partition}_file_contexts',
        f'{partition}_fs_config',
    )


def metadata_path(config_dir, partition, suffix):
    return Path(config_dir) / f'{partition}{suffix}'


def normalize_erofs_metadata(partition, config_dir):
    """Normalize only this EROFS partition's metadata in a staging config directory."""
    config_dir = Path(config_dir)
    if config_dir.is_symlink() or not config_dir.is_dir():
        raise LayoutError(f'{partition} 的 EROFS metadata 目录无效: {config_dir}')
    raw_contexts = metadata_path(config_dir, partition, '_file_contexts')
    raw_fsconfig = metadata_path(config_dir, partition, '_fs_config')
    contexts = metadata_path(config_dir, partition, '_contexts.txt')
    fsconfig = metadata_path(config_dir, partition, '_fsconfig.txt')
    if raw_contexts.is_symlink() or raw_fsconfig.is_symlink():
        raise LayoutError(f'{partition} 的 EROFS metadata 不能是符号链接')
    if not (raw_contexts.is_file() and raw_fsconfig.is_file()):
        print(f"> {partition} 的 EROFS metadata 不完整，已保留临时工作现场")
        return False
    os.replace(raw_contexts, contexts)
    os.replace(raw_fsconfig, fsconfig)
    return True


def ensure_contexts_file(partition, config_dir):
    """Create the canonical contexts file when an image has no SELinux xattrs."""
    contexts = metadata_path(config_dir, partition, '_contexts.txt')
    contexts.touch(exist_ok=True)
    return contexts


def create_partition_stage(partition, category, create_partition=True):
    """Prepare WORKSPACE/<partition>/ and WORKSPACE/config/ for direct extraction."""
    partition = ProjectLayout.validate_component(partition, '分区')
    partition_dir = Path(workspace_partition(partition))
    config_dir = Path(V.config)
    config_dir.mkdir(parents=True, exist_ok=True)
    if partition_dir.exists():
        shutil.rmtree(partition_dir)
    if create_partition:
        partition_dir.mkdir(parents=True, exist_ok=True)
    return partition_dir.parent, partition_dir, config_dir


def workspace_relative_path(relative_path):
    """Resolve a configured relative path and keep it inside editable partitions."""
    relative = Path(relative_path)
    if relative.parts and relative.parts[0] in {'config'}:
        raise LayoutError(f"不允许修改 WORKSPACE/{relative.parts[0]}")
    return V.layout.require_workspace_path(Path(V.workspace, relative))


def _get_image_logical_size(source):
    """Return logical size of an image (sparse-aware)."""
    return imgextractor.ULTRAMAN().LEMON(source)


def repack_super(selected_parts, super_type, super_sparse):
    """Synthesize super.img from selected partition images.

    Args:
        selected_parts: list of (name, path) tuples from INPUT
        super_type: 0=A-only, 1=A/B, 2=Virtual A/B
        super_sparse: 1=sparse output, 0=raw output
    """
    group_name = V.SETUP_MANIFEST['GROUP_NAME']
    super_size = V.SETUP_MANIFEST['SUPER_SIZE']
    super_output = os.path.join(V.out, 'super.img')
    type_names = {0: 'A-only', 1: 'A/B', 2: 'Virtual A/B'}

    argvs = [
        'lpmake',
        '--metadata-size', '65536',
        '--super-name', 'super',
        '--device', f'super:{super_size}',
    ]
    image_parts = []

    # Convert sparse images to raw before passing to lpmake
    raw_parts = []
    try:
        for name, path in selected_parts:
            if gettype.gettype(path) == 'sparse':
                display(f'转换 sparse: {os.path.basename(path)} ...')
                raw = imgextractor.ULTRAMAN().APPLE(path)
                if not raw or not os.path.isfile(raw):
                    print(f'> 无法转换 sparse 镜像: {path}')
                    return
                raw_parts.append((name, raw))
            else:
                raw_parts.append((name, path))
    except (LayoutError, OSError) as error:
        print(f'> 准备 super 镜像失败: {error}')
        return

    # Check for _b images in INPUT for A/B and VAB modes
    input_dir = V.input

    try:
        if super_type == 0:
            # A-only: slots=2, single group, no suffix
            argvs.extend(['--metadata-slots', '2',
                          '--group', f'{group_name}:{super_size}'])
            for name, path in raw_parts:
                size = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}:readonly:{size}:{group_name}',
                    '--image', f'{name}={path}',
                ])
                image_parts.append(name)
        elif super_type == 1:
            # A/B: slots=3, dual groups _a/_b, _b uses actual image if available
            argvs.extend(['--metadata-slots', '3',
                          '--group', f'{group_name}_a:{super_size}',
                          '--group', f'{group_name}_b:{super_size}'])
            for name, path in raw_parts:
                size_a = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}_a:readonly:{size_a}:{group_name}_a',
                    '--image', f'{name}_a={path}',
                ])
                # Check for _b.img in INPUT (MIO behavior: _b empty if not provided)
                b_path = os.path.join(input_dir, f'{name}_b.img')
                if os.path.isfile(b_path):
                    if gettype.gettype(b_path) == 'sparse':
                        display(f'转换 sparse: {os.path.basename(b_path)} ...')
                        b_raw = imgextractor.ULTRAMAN().APPLE(b_path)
                        if b_raw and os.path.isfile(b_raw):
                            b_path = b_raw
                    size_b = os.path.getsize(b_path)
                    argvs.extend([
                        '--partition', f'{name}_b:readonly:{size_b}:{group_name}_b',
                        '--image', f'{name}_b={b_path}',
                    ])
                else:
                    argvs.extend([
                        '--partition', f'{name}_b:readonly:0:{group_name}_b',
                    ])
                image_parts.append(name)
        else:
            # Virtual A/B: slots=3, dual groups _a/_b, _b size=0, --virtual-ab
            argvs.extend(['--metadata-slots', '3', '--virtual-ab', '-F',
                          '--group', f'{group_name}_a:{super_size}',
                          '--group', f'{group_name}_b:{super_size}'])
            for name, path in raw_parts:
                size = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}_a:readonly:{size}:{group_name}_a',
                    '--image', f'{name}_a={path}',
                    '--partition', f'{name}_b:readonly:0:{group_name}_b',
                ])
                image_parts.append(name)
    except (LayoutError, OSError) as error:
        print(f'> 准备 super 镜像失败: {error}')
        return

    if not image_parts:
        print('> 未选择任何分区镜像')
        return

    if super_sparse == 1:
        argvs.append('--sparse')
    argvs.extend(['--out', super_output])

    display(f'重新合成: super.img <Size:{super_size}|Type:{type_names[super_type]}|Sparse:{super_sparse}>')
    display(f"包含分区：{'|'.join(image_parts)}")
    with CoastTime():
        result = call(argvs)
    if result != 0 or not os.path.isfile(super_output):
        print('> super.img 合成失败')
        return

    print(f'> super.img 已输出到 {V.out}')


def walk_contexts(contexts):
    with open(contexts, "r", encoding="utf-8") as f3:
        text_list = list(set(f3.readlines()))
    if os.path.isfile(contexts):
        os.remove(contexts)
    with open(contexts, "a+", encoding="utf-8") as f:
        f.writelines(text_list)


def recompress(source, fsconfig, contexts, dumpinfo, flag=8):
    label = os.path.basename(source)
    if not os.path.isdir(V.out):
        os.makedirs(V.out)
    distance = V.out + label + ".img"
    if os.path.isfile(distance):
        os.remove(distance)
    fspatch.main(source, fsconfig)
    walk_contexts(fsconfig)
    walk_contexts(contexts)
    timestamp = int(time.time()) if V.SETUP_MANIFEST["UTC"].lower() == "live" else V.SETUP_MANIFEST["UTC"]
    read = "ro"
    resize2_rw = False
    fsize = None
    if dumpinfo:
        (fsize, dsize, inodes, block_size, blocks, per_group, mount_point) = load_image_json(dumpinfo, source)
        size = dsize
    else:
        size = get_dir_size(source, 1.3)
        if int(size) <= 1048576:
            size = 1048576
        mount_point = "/" + label
        if os.path.isfile(source + os.sep + "system" + os.sep + "build.prop"):
            mount_point = "/"
    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "0":
        fs_variant = "ext4"
        block_size = 4096
        blocks = ceil(int(size) / int(block_size))
        if not fsize:
            read = "rw"
        new_distance = V.out + label + "_new.img"
        if os.path.isfile(new_distance):
            os.remove(new_distance)
        mke2fs_a_cmd = [
            'mke2fs', '-O', '^has_journal,^metadata_csum,extent,huge_file,^flex_bg,^64bit,uninit_bg,dir_nlink,extra_isize',
            '-L', label, '-I', '256', '-M', mount_point, '-m', '0', '-t', 'ext4', '-b', str(block_size),
            new_distance, str(blocks),
        ]
        e2fsdroid_a_cmd = [
            'e2fsdroid', '-e', '-T', str(timestamp), '-S', contexts, '-C', fsconfig,
            '-a', f'/{label}', '-f', source, new_distance,
        ]
    else:
        fs_variant = "erofs"
        erofs_level = V.SETUP_MANIFEST.get("EROFS_LEVEL", "1")
        erofs_format = "lz4hc" if V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1" else "lz4"
        erofs_compress = f'{erofs_format},{erofs_level}' if erofs_format != 'lz4' else erofs_format
        mkerofs_cmd = ['mkfs.erofs']
        if V.SETUP_MANIFEST.get("EROFS_OLD_KERNEL", "0") == "1":
            mkerofs_cmd.extend(['-E', 'legacy-compress'])
        new_distance = V.out + label + "_new.img"
        if os.path.isfile(new_distance):
            os.remove(new_distance)
        mkerofs_cmd.extend([
            f'-z{erofs_compress}',
            '-T', str(timestamp),
            f'--mount-point=/{label}',
            f'--product-out={V.workspace}',
            f'--fs-config-file={fsconfig}',
            f'--file-contexts={contexts}',
            new_distance,
            source,
        ])
    printinform = f"Size:{size}|FsT:{fs_variant}|FsR:{read}|Sparse:{V.SETUP_MANIFEST['REPACK_SPARSE_IMG']}"
    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "0":
        if V.SETUP_MANIFEST["RESIZE_IMG"] == "1" and V.SETUP_MANIFEST["REPACK_TO_RW"] == "1":
            printinform += "|Resize:1"
        else:
            printinform += "|Resize:0"
    elif V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "1":
        printinform += "|lz4hc"
    elif V.SETUP_MANIFEST["RESIZE_EROFSIMG"] == "2":
        printinform += "|lz4"
    display(printinform)
    display(f"重新合成: {label}.img ...", 4)

    if V.SETUP_MANIFEST["REPACK_EROFS_IMG"] == "1":
        if call(mkerofs_cmd) != 0:
            try:
                os.remove(new_distance)
            except:
                pass
        if os.path.isfile(new_distance):
            print(" Done")
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 9:
                display("开始转换: sparse format ...")
                call(['img2simg', new_distance, distance])
                try:
                    os.remove(new_distance)
                except:
                    pass
            else:
                if os.path.isfile(distance):
                    os.remove(distance)
                os.rename(new_distance, distance)
    else:
        call(mke2fs_a_cmd)
        if os.path.isfile(new_distance):
            if call(e2fsdroid_a_cmd) != 0:
                try:
                    os.remove(new_distance)
                except:
                    pass
        if os.path.isfile(new_distance):
            print(" Done")
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 9:
                display("开始转换: sparse format ...")
                call(['img2simg', new_distance, distance])
                try:
                    os.remove(new_distance)
                except:
                    pass
            else:
                if os.path.isfile(distance):
                    os.remove(distance)
                os.rename(new_distance, distance)
    if os.path.isfile(distance):
        op_list = V.input + "dynamic_partitions_op_list"
        new_op_list = V.out + "dynamic_partitions_op_list"
        if os.path.isfile(op_list) or os.path.isfile(new_op_list):
            if not os.path.isfile(new_op_list):
                shutil.copyfile(op_list, new_op_list)
        else:
            CONTENT = "remove_all_groups\n"
            for slot in ('_a', '_b'):
                CONTENT += f"add_group qti_dynamic_partitions{slot} {V.SETUP_MANIFEST['SUPER_SIZE']}\n"
            for partition in ('system', 'system_ext', 'product', 'vendor', 'odm'):
                for slot in ('_a', '_b'):
                    CONTENT += f"add {partition}{slot} qti_dynamic_partitions{slot}\n"
            for partition in ('system_a', 'system_ext_a', 'product_a', 'vendor_a', 'odm_a'):
                CONTENT += f"resize {partition} 2\n"
            with open(new_op_list, "w", encoding="UTF-8", newline="\n") as ST:
                ST.write(CONTENT)
        renew_size = os.path.getsize(distance)
        with open(new_op_list, "r", encoding="UTF-8") as f_r:
            data = f_r.readlines()
            with open(new_op_list, "w", encoding="UTF-8") as f_w:
                for line in data:
                    if f"resize {label} " in line:
                        line = f"resize {label} {renew_size}\n"
                    elif f"resize {label}_a " in line:
                        line = f"resize {label}_a {renew_size}\n"
                    f_w.write(line)
        if flag > 9:
            display(f"重新生成: {label}.new.dat ...", 3)
            img2sdat.main(distance, V.out, 4, label)
            newdat = V.out + label + ".new.dat"
            if os.path.isfile(newdat):
                print(" Done")
                os.remove(distance)
                if flag == 11:
                    level = V.SETUP_MANIFEST["REPACK_BR_LEVEL"]
                    display(f"重新生成: {label}.new.dat.br | Level={level} ...", 3)
                    newdat_brotli = newdat + ".br"
                    call(['brotli', f'-{level}jfo', newdat_brotli, newdat])
                    print(f" {GREEN}打包成功{CLOSE}" if os.path.isfile(newdat_brotli) else f" {RED}打包失败{CLOSE}")
            else:
                print(f" {RED}打包失败{CLOSE}")
    else:
        print(f" {RED}打包失败{CLOSE}")


def rmdire(path):
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
        except PermissionError:
            print("无法删除文件夹，权限不足")
        else:
            print("删除成功！")


def unpackboot(file, distance):
    """Unpack a boot image into a staging directory and report success."""
    original_dir = os.getcwd()
    work_dir = Path(distance)
    try:
        rmdire(work_dir)
        work_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(file, work_dir / "boot_o.img")
        os.chdir(work_dir)
        if call(['magiskboot', 'unpack', '-h', str(file)]) != 0:
            print(f"Unpack {file} Fail...")
            return False

        ramdisk = work_dir / 'ramdisk.cpio'
        if not ramdisk.is_file() or V.SETUP_MANIFEST.get('BOOT_SKIP_RAMDISK', '0') == '1':
            print("Unpack Done!")
            return True

        comp = gettype.gettype(str(ramdisk))
        print(f"Ramdisk is {comp}")
        (work_dir / 'comp').write_text(comp, encoding='utf-8')
        if comp != 'unknown':
            compressed_ramdisk = work_dir / 'ramdisk.cpio.comp'
            os.replace(ramdisk, compressed_ramdisk)
            if call([
                'magiskboot',
                'decompress',
                str(compressed_ramdisk),
                str(ramdisk),
            ]) != 0:
                print("Decompress Ramdisk Fail...")
                return False

        ramdisk_dir = work_dir / 'ramdisk'
        ramdisk_dir.mkdir(exist_ok=True)
        print("Unpacking Ramdisk...")
        os.chdir(ramdisk_dir)
        if call(['magiskboot', 'cpio', str(work_dir / 'ramdisk.cpio'), 'extract']) != 0:
            print("Unpack Ramdisk Fail...")
            os.chdir(work_dir)
            return False
        os.chdir(work_dir)
        return True
    except OSError as error:
        print(f"Unpack {file} Fail: {error}")
        return False
    finally:
        os.chdir(original_dir)


def dboot(infile, dist):
    or_dir = os.getcwd()
    if not os.path.exists(infile):
        print(f"Cannot Find {infile}...")
        return
    if os.path.isdir(infile + os.sep + "ramdisk") and V.SETUP_MANIFEST.get('BOOT_SKIP_RAMDISK', '0') == '0':
        new_cpio = os.path.join(infile, "ramdisk-new.cpio")
        try:
            os.chdir(infile + os.sep + "ramdisk")
        except Exception as e:
            print("Ramdisk Not Found.. %s" % e)
            return
        busybox = gettype.findfile('busybox', BIN_PATH).replace('\\', "/")
        try:
            proc = subprocess.Popen(
                f'find . -mindepth 1 | {busybox} cpio -o -H newc -R 0:0 -F ../ramdisk-new.cpio',
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            stdout, _ = proc.communicate(timeout=120)
            cpio_rc = proc.returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"cpio error: {e}")
            cpio_rc = 1
        os.chdir(infile)
        if cpio_rc != 0 or not os.path.isfile(new_cpio):
            print("Pack Ramdisk Fail... (cpio error)")
            os.chdir(or_dir)
            return
        print("Pack Ramdisk Successful..")
        try:
            os.remove("ramdisk.cpio")
        except OSError:
            pass
        os.rename("ramdisk-new.cpio", "ramdisk.cpio")
    else:
        os.chdir(infile)
    repack_args = ['magiskboot', 'repack', os.path.join(infile, "boot_o.img")]
    if call(repack_args) != 0:
        print("Pack boot Fail...")
        os.chdir(or_dir)
        return
    else:
        if os.path.exists(os.path.join(dist, os.path.basename(infile) + ".img")):
            os.remove(os.path.join(dist, os.path.basename(infile) + ".img"))
        os.rename(infile + os.sep + "new-boot.img", os.path.join(dist, os.path.basename(infile) + ".img"))
        os.chdir(or_dir)
        print("Pack Successful...")


def boot_utils(source, distance, flag=1):
    if not os.path.isdir(distance):
        os.makedirs(distance)
    if flag == 1:
        display(f"正在分解: {os.path.basename(source)}")
        return unpackboot(source, distance)
    if flag == 2:
        display(f"重新合成: {os.path.basename(source)}.img")
        return dboot(source, distance)
    return False


def _stage_work_source(source, category):
    """Return the source path directly; INPUT is read but never modified."""
    return str(Path(source).resolve())


def _destination_partition(distance, source):
    if distance:
        candidate = os.path.basename(os.path.normpath(distance))
    else:
        candidate = partition_name(source)
    V.layout.partition_dir(candidate)
    return candidate


def _safe_remove_workspace_dir(path):
    path = V.layout.require_workspace_path(path)
    if path.is_dir():
        shutil.rmtree(path)


def _super_images_to_process(super_dir):
    images = sorted(glob(os.path.join(super_dir, '*.img')))
    # Auto-detect VAB: if any _a.img exists, treat as VAB
    has_a_suffix = any(Path(img).stem.endswith('_a') for img in images)
    if not has_a_suffix:
        return [(image, partition_name(image)) for image in images if os.path.getsize(image) > 0]

    # 按 canonical 分区名分组 _a / _b / 无后缀
    a_parts = {}
    b_parts = {}
    other_parts = {}
    for image in images:
        p = Path(image)
        stem = p.stem
        if stem.endswith('_a'):
            a_parts[stem[:-2]] = p
        elif stem.endswith('_b'):
            b_parts[stem[:-2]] = p
        else:
            if p.stat().st_size > 0:
                other_parts[stem] = p

    selected = []
    for part in sorted(set(a_parts) | set(b_parts)):
        pa = a_parts.get(part)
        pb = b_parts.get(part)
        size_a = pa.stat().st_size if pa and pa.exists() else 0
        size_b = pb.stat().st_size if pb and pb.exists() else 0

        if size_a == 0 and size_b == 0:
            # 两侧均为 0B，全部删除
            for p in (pa, pb):
                if p and p.exists():
                    p.unlink()
        elif size_a > 0 and size_b > 0:
            # A/B 双槽均有效，保留 _a/_b 各自独立分解
            selected.append((str(pa), f'{part}_a'))
            selected.append((str(pb), f'{part}_b'))
        elif size_a > 0:
            # 仅 A 槽有内容，删除 B，A 重命名为 canonical
            if pb and pb.exists():
                pb.unlink()
            dest = Path(super_dir) / f'{part}.img'
            if dest.exists():
                dest.unlink()
            pa.rename(dest)
            selected.append((str(dest), part))
        else:
            # 仅 B 槽有内容，删除 A，B 重命名为 canonical
            if pa and pa.exists():
                pa.unlink()
            dest = Path(super_dir) / f'{part}.img'
            if dest.exists():
                dest.unlink()
            pb.rename(dest)
            selected.append((str(dest), part))

    # 无后缀的非 VAB 分区直接加入
    for part, image in sorted(other_parts.items()):
        selected.append((str(image), part))

    return selected


def _canonical_stage_source(source, partition, stage_root):
    """Return source directly; metadata names are aligned by partition name."""
    return str(Path(source).resolve())


def _commit_extracted_partition(partition, stage_root, required_metadata, preserve_existing_metadata=False):
    """Direct extraction mode: files are already in WORKSPACE, just verify metadata."""
    config_dir = Path(V.config)
    required = set(required_metadata)
    available = set()
    for name in partition_metadata_names(partition):
        candidate = config_dir / name
        if candidate.exists() and candidate.is_file():
            available.add(name)
    if not required.issubset(available):
        missing = ', '.join(sorted(required - available))
        print(f'> {partition} 缺少必要 metadata: {missing}')
        return False
    return True


def decompress_img(source, distance=None, keep=1):
    """Extract one image directly into WORKSPACE/<partition>/."""
    del keep
    source_type = gettype.gettype(source)
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
    file_type = gettype.gettype(working_source)
    committed = False

    if file_type in ('boot', 'vendor_boot'):
        try:
            _, staged_partition, staged_config = create_partition_stage(partition, 'boot-extract')
            if not boot_utils(working_source, str(staged_partition)):
                raise LayoutError(f'{partition} boot 解包失败')
            if not (staged_partition / 'boot_o.img').is_file():
                raise LayoutError(f'{partition} boot 解包未生成 boot_o.img')
            metadata_path(staged_config, partition, '_kernel.txt').touch()
            committed = _commit_extracted_partition(
                partition, staged_partition, {f'{partition}_kernel.txt'})
        except (LayoutError, OSError) as error:
            print(f'> {partition} boot 分解失败: {error}')
    elif file_type == 'sparse':
        display(f'正在转换: Unsparse Format [{os.path.basename(working_source)}] ...')
        raw_source = imgextractor.ULTRAMAN().APPLE(working_source)
        if raw_source and os.path.isfile(raw_source):
            decompress_img(raw_source, destination)
        else:
            echo('[red][Failed][/]')
        return
    elif file_type == 'ext':
        try:
            _, staged_partition, staged_config = create_partition_stage(partition, 'ext-extract')
            with Console().status(f"[yellow]正在提取{os.path.basename(working_source)}[/]"):
                imgextractor.ULTRAMAN().MONSTER(working_source, str(staged_partition))
            ensure_contexts_file(partition, staged_config)
            committed = _commit_extracted_partition(
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
    elif file_type == 'erofs':
        display(f'正在分解: {os.path.basename(working_source)} <{file_type}>', 3)
        try:
            staged_partition = Path(destination)
            staged_config = Path(V.config)
            staged_config.mkdir(parents=True, exist_ok=True)
            with open(metadata_path(staged_config, partition, '_size.txt'), 'w', encoding='utf-8') as size_file:
                size_file.write(str(os.path.getsize(working_source)))
            # Extract directly to WORKSPACE/ — extract.erofs creates WORKSPACE/<partition>/ and writes metadata to WORKSPACE/config/
            if call(['extract.erofs', '-i', working_source, '-o', V.workspace, '-x']) != 0:
                print('> EROFS 分解失败')
            else:
                # Metadata files land in WORKSPACE/config/ with {partition}_* names already
                if normalize_erofs_metadata(partition, staged_config):
                    committed = _commit_extracted_partition(
                        partition,
                        staged_partition,
                        {
                            f'{partition}_contexts.txt',
                            f'{partition}_fsconfig.txt',
                            f'{partition}_size.txt',
                        },
                    )
        except (LayoutError, OSError) as error:
            print(f'> EROFS 分解失败: {error}')
    elif file_type == 'super':
        display(f'正在分解: {os.path.basename(working_source)} <{file_type}>', 3)
        super_dir = os.path.join(V.workspace, 'super') + os.sep
        try:
            lpunpack.unpack(working_source, super_dir)
        except (Exception, SystemExit) as error:
            print(f'> super 分解失败: {error}')
            return
        if input('> 是否继续分解img [0/1]: ') != '1':
            # 清理 _a/_b 后缀：双槽均非0B保留，单边去后缀
            files = {Path(f).stem: Path(super_dir) / f for f in os.listdir(super_dir) if f.endswith('.img')}
            a_parts = {s[:-2]: p for s, p in files.items() if s.endswith('_a') and p.exists()}
            b_parts = {s[:-2]: p for s, p in files.items() if s.endswith('_b') and p.exists()}
            for part in sorted(set(a_parts) | set(b_parts)):
                pa = a_parts.get(part)
                pb = b_parts.get(part)
                size_a = pa.stat().st_size if pa and pa.exists() else 0
                size_b = pb.stat().st_size if pb and pb.exists() else 0
                if size_a == 0 and size_b == 0:
                    # 两侧均为 0B，全部删除
                    for p in (pa, pb):
                        if p and p.exists():
                            p.unlink()
                elif size_a > 0 and size_b > 0:
                    # A/B 双槽均有效，保留 _a/_b 后缀
                    pass
                elif size_a > 0:
                    # 仅 A 槽有内容，system_b.img(0B) 删，system_a.img → system.img
                    if pb and pb.exists():
                        pb.unlink()
                    dest = Path(super_dir) / f'{part}.img'
                    if dest.exists():
                        dest.unlink()
                    pa.rename(dest)
                else:
                    # 仅 B 槽有内容，system_a.img(0B) 删，system_b.img → system.img
                    if pa and pa.exists():
                        pa.unlink()
                    dest = Path(super_dir) / f'{part}.img'
                    if dest.exists():
                        dest.unlink()
                    pb.rename(dest)
            # 将清理后的 img 文件移至 OUT 目录，再删除 super 临时目录
            out_dir = V.out
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            remaining = [f for f in os.listdir(super_dir) if f.endswith('.img')]
            if remaining:
                for name in sorted(remaining):
                    src = Path(super_dir) / name
                    dst = Path(out_dir) / name
                    if dst.exists():
                        dst.unlink()
                    os.replace(str(src), str(dst))
                    display(f'已输出: {name} -> {out_dir}')
            shutil.rmtree(super_dir, ignore_errors=True)
            return
        for image, image_partition in _super_images_to_process(super_dir):
            decompress_img(image, workspace_partition(image_partition))
        shutil.rmtree(super_dir, ignore_errors=True)
        return

    if committed:
        print('\x1b[1;32m %ds Done\x1b[0m' % (time.time() - s_time))
    elif file_type not in ('boot', 'vendor_boot'):
        echo('[red][Failed][/]')


def _numbered_fragments(source):
    """Return contiguous .1/.2/... fragments or reject an incomplete bundle."""
    source_path = Path(source)
    if source_path.is_symlink() or not source_path.is_file():
        raise LayoutError(f'数据文件无效: {source_path}')
    fragments = {}
    prefix = f'{source_path.name}.'
    for candidate in source_path.parent.iterdir():
        if candidate.name == source_path.name or not candidate.name.startswith(prefix):
            continue
        suffix = candidate.name[len(prefix):]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        if index < 1 or candidate.is_symlink() or not candidate.is_file() or index in fragments:
            raise LayoutError(f'分段数据文件无效: {candidate}')
        fragments[index] = candidate
    if fragments:
        expected = set(range(1, max(fragments) + 1))
        missing = sorted(expected - set(fragments))
        if missing:
            raise LayoutError(f'分段数据缺失: {", ".join(map(str, missing))}')
    return [fragments[index] for index in sorted(fragments)]


def _combine_fragments(source):
    """Combine numbered fragments into a single file in WORKSPACE root, return the path."""
    source_path = Path(source)
    fragments = _numbered_fragments(source_path)
    if not fragments:
        return str(source)
    dest = Path(V.workspace) / source_path.name
    sources = [source_path, *fragments]
    with open(dest, 'xb') as destination_file:
        for index, fragment in enumerate(sources):
            if index:
                display(f'合并: {fragment.name} ...')
            with open(fragment, 'rb') as source_file:
                shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
    return str(dest)


def decompress_dat(transfer, source, distance=None, keep=0):
    """Convert DAT directly: read transfer.list + dat from INPUT, extract to partition."""
    del distance, keep
    if not transfer or not os.path.isfile(transfer):
        print(f'> 未找到 {os.path.basename(source).split(".")[0]}.transfer.list')
        return

    combined = None
    raw_image = None
    try:
        s_time = time.time()
        partition = partition_name(source)
        combined = _combine_fragments(source)
        raw_image = os.path.join(V.workspace, f'{partition}.img')
        display(f"正在分解: {os.path.basename(combined)} ...", 3)
        sdat2img.main(transfer, combined, raw_image)
        if not os.path.isfile(raw_image):
            raise sdat2img.SdatError('未生成 raw image')
        print("\x1b[1;32m [%ds]\x1b[0m" % (time.time() - s_time))
        decompress_img(raw_image, workspace_partition(partition))
    except (LayoutError, OSError, ValueError, sdat2img.SdatError) as error:
        print(f'> DAT 分解失败: {error}')
    finally:
        for f in (combined, raw_image):
            if f and f != source and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def decompress_bro(transfer, source, distance=None, keep=0):
    """Decompress BR directly from INPUT, then run DAT pipeline."""
    del distance, keep
    if not transfer or not os.path.isfile(transfer):
        print(f'> 未找到 {os.path.basename(source).split(".")[0]}.transfer.list')
        return

    combined = None
    staged_dat = None
    try:
        s_time = time.time()
        combined = _combine_fragments(source)
        if not combined.endswith('.br'):
            raise LayoutError(f'BROTLI 文件扩展名无效: {combined}')
        staged_dat = combined[:-3]
        display(f"正在分解: {os.path.basename(source)} ...", 3)
        if call(['brotli', '-df', combined, '-o', staged_dat]) != 0:
            raise LayoutError('brotli 解压失败')
        if not os.path.isfile(staged_dat):
            raise LayoutError('brotli 未生成 new.dat')
        print("\x1b[1;32m [%ds]\x1b[0m" % (time.time() - s_time))
        decompress_dat(transfer, staged_dat)
    except (LayoutError, OSError) as error:
        print(f'> BROTLI 分解失败: {error}')
    finally:
        for f in (combined, staged_dat):
            if f and f != source and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def _human_size(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


def _list_dat_partitions(infile):
    """Scan dat.br/dat files, pair with transfer.list, return sorted list of dicts."""
    items = []
    for part in sorted(infile):
        if not os.path.isfile(part):
            continue
        transfer = os.path.join(os.path.dirname(part), os.path.basename(part).split('.')[0] + '.transfer.list')
        if not os.path.isfile(transfer):
            print(f'> 跳过 {os.path.basename(part)}：未找到 transfer.list')
            continue
        name = partition_name(part)
        size = os.path.getsize(part)
        items.append({"path": part, "transfer": transfer, "partition": name, "size": size})
    return items


def _decompress_single_partition(item, flag):
    """Worker for parallel dat.br/dat decomposition. Returns result dict."""
    name = item["partition"]
    try:
        if flag == 2:
            decompress_bro(item["transfer"], item["path"])
        elif flag == 3:
            decompress_dat(item["transfer"], item["path"])
        return {"partition": name, "success": True, "error": None}
    except (LayoutError, OSError, ValueError, sdat2img.SdatError) as error:
        return {"partition": name, "success": False, "error": str(error)}


def _decompress_payload_images(payload, payload_dir, mode):
    payload_partitions = extract_payload.info(payload)
    if mode == '1':
        print(f"> {YELLOW}包含的所有镜像文件: {CLOSE}\n")
        name_w = max(len(name) for name, _ in payload_partitions) + 4
        size_w = 10
        cols = 2
        for i, (name, size) in enumerate(payload_partitions):
            print(f"  {GREEN}{name}{CLOSE}".ljust(name_w + len(GREEN) + len(CLOSE)),
                  f"{_human_size(size):>{size_w}}", end='')
            if (i + 1) % cols == 0:
                print()
        print()
        name_set = {name for name, _ in payload_partitions}
        partitions = input(
            f"> {RED}根据以上信息输入一个或多个镜像，以空格分开{CLOSE}\n> {MAGENTA}").split()
        for partition in partitions:
            if partition in name_set:
                extract_payload.run(payload, payload_dir, partition)
    else:
        print(f"> {YELLOW}提取【{os.path.basename(payload)}】所有镜像文件:{CLOSE}\n")
        extract_payload.main(payload, payload_dir)

    images = sorted(glob(os.path.join(payload_dir, '*.img')))
    if input('> 是否继续分解img [0/1]: ') != '1':
        # Move .img to OUT
        os.makedirs(V.out, exist_ok=True)
        for image in images:
            dest = os.path.join(V.out, os.path.basename(image))
            if os.path.isfile(dest):
                os.remove(dest)
            shutil.move(image, dest)
            print(f'> {os.path.basename(image)} -> {V.out}')
        return
    # Decompose into partition trees, then clean up .img
    for image in images:
        decompress_img(image, workspace_partition(partition_name(image)))
    for image in images:
        if os.path.isfile(image):
            os.remove(image)


def decompress_bin(infile, outdir=None, flag='1'):
    """Extract payload images directly to WORKSPACE."""
    del outdir
    os.system("clear")
    try:
        payload = _stage_work_source(infile, 'payload')
        _decompress_payload_images(payload, V.workspace, flag)
    except (LayoutError, OSError, AssertionError) as error:
        print(f'> Payload 分解失败: {error}')
        input('> 任意键继续')


def appendf(msg, log):
    if not os.path.isfile(log) and not os.path.exists(log):
        open(log, 'tw', encoding='utf-8').close()
    with open(log, 'w', newline='\n') as file:
        print(msg, file=file)


def safe_extract_tar(archive, destination):
    """Stream regular TAR members into a validated WORKSPACE staging directory."""
    destination = Path(destination).resolve()
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


def _win_partition(source):
    name = os.path.basename(source)
    return ProjectLayout.validate_component(name.split('.', 1)[0], "分区")


def decompress_win(infile_list):
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
            if gettype.gettype(staged_win) in ['erofs', 'ext', 'sparse', 'super', 'boot', 'vendor_boot']:
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


def decompress(infile, flag=4):
    # flag 2/3 (dat.br/dat): selection + parallel execution
    if flag in (2, 3):
        items = _list_dat_partitions(infile)
        if not items:
            print(f'> 未发现可用的 {"dat.br" if flag == 2 else "dat"} 文件')
            return

        # Selection phase
        if not V.JM:
            label = "dat.br" if flag == 2 else "dat"
            print(f'\n> 发现以下 {label} 文件：\n')
            for i, it in enumerate(items, 1):
                print(f'  {YELLOW}[{i:>2}]{CLOSE}\t{GREEN}{os.path.basename(it["path"])}{CLOSE}')
            print(f'\n{YELLOW}请输入要分解的序号（多个用逗号分隔，0跳过）{CLOSE}')
            ans = input('> ').strip()
            if not ans or ans == '0':
                return
            selected = []
            for token in ans.replace('，', ',').split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    idx = int(token)
                    if 1 <= idx <= len(items):
                        selected.append(items[idx - 1])
                    else:
                        print(f'  {RED}无效序号: {idx}{CLOSE}')
                except ValueError:
                    print(f'  {RED}无法解析: {token}{CLOSE}')
            if not selected:
                print('> 未选择任何分区')
                return
        else:
            selected = items

        # Parallel execution phase
        workers = min(len(selected), os.cpu_count() or 2)
        print(f'\n> 并行分解中......\n')
        ok, fail = 0, 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_decompress_single_partition, it, flag): it for it in selected}
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    print(f'  {GREEN}✓{CLOSE} {result["partition"]}')
                    ok += 1
                else:
                    print(f'  {RED}✗{CLOSE} {result["partition"]}: {result["error"]}')
                    fail += 1
        print(f'\n> 分解完成: {ok}/{ok + fail} 成功')
        return

    # flag 4 (img): sequential with per-file confirmation
    for part in sorted(infile):
        if not os.path.isfile(part):
            continue
        try:
            if os.path.basename(part) in ('dsp.img', 'cust.img'):
                continue
            if gettype.gettype(part) not in ('ext', 'sparse', 'erofs', 'super', 'boot', 'vendor_boot'):
                continue
            if not V.JM:
                display(f'是否分解: {os.path.basename(part)} [1/0]: ', 2, '')
                if input() != '1':
                    continue
            decompress_img(part, workspace_partition(partition_name(part)))
        except LayoutError as error:
            print(f'> 跳过 {os.path.basename(part)}: {error}')


def envelop_project():
    project_name = os.path.basename(os.path.normpath(V.project))
    ProjectLayout.validate_component(project_name, "工程")
    V.project = project_name
    V.layout = ProjectLayout(os.path.join(PWD_DIR, project_name)).initialize()
    V.project_dir = str(V.layout.project_dir) + os.sep
    V.input = str(V.layout.input_dir) + os.sep
    V.out = str(V.layout.out_dir) + os.sep
    V.workspace = str(V.layout.workspace_dir) + os.sep
    V.config = str(V.layout.config_dir) + os.sep


def safe_extract_zip(archive, destination):
    """Extract a ZIP only after rejecting members that escape its destination."""
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise LayoutError(f'ZIP 输出目录无效: {destination}')
    destination = destination.resolve()
    for member in archive.infolist():
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
            raise LayoutError(f'ZIP 不支持链接或特殊文件: {member.filename}')
        target = (destination / member.filename).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise LayoutError(f'ZIP 包含越界路径: {member.filename}') from error
    archive.extractall(destination)


def _find_imported_files(import_dir, pattern):
    return sorted(glob(os.path.join(import_dir, '**', pattern), recursive=True))


def extract_zrom(rom):
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
        except (LayoutError, UnsupportedLayoutError) as error:
            input(f'> 无法创建或打开工程: {error}')
            return

        import_dir = workspace_temp('import')
        print(f'> 解压缩: {os.path.basename(rom)} 到 WORKSPACE')
        try:
            safe_extract_zip(archive, import_dir)
        except LayoutError as error:
            input(f'> ROM ZIP 解压失败: {error}')
            return

    payload_files = _find_imported_files(import_dir, 'payload.bin')
    if payload_files:
        decompress_bin(
            payload_files[0],
            flag=input(f'> {RED}选择提取方式:  [0]全盘提取  [1]指定镜像{CLOSE} >> '),
        )
        shutil.rmtree(import_dir, ignore_errors=True)
        menu_main()
        return

    if _find_imported_files(import_dir, '*.new.dat.br'):
        infile, able = _find_imported_files(import_dir, '*.new.dat.br'), 2
    elif _find_imported_files(import_dir, '*.new.dat'):
        infile, able = _find_imported_files(import_dir, '*.new.dat'), 3
    elif _find_imported_files(import_dir, '*.img'):
        infile, able = _find_imported_files(import_dir, '*.img'), 4
    else:
        input('> 仅支持含有payload.bin/*.new.dat/*.new.dat.br/*.img的zip固件')
        shutil.rmtree(import_dir, ignore_errors=True)
        menu_main()
        return

    quiet()
    decompress(infile, able)
    shutil.rmtree(import_dir, ignore_errors=True)
    menu_main()


_RESERVED_MENU_IDS = {22, 44, 66, 88}


def lists_project(dTitle, sPath, flag):
    i = 0
    V.dict0 = {i: dTitle}
    if flag == 0:
        for obj in glob(sPath):
            if os.path.isdir(obj):
                i += 1
                while i in _RESERVED_MENU_IDS:
                    i += 1
                V.dict0[i] = obj

    elif flag == 1:
        for obj in glob(sPath):
            if os.path.isfile(obj):
                i += 1
                while i in _RESERVED_MENU_IDS:
                    i += 1
                V.dict0[i] = obj

    elif flag == 2:
        for obj in glob(sPath):
            if os.path.isdir(obj):
                if os.path.isfile(obj + os.sep + "run.sh"):
                    i += 1
                    while i in _RESERVED_MENU_IDS:
                        i += 1
                    V.dict0[i] = obj

    e = 1
    print("-------------------------------------------------------\n")
    for (key, value) in V.dict0.items():
        print(f"  \x1b[0;3{e}m[{key}]\x1b[0m - \x1b[0;3{e + 4}m{os.path.basename(value)}\x1b[0m")
        e = 2

    print("\n-------------------------------------------------------")
    if flag == 0:
        print("\x1b[0;35m  [22] - 删除项目      [44] - 工具设置\n  [66] - 退出工具      [88] - 工具信息  \x1b[0m\n")

    if flag == 2:
        print("\x1b[0;35m  [33] - 安装         [44] - 删除         [88] - 退出  \x1b[0m\n")




def creat_project():
    os.system("clear")
    print("\x1b[1;31m> 新建工程:\x1b[0m\n")
    creat_name = input("  输入名称【不能有空格、特殊符号】: DNA_").strip().rstrip("\\").replace(" ", "_")
    if not creat_name:
        return

    V.project = "DNA_" + creat_name
    try:
        ProjectLayout.validate_component(V.project, "工程")
    except LayoutError as error:
        input(f"> 工程名称无效: {error}")
        return
    if os.path.exists(os.path.join(PWD_DIR, V.project)):
        input(f"\x1b[0;31m\n 工程目录< \x1b[0;32m{V.project} \x1b[0;31m>已存在, 回车返回 ...\x1b[0m\n")
        return

    try:
        envelop_project()
    except (LayoutError, UnsupportedLayoutError) as error:
        input(f"> 创建工程失败: {error}")
        return
    return True


def menu_once():
    load_setup_json()
    while True:
        os.system("clear")
        print("\x1b[0;33m> 工程列表\x1b[0m")
        lists_project("新建工程", "DNA_*", 0)
        choice = input("> 选择: ")
        if not choice or not choice.isdigit():
            continue
        if int(choice) == 66:
            sys.exit()
        elif int(choice) == 22:
            if V.dict0:
                which = input("> 输入序号进行删除: ")
                if not which.isdigit():
                    continue
                elif int(which) > 0:
                    if int(which) < len(V.dict0):
                        if input(
                                f"\x1b[0;31m> 是否删除 \x1b[0;34mNo.{which} \x1b[0;31m工程: \x1b[0;32m{os.path.basename(V.dict0[int(which)])}\x1b[0;31m [0/1]:\x1b[0m ") == "1":
                            if os.path.isdir(V.dict0[int(which)]):
                                rmdire(V.dict0[int(which)])
                                continue
                    input(f"> Number {which} Error !")
        elif int(choice) == 44:
            env_setup()
            load_setup_json()
        elif int(choice) == 88:
            tool_info()
        elif int(choice) == 0:
            if creat_project():
                menu_main()
            continue
        elif 0 < int(choice) < len(V.dict0):
            V.project = V.dict0[int(choice)]
            try:
                envelop_project()
            except (LayoutError, UnsupportedLayoutError) as error:
                input(f'> 无法打开工程: {error}')
                continue
            menu_main()
            continue
        else:
            input(f"> Number \x1b[0;33m{choice}\x1b[0m enter error !")


def menu_super():
    """Interactive super image repack: select partitions from INPUT, choose type and format."""
    os.system("clear")
    print(f'\x1b[1;36m> 合成 super.img\x1b[0m')
    print(f'> 请将需要打包的 .img 文件放入 INPUT 目录')
    print(f'> INPUT: {V.input}')
    input('> 准备好后按回车继续...')

    # Scan INPUT for .img files
    images = sorted(glob(os.path.join(V.input, '*.img')))
    if not images:
        print('> INPUT 目录下未发现 .img 文件')
        input('> 任意键返回')
        return

    print(f'\n发现以下镜像文件：')
    for i, img in enumerate(images, 1):
        print(f'  [{i}] {os.path.basename(img)}')

    # Ask user to select each image, strip _a/_b suffix for partition name
    selected = []
    for img in images:
        stem = Path(img).stem
        # Strip _a/_b suffix to get canonical partition name (MIO behavior)
        if stem.endswith('_a') or stem.endswith('_b'):
            name = stem[:-2]
        else:
            name = stem
        choice = input(f'\n是否要打包 {os.path.basename(img)} ? [1:YES/0:NO]: ')
        if choice == '1':
            selected.append((name, img))
            print(f'  ✓ {name}')
        else:
            print(f'  ✗ {name} (跳过)')

    if not selected:
        print('\n> 未选择任何镜像')
        input('> 任意键返回')
        return

    # Ask super type with validation
    while True:
        super_type = input('\n打包类型 [0:Aonly/1:AB/2:VAB]: ')
        if super_type in ('0', '1', '2'):
            super_type = int(super_type)
            break
        print('> 无效输入，请输入 0、1 或 2')

    # Ask sparse format with validation
    while True:
        super_sparse = input('合成 SUPER 镜像格式 [1:SPARSE/0:RAW]: ')
        if super_sparse in ('0', '1'):
            super_sparse = int(super_sparse)
            break
        print('> 无效输入，请输入 0 或 1')

    type_names = {0: 'A-only', 1: 'A/B', 2: 'Virtual A/B'}
    print(f'\n打包类型: {type_names[super_type]}')
    print(f'输出格式: {"SPARSE" if super_sparse else "RAW"}')
    print(f'包含分区: {", ".join(name for name, _ in selected)}')

    with CoastTime():
        repack_super(selected, super_type, super_sparse)


def menu_modules():
    while True:
        os.system("clear")
        print("\x1b[0;33m> 插件列表\x1b[0m")
        lists_project("返回上级", MOD_DIR + "DNA_*", 2)
        choice = input("> 选择: ")
        if not choice.isdigit():
            continue
        if int(choice) == 88:
            sys.exit()
        elif int(choice) == 33:
            extract_zrom(input("请输入插件路径："))
        elif int(choice) == 44:
            if V.dict0:
                which = input("> 输入序号进行删除: ")
                if int(which) == 0 or not which.isdigit():
                    continue
                if int(which) <= len(V.dict0):
                    if input(
                            f"\x1b[0;31m> 是否删除 \x1b[0;34mNo.{which} \x1b[0;31m插件: \x1b[0;32m{os.path.basename(V.dict0[int(which)])}\x1b[0;31m [0/1]:\x1b[0m ") == "1":
                        if os.path.isdir(V.dict0[int(which)]):
                            rmdire(V.dict0[int(which)])
                            continue
                        else:
                            input(f"> Number {which} Error !")
        elif int(choice) == 0:
            return
        if 0 < int(choice) < len(V.dict0):
            os.system("clear")
            print(f"\x1b[1;31m> 执行插件:\x1b[0m {os.path.basename(V.dict0[int(choice)])}\n")
            if os.path.isfile(shell_sub := (V.dict0[int(choice)] + os.sep + "run.sh")):
                call(['busybox', 'bash', shell_sub, V.workspace.replace(os.sep, '/')])
            input('> 任意键继续')
        else:
            print(f"> Number \x1b[0;33m{choice}\x1b[0m enter error !")


def quiet():
    V.JM = input('> 是否开启静默 [0/1]: ') == '1'


def tool_info():
    os.system("clear")
    print(f"""
\x1b[1;36m{'=' * 50}
  A.R.T - Android ROM Tool
{'=' * 50}\x1b[0m

\x1b[1;33m项目链接:\x1b[0m
  GitHub: https://github.com/ELF-RC/A.R.T

\x1b[1;33m原工具开发者:\x1b[0m
  ColdWindScholar (3590361911@qq.com)

\x1b[1;33m工具开发者:\x1b[0m
  ELF-RC (3580977309@qq.com)

\x1b[1;33m二进制文件开发者:\x1b[0m
  AOSP (Apache-2.0)        - make_ext4fs, img2simg, lpmake
  erofs-utils (GPL-2.0)    - extract.erofs, mkfs.erofs
  e2fsprogs (GPL-2.0)      - mke2fs, e2fsdroid, e2fsck, resize2fs
  Magisk (GPL-3.0)         - magiskboot
  BusyBox (GPL-2.0)        - busybox, cpio
  Google (Apache-2.0)      - brotli
  Meta (BSD-3-Clause)      - zstd
  dtc (GPL-2.0)            - dtc
  avbroot (GPL-3.0)        - avbroot (chenxiaolong)
  avbroot-pro-max (GPL-3.0) - avbroot Pro Max (ChuiShui233)

\x1b[1;33m协议:\x1b[0m
  本工具使用 AGPL-3.0 协议

\x1b[1;33m感谢所有开源贡献者！\x1b[0m
{'=' * 50}
""")
    input('> 任意键返回')


menu_actions = {
    55: lambda: input(
        "Github: https://github.com/ColdWindScholar/D.N.A3/\nWrote By ColdWindScholar (3590361911@qq.com)"),
    66: sys.exit,
    88: tool_info,
    8: menu_modules,
    7: menu_super
}


def menu_main():
    """Run the project menu iteratively so long sessions do not grow the call stack."""
    V.JM = True
    while True:
        os.system("clear")
        print(f'\x1b[1;36m> 当前工程: \x1b[0m{V.project}')
        print('-------------------------------------------------------------\n')
        print('\x1b[0;31m\t   0 > 返回主菜单            66 > 退出工具\x1b[0m\n')
        print('\n')
        print('\x1b[0;32m\t   1 > 分解 [bin]            2 > 分解 [dat.br]        \x1b[0m\n')
        print('\x1b[0;36m\t   3 > 分解 [dat]            4 > 分解 [img]\x1b[0m\n')
        print('\x1b[0;33m\t   5 > 分解 [win]            6 > 分解 [super]\x1b[0m\n')
        print('\n')
        print('\x1b[0;35m\t   7 > 合成 [super]          8 > 插件 [sub]\x1b[0m\n')
        print('\x1b[0;34m\t   9 > 合成 [img]           10 > 合成 [dat]\x1b[0m\n')
        print('\x1b[0;32m\t  11 > 合成 [dat.br]        12 > 更多 [more]\x1b[0m\n')
        print('-------------------------------------------------------------')
        option = input(f'> {RED}输入序号{CLOSE} >> ')
        if not option.isdigit():
            input('> 输入序号数字')
            continue

        option = int(option)
        valid_options = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 55, 66, 88}
        if option not in valid_options:
            input(f'> 无效序号: {option}')
            continue

        if option == 0:
            return
        if option in menu_actions:
            menu_actions[option]()
        elif option == 1:
            infile = V.input + 'payload.bin'
            if not os.path.exists(infile):
                input("未发现Payload.Bin")
            else:
                decompress_bin(infile, V.input,
                               input(f'> {RED}选择提取方式:  [0]全盘提取  [1]指定镜像{CLOSE} >> '))
        elif int(option) in [2, 3, 4]:
            quiet()
            decompress(glob(V.input + {2: "*.br", 3: "*.new.dat", 4: "*.img"}[int(option)]), int(option))
        elif int(option) == 5:
            infile = glob(V.input + '*.win*')
            for i in glob(V.input + '*.win'):
                infile.append(i)
            quiet()
            decompress_win(list(set(sorted(infile))))
        elif int(option) == 6:
            from pys import lpunpack2
            lpunpack2.main()
            input('> 任意键继续')
            continue
        elif int(option) == 12:
            from pys import more
            more.main()
            continue
        elif int(option) in [9, 10, 11]:
            quiet()
            if int(option) == 9:
                for file in glob(V.config + '*_kernel.txt'):
                    f_basename = os.path.basename(file).rsplit('_', 1)[0]
                    source = workspace_partition(f_basename)
                    if os.path.isdir(source):
                        if not V.JM:
                            display(f'是否合成: {f_basename}.img [1/0]: ', end='')
                            if input() != '1':
                                continue
                        boot_utils(source, V.out, 2)
            for file in glob(V.config + '*_contexts.txt'):
                f_basename = os.path.basename(file).rsplit('_', 1)[0]
                source = workspace_partition(f_basename)
                if os.path.isdir(source):
                    fsconfig = V.config + f_basename + '_fsconfig.txt'
                    contexts = V.config + f_basename + '_contexts.txt'
                    infojson = V.config + f_basename + '_info.txt'
                    if not os.path.isfile(infojson):
                        infojson = None
                    if os.path.isfile(contexts) and os.path.isfile(fsconfig):
                        if not V.JM:
                            txts = {9: "img", 10: "new.dat", 11: "new.dat.br"}
                            display(f'是否合成: {f_basename}.{txts.get(int(option), ".new.dat.br")} [1/0]: ', end='')
                            if input() != '1':
                                continue
                        recompress(source, fsconfig, contexts, infojson, int(option))
        else:
            input(f'\x1b[0;33m{option}\x1b[0m enter error !')
            continue
        input('> 任意键继续')
