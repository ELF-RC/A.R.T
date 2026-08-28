"""Configuration and setup management extracted from cyrus.py."""

import json
import os
import re
import sys

from pys.utils import (
    PWD_DIR, BIN_PATH, V, GREEN, CYAN, YELLOW, BOLD, CLOSE, display, call,
)

SETUP_JSON = PWD_DIR + "local/set/setup.json"

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
    'BOOT_SKIP_RAMDISK': "0",
}


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


def load_setup_json():
    with open(SETUP_JSON, "r", encoding="utf-8") as manifest_file:
        V.SETUP_MANIFEST = json.load(manifest_file)
    set_default_env_setup()
    validate_default_env_setup(V.SETUP_MANIFEST)
    with open(SETUP_JSON, "w", encoding="utf-8") as f:
        json.dump(V.SETUP_MANIFEST, f, indent=4)


def env_setup():
    """Interactive settings editor."""
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
