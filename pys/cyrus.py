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
                         "mke2fs", "e2fsdroid", "mkfs.erofs", "lpmake", "extract.erofs", "magiskboot"]

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


def set_default_env_setup():
    properties = {
        'IS_VAB': "1",
        'REPACK_EROFS_IMG': "1",
        'REPACK_TO_RW': "0",
        'RESIZE_IMG': "0",
        'RESIZE_EROFSIMG': "1",
        'REPACK_SPARSE_IMG': "0",
        'REPACK_BR_LEVEL': "3",
        'SUPER_SIZE': "9126805504",
        'GROUP_NAME': "qti_dynamic_partitions",
        'SUPER_SPARSE': "1",
        'UTC': "LIVE",
        'UNPACK_SPLIT_DAT': "15"}
    with open(SETUP_JSON, 'w', encoding='utf-8') as ss:
        json.dump(properties, ss, ensure_ascii=False, indent=4)


def validate_default_env_setup(setup_manifest):
    for k in ('IS_VAB', 'REPACK_EROFS_IMG', 'REPACK_SPARSE_IMG', 'REPACK_TO_RW',
              'SUPER_SPARSE', 'RESIZE_IMG'):
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
    question_list = {
        '是否虚拟AB分区[1/0]': "IS_VAB",
        '合成镜像类型[0:EXT4/1:EROFS]': "REPACK_EROFS_IMG",
        '合成镜像格式[0:RAW/1:SPARSE]': "REPACK_SPARSE_IMG",
        '合成SUPER镜像格式[1:SPARSE/0:RAW]': "SUPER_SPARSE",
        '合成EXT4动态分区状态[0:RO/1:RW]': "REPACK_TO_RW",
        '合成EXT4压缩分区空间[0/1]': "RESIZE_IMG",
        '合成EROFS压缩算法[0:NO/1:LZ4HC/2:LZ4]': "RESIZE_EROFSIMG",
        'EROFS压缩等级[1]': "EROFS_LEVEL",
        'EROFS旧内核兼容[0/1]': "EROFS_OLD_KERNEL",
        '压缩BROTLI等级[0-9|3]': "REPACK_BR_LEVEL",
        '动态分区簇名称[qti_dynamic_partitions]': "GROUP_NAME",
        '动态SUPER分区总大小[9126805504]': "SUPER_SIZE",
        '自定义UTC时间戳[live]': "UTC",
        '分段DAT/IMG支持个数[15]': "UNPACK_SPLIT_DAT"}
    while True:
        os.system("clear")
        print(f"\n> {GREEN}设置文件{CLOSE}: {SETUP_JSON.replace(PWD_DIR, '')}")
        i = 1
        data1 = {}
        with open(SETUP_JSON, 'r', encoding='utf-8') as ss:
            data = json.load(ss)
        for (name, value) in question_list.items():
            print(f"{YELLOW}[{'0' if i < 10 else ''}{i}]{CLOSE}\t{BOLD}{name}{CLOSE}: {GREEN}{data[value]}{CLOSE}")
            data1[f"{'0' if i < 10 else ''}{i}"] = name
            i += 1
        sum_ = input(f"\n请输入你要更改的序列，输入{YELLOW}00{CLOSE}为返回：")
        if sum_ in ["00", "0"]:
            return
        if sum_ not in data1.keys():
            continue
        data[question_list[data1[sum_]]] = input(data1[sum_] + "：")
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


def _super_partitions_in_out():
    patterns = ('system*', 'product*', 'vendor*', 'odm*', 'my_*')
    partitions = set()
    for pattern in patterns:
        for image in glob(os.path.join(V.out, f'{pattern}.img')):
            label = Path(image).stem
            if label == 'super':
                continue
            if label.endswith(('_a', '_b')):
                label = label[:-2]
            try:
                partitions.add(ProjectLayout.validate_component(label, '分区'))
            except LayoutError:
                continue
    return sorted(partitions)


def _prepare_super_image(source):
    """Return a raw image path for lpmake. Converts sparse in-place if needed."""
    if gettype.gettype(source) != 'sparse':
        return source
    raw_image = imgextractor.ULTRAMAN().APPLE(source)
    if not raw_image or not os.path.isfile(raw_image):
        raise LayoutError(f'无法转换 sparse 镜像: {source}')
    return raw_image


def repack_super():
    parts = _super_partitions_in_out()
    if not parts:
        input('> 未发现 OUT 文件夹下可用于合成 super 的镜像文件')
        return

    super_output = os.path.join(V.out, 'super.img')
    group_name = V.SETUP_MANIFEST['GROUP_NAME']
    super_size = V.SETUP_MANIFEST['SUPER_SIZE']
    argvs = [
        'lpmake',
        '--metadata-size', '65536',
        '--super-name', 'super',
        '--device', f'super:{super_size}',
    ]
    image_parts = []

    try:
        if V.SETUP_MANIFEST['IS_VAB'] == '1':
            argvs.extend(['--metadata-slots', '3', '--virtual-ab', '-F'])
            for part in parts:
                source = os.path.join(V.out, f'{part}.img')
                if not os.path.isfile(source):
                    source = os.path.join(V.out, f'{part}_a.img')
                if not os.path.isfile(source):
                    continue
                image_a = _prepare_super_image(source)
                image_size_a = imgextractor.ULTRAMAN().LEMON(image_a)
                argvs.extend([
                    '--partition', f'{part}_a:readonly:{image_size_a}:{group_name}_a',
                    '--image', f'{part}_a={image_a}',
                    '--partition', f'{part}_b:readonly:0:{group_name}_b',
                ])
                image_parts.append(part)
        else:
            argvs.extend(['--metadata-slots', '2'])
            for part in parts:
                source_a = os.path.join(V.out, f'{part}_a.img')
                source_b = os.path.join(V.out, f'{part}_b.img')
                if not os.path.isfile(source_a):
                    source_a = os.path.join(V.out, f'{part}.img')
                if not (os.path.isfile(source_a) and os.path.isfile(source_b)):
                    continue
                image_a = _prepare_super_image(source_a)
                image_b = _prepare_super_image(source_b)
                size_a = imgextractor.ULTRAMAN().LEMON(image_a)
                size_b = imgextractor.ULTRAMAN().LEMON(image_b)
                argvs.extend([
                    '--partition', f'{part}_a:readonly:{size_a}:{group_name}_a',
                    '--image', f'{part}_a={image_a}',
                    '--partition', f'{part}_b:readonly:{size_b}:{group_name}_b',
                    '--image', f'{part}_b={image_b}',
                ])
                image_parts.append(part)
    except (LayoutError, OSError) as error:
        print(f'> 准备 super 镜像失败: {error}')
        return

    if not image_parts:
        input('> 未发现与当前 A/B 设置匹配的 OUT 分区镜像')
        return
    if V.SETUP_MANIFEST['SUPER_SPARSE'] == '1':
        argvs.append('--sparse')
    argvs.extend([
        '--group', f'{group_name}_a:{super_size}',
        '--group', f'{group_name}_b:{super_size}',
        '--output', super_output,
    ])
    display(
        f'重新合成: super.img <Size:{super_size}|Vab:{V.SETUP_MANIFEST["IS_VAB"]}|'
        f'Sparse:{V.SETUP_MANIFEST["SUPER_SPARSE"]}>'
    )
    display(f"包含分区：{'|'.join(image_parts)}")
    with CoastTime():
        result = call(argvs)
    if result != 0 or not os.path.isfile(super_output):
        print('> super.img 合成失败；OUT 中的现有分区镜像未被修改')
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
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 8:
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
            if V.SETUP_MANIFEST['REPACK_SPARSE_IMG'] == '1' or flag > 8:
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
            if V.SETUP_MANIFEST["IS_VAB"] == "1":
                for partition in ('system_a', 'system_ext_a', 'product_a', 'vendor_a', 'odm_a'):
                    CONTENT += f"resize {partition} 2\n"
            else:
                for partition in ('system', 'system_ext', 'product', 'vendor', 'odm'):
                    for slot in ('_a', '_b'):
                        CONTENT += f"resize {partition}{slot} 2\n"
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
        if flag > 8:
            display(f"重新生成: {label}.new.dat ...", 3)
            img2sdat.main(distance, V.out, 4, label)
            newdat = V.out + label + ".new.dat"
            if os.path.isfile(newdat):
                print(" Done")
                os.remove(distance)
                if flag == 10:
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
        if not ramdisk.is_file():
            print("Unpack Done!")
            return True

        comp = gettype.gettype(str(ramdisk))
        print(f"Ramdisk is {comp}")
        (work_dir / 'comp').write_text(comp, encoding='utf-8')
        if comp != 'unknow':
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
        if call(['cpio', '-i', '-d', '-F', 'ramdisk.cpio', '-D', 'ramdisk']) != 0:
            print("Unpack Ramdisk Fail...")
            return False
        return True
    except OSError as error:
        print(f"Unpack {file} Fail: {error}")
        return False
    finally:
        os.chdir(original_dir)


def dboot(infile, dist):
    or_dir = os.getcwd()
    flag = ''
    if not os.path.exists(infile):
        print(f"Cannot Find {infile}...")
        return
    if os.path.isdir(infile + os.sep + "ramdisk"):
        try:
            os.chdir(infile + os.sep + "ramdisk")
        except Exception as e:
            print("Ramdisk Not Found.. %s" % e)
            return
        cpio = gettype.findfile('cpio',
                                BIN_PATH).replace(
            '\\', "/")
        call(exe="busybox ash -c \"find | sed 1d | %s -H newc -R 0:0 -o -F ../ramdisk-new.cpio\"" % cpio, sp=1,
             shstate=True)
        os.chdir(infile)
        with open("comp", "r", encoding='utf-8') as compf:
            comp = compf.read()
        print("Compressing:%s" % comp)
        if comp != "unknow":
            if call(['magiskboot', f'compress={comp}', 'ramdisk-new.cpio']) != 0:
                print("Pack Ramdisk Fail...")
                os.remove("ramdisk-new.cpio")
                return
            else:
                print("Pack Ramdisk Successful..")
                try:
                    os.remove("ramdisk.cpio")
                except Exception:
                    pass
                os.rename("ramdisk-new.cpio.%s" % comp.split('_')[0], "ramdisk.cpio")
        else:
            print("Pack Ramdisk Successful..")
            os.remove("ramdisk.cpio")
            os.rename("ramdisk-new.cpio", "ramdisk.cpio")
        if comp == "cpio":
            flag = "-n"
    else:
        os.chdir(infile)
    repack_args = ['magiskboot', 'repack', flag, os.path.join(infile, "boot_o.img")]
    if call(repack_args) != 0:
        print("Pack boot Fail...")
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
    if V.SETUP_MANIFEST['IS_VAB'] != '1':
        return [(image, partition_name(image)) for image in images if os.path.getsize(image) > 0]

    selected = []
    for image in images:
        stem = os.path.basename(image).rsplit('.', 1)[0]
        if stem.endswith('_b') or os.path.getsize(image) == 0:
            continue
        if stem.endswith('_a'):
            partition = stem[:-2]
            canonical = os.path.join(super_dir, f'{partition}.img')
            shutil.copy2(image, canonical)
            selected.append((canonical, partition))
        else:
            selected.append((image, partition_name(image)))
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
        super_dir = workspace_temp('super')
        try:
            lpunpack.unpack(working_source, super_dir)
        except (Exception, SystemExit) as error:
            print(f'> super 分解失败: {error}')
            return
        if input('> 是否继续分解img [0/1]: ') != '1':
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


def _decompress_payload_images(payload, payload_dir, mode):
    payload_partitions = extract_payload.info(payload).split()
    if mode == '1':
        print(f"> {YELLOW}包含的所有镜像文件: {CLOSE}\n")
        print(' '.join(payload_partitions))
        partitions = input(
            f"> {RED}根据以上信息输入一个或多个镜像，以空格分开{CLOSE}\n> {MAGENTA}").split()
        for partition in partitions:
            if partition in payload_partitions:
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
    for part in sorted(infile):
        if not os.path.isfile(part):
            continue
        try:
            if flag < 4:
                transfer = os.path.join(os.path.dirname(part), os.path.basename(part).split('.')[0] + '.transfer.list')
                if not os.path.isfile(transfer):
                    print(f'> 跳过 {os.path.basename(part)}：未找到 transfer.list')
                    continue
                if not V.JM:
                    display(f'是否分解: {os.path.basename(part)} [1/0]: ', 2, '')
                    if input() != '1':
                        continue
                if flag == 2:
                    decompress_bro(transfer, part)
                elif flag == 3:
                    decompress_dat(transfer, part)
                continue

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
    """Directly run super image repack."""
    with CoastTime():
        repack_super()
    input('> 任意键继续')


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
    7: menu_modules,
    6: menu_super
}


def menu_main():
    """Run the project menu iteratively so long sessions do not grow the call stack."""
    V.JM = True
    while True:
        os.system("clear")
        print(f'\x1b[1;36m> 当前工程: \x1b[0m{V.project}')
        print('-------------------------------------------------------\n')
        print('\x1b[0;31m\t  0> 选择[etc]          1> 分解[bin]\x1b[0m\n')
        print('\x1b[0;32m\t  2> 分解[bro]          3> 分解[dat]\x1b[0m\n')
        print('\x1b[0;36m\t  4> 分解[img]          5> 分解[win]\x1b[0m\n')
        print('\x1b[0;33m\t  6> 合成super          7> 插件[sub]\x1b[0m\n')
        print('\x1b[0;35m\t  8> 合成[img]          9> 合成[dat]\x1b[0m\n')
        print('\x1b[0;34m\t  10> 合成[bro]        66> 退出工具\x1b[0m\n')
        print('-------------------------------------------------------')
        option = input(f'> {RED}输入序号{CLOSE} >> ')
        if not option.isdigit():
            input('> 输入序号数字')
            continue

        option = int(option)
        valid_options = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 55, 66, 88}
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
        elif int(option) in [8, 9, 10]:
            quiet()
            if int(option) == 8:
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
                            txts = {8: "img", 9: "new.dat", 10: "new.dat.br"}
                            display(f'是否合成: {f_basename}.{txts.get(int(option), ".new.dat.br")} [1/0]: ', end='')
                            if input() != '1':
                                continue
                        recompress(source, fsconfig, contexts, infojson, int(option))
        else:
            input(f'\x1b[0;33m{option}\x1b[0m enter error !')
            continue
        input('> 任意键继续')
