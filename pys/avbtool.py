"""AVBTOOL - 镜像签名与VBMeta工具"""

import os
import shutil
import subprocess

from pys.cyrus import V, BIN_PATH

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
CYAN = '\x1b[1;36m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'

AVBTOOL = os.path.join(BIN_PATH, "avbtool")


def _run(args):
    """Run avbtool, print output, return True on success."""
    result = subprocess.run(
        [AVBTOOL] + args,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, end='')
    return result.returncode == 0


def _signkey_dir():
    return V.layout.ota_signkey_dir if V.layout else None


def _avb_key_path():
    d = _signkey_dir()
    if not d or not d.is_dir():
        return None
    p = d / 'avb.key'
    return str(p) if p.is_file() else None


def _pass_file_path():
    d = _signkey_dir()
    if not d or not d.is_dir():
        return None
    p = d / 'passphrase.txt'
    return str(p) if p.is_file() else None


def _select_file(prompt="选择文件"):
    while True:
        path = input(f'\n  {prompt}（绝对路径）>> ').strip()
        if not path:
            return None
        if os.path.isfile(path):
            return path
        print(f'  {RED}> 文件不存在: {path}{CLOSE}')


def cmd_info_image():
    """[01] 查看镜像信息"""
    img = _select_file('选择要查看的镜像')
    if not img:
        input('> 按回车继续')
        return
    _run(['info_image', '--image', img])
    input('> 按回车继续')


def _trim_trailing_zeros(img):
    """截掉镜像尾部全零数据，返回截断后大小；若无零数据则返回原大小。"""
    size = os.path.getsize(img)
    with open(img, 'rb') as f:
        chunk_size = 65536
        offset = size - 1
        while offset >= 0:
            read_start = max(0, offset - chunk_size + 1)
            f.seek(read_start)
            chunk = f.read(offset - read_start + 1)
            for i in range(len(chunk) - 1, -1, -1):
                if chunk[i] != 0:
                    real_end = read_start + i + 1
                    trimmed = (real_end + 4095) // 4096 * 4096
                    if trimmed < size:
                        with open(img, 'r+b') as bf:
                            bf.truncate(trimmed)
                    return trimmed
            offset = read_start - 1
        return 0


def _sign_footer(cmd_name, img, trim_zeros=True):
    """Common logic for add_hash_footer and add_hashtree_footer。"""
    key_path = _avb_key_path()
    if not key_path:
        print(f'\n{RED}> 未找到 avb.key，请先生成密钥{CLOSE}')
        input('> 按回车继续')
        return

    # 拷贝原文件为 x_signed.img，在副本上操作
    base, ext = os.path.splitext(img)
    out_img = f'{base}_signed{ext}'
    shutil.copy2(img, out_img)

    orig_size = os.path.getsize(out_img)
    if trim_zeros:
        trimmed_size = _trim_trailing_zeros(out_img)
        if trimmed_size == 0:
            print(f'\n{RED}> 镜像文件全为零，无法签名{CLOSE}')
            os.remove(out_img)
            input('> 按回车继续')
            return
        img_size = os.path.getsize(out_img)
        print(f'\n  {orig_size} bytes → {img_size}')
    else:
        img_size = orig_size

    # 分区名：用户输入，留空则用文件名去掉.img
    part_name = input('\n  分区名（留空用文件名）>> ').strip() or os.path.splitext(os.path.basename(img))[0]

    pass_path = _pass_file_path()
    # partition_size：用户输入则直接用，否则自动计算
    ps_input = input('  分区大小（字节），留空自动计算 >> ').strip()
    import math
    if ps_input:
        v = int(ps_input)
        aligned_ps = str(v) if v % 4096 == 0 else str((v + 4095) // 4096 * 4096)
    elif cmd_name == 'add_hashtree_footer':
        # --calc_max_image_size 比例估算
        trial_ps = (img_size + 64 * 1024 * 1024 + 4095) // 4096 * 4096
        r = subprocess.run([AVBTOOL, 'add_hashtree_footer',
            '--image', out_img, '--partition_name', part_name,
            '--algorithm', 'SHA256_RSA4096', '--key', key_path,
            '--partition_size', str(trial_ps),
            '--do_not_generate_fec'] + (['--pass-file', pass_path] if pass_path else []) +
            ['--calc_max_image_size'],
            capture_output=True, text=True)
        max_img = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
        aligned_ps = str(math.ceil(img_size / max_img * trial_ps / 4096) * 4096)
        r2 = subprocess.run([AVBTOOL, 'add_hashtree_footer',
            '--image', out_img, '--partition_name', part_name,
            '--algorithm', 'SHA256_RSA4096', '--key', key_path,
            '--partition_size', aligned_ps,
            '--do_not_generate_fec'] + (['--pass-file', pass_path] if pass_path else []) +
            ['--calc_max_image_size'],
            capture_output=True, text=True)
        max_img2 = int(r2.stdout.strip()) if r2.stdout.strip().isdigit() else 0
        if max_img2 < img_size:
            aligned_ps = str(math.ceil(img_size / max_img2 * int(aligned_ps) / 4096) * 4096)
    else:
        aligned_ps = str((img_size + 69632 + 4095) // 4096 * 4096)

    # 构建 args
    args = [cmd_name, '--image', out_img, '--partition_name', part_name,
            '--algorithm', 'SHA256_RSA4096', '--key', key_path,
            '--partition_size', aligned_ps]
    if cmd_name == 'add_hashtree_footer':
        args.append('--do_not_generate_fec')
    if pass_path:
        args.extend(['--pass-file', pass_path])
    # rollback_index：hash footer 用户输入，hashtree 不传
    if cmd_name == 'add_hash_footer':
        rollback = input('  回滚索引（默认0）>> ').strip() or '0'
        args.extend(['--rollback_index', rollback])

    print(f'  输出: {out_img}')
    ok = _run(args)
    if ok:
        print('\n  Patch has been completed.')
    else:
        print(f'\n  {RED}> 失败{CLOSE}')
        os.remove(out_img)
    input('> 按回车继续')


def cmd_add_hash_footer():
    """[02] 为 boot/recovery/dtbo 等小分区添加 hash footer"""
    img = _select_file('选择要签名的镜像')
    if not img:
        input('> 按回车继续')
        return
    _sign_footer('add_hash_footer', img, trim_zeros=True)


def cmd_add_hashtree_footer():
    """[03] 为 system/vendor 等大分区添加 hashtree footer"""
    img = _select_file('选择要签名的镜像')
    if not img:
        input('> 按回车继续')
        return
    _sign_footer('add_hashtree_footer', img, trim_zeros=False)


def cmd_verify_image():
    """[04] 验证镜像签名"""
    img = _select_file('选择要验证的镜像')
    if not img:
        input('> 按回车继续')
        return

    print(f'\n  验证: {img}')
    # 不传 --key，让 avbtool 从镜像内部提取公钥验证
    # 避免新 avbtool 无法读取 avb_pkmd.bin（OpenSSL 3.x 兼容问题）
    result = subprocess.run(
        [AVBTOOL, 'verify_image', '--image', img],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if 'Successfully verified' in output:
        print('\n  验证通过。')
    else:
        print(f'\n  {RED}> 验证失败{CLOSE}')
    input('> 按回车继续')


def main():
    if not os.path.isfile(AVBTOOL):
        print(f'\n{RED}> 未找到 avbtool: {AVBTOOL}{CLOSE}')
        input('> 按回车继续')
        return

    actions = {
        '01': cmd_info_image,
        '02': cmd_add_hash_footer,
        '03': cmd_add_hashtree_footer,
        '04': cmd_verify_image,
    }

    while True:
        os.system("clear")
        print(f'\n{BOLD}> 镜像签名与VBMeta工具{CLOSE}\n')
        print(f'  {YELLOW}[00]{CLOSE}\t返回上级菜单')
        print()
        print(f'  {YELLOW}[01]{CLOSE}\t解析VBMeta镜像信息')
        print()
        print(f'  {GREEN}[02]{CLOSE}\t添加哈希签名 (小分区)')
        print()
        print(f'  {GREEN}[03]{CLOSE}\t添加哈希树签名 (大分区)')
        print()
        print(f'  {CYAN}[04]{CLOSE}\t验证镜像签名')
        print()

        choice = input(f'> {RED}输入序号{CLOSE} >> ').strip()
        if choice == '00' or choice == '0':
            return
        elif choice in actions:
            actions[choice]()
        else:
            input(f'> 无效序号: {choice}')


if __name__ == '__main__':
    main()
