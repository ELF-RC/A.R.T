"""AVBTOOL - Image signing and VBMeta operations"""

import os
import shutil
import subprocess

from Scripts.Primary.Utils import V, BIN_PATH

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
CYAN = '\x1b[1;36m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'

AVBTOOL = os.path.join(BIN_PATH, "avbtool")


# AVB command execution and signing-key path helpers.
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


def _unencrypted_key_path(pass_path):
    """Decrypt a passphrase-protected avb.key into a temp plaintext key.

    This avbtool build has no --pass-file option, so when the private key
    is encrypted we decrypt it first with the bundled openssl and hand the
    plaintext key to --key. Returns (plaintext_key_path, temp_to_cleanup)
    or (None, None) when decryption fails.
    """
    if not pass_path:
        return None, None
    OPENSSL = os.path.join(os.path.dirname(AVBTOOL), 'openssl')
    encrypted_key = os.path.join(os.path.dirname(pass_path), 'avb.key')
    plain_key = encrypted_key + '.plain'
    result = subprocess.run(
        [OPENSSL, 'rsa', '-in', encrypted_key, '-out', plain_key,
         '-passin', f'file:{pass_path}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not os.path.isfile(plain_key):
        print(f'\n{RED}> 解密 avb.key 失败: {result.stderr.strip()}{CLOSE}')
        return None, None
    return plain_key, plain_key


def _select_files(prompt="选择文件"):
    """Input may contain multiple space-separated absolute paths."""
    while True:
        line = input(f'\n  {prompt}（绝对路径，多个用空格分隔）>> ').strip()
        if not line:
            return []
        paths = line.split()
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            for p in missing:
                print(f'  {RED}> 文件不存在: {p}{CLOSE}')
            continue
        return paths


# Interactive AVB and VBMeta operations.
def cmd_info_image():
    """[01] Show image information"""
    imgs = _select_files('选择要查看的镜像')
    if not imgs:
        input('> 按回车继续')
        return
    for img in imgs:
        print(f'\n  查看: {img}')
        _run(['info_image', '--image', img])
    input('> 按回车继续')


def _trim_trailing_zeros(img):
    """Trim trailing zero data and return the resulting size; keep the original size when no zero data exists."""
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


def _ask_props():
    """Collect AVB properties: one 'key value' per line, empty line to finish."""
    props = []
    print('\n  ─────────────  Prop（可选）  ─────────────')
    print('  每行格式: key value（空格分隔，留空结束）\n')
    while True:
        line = input('  >> ').strip()
        if not line:
            break
        parts = line.split(None, 1)
        if len(parts) != 2:
            print(f'  {RED}格式错误，应为 key value{CLOSE}')
            continue
        props.append((parts[0], parts[1].strip()))
    if props:
        print(f'\n  共 {len(props)} 条 Prop:')
        for k, v in props:
            print(f'    • {k} = {v}')
    else:
        print('  （无 Prop）')
    print('  ─────────────────────────────────────────')
    return props


# Phase 1: collect partition name, size, props, rollback for one image.
def _collect_sign_spec(img, idx, total, ask_rollback):
    """Prompt for one image's signing parameters without side effects."""
    print(f'\n────────────────────────────────────')
    print(f'当前镜像 ({idx}/{total}) : {os.path.basename(img)}')
    print('────────────────────────────────────')
    part_name = input('\n  分区名（留空用文件名）>> ').strip() or os.path.splitext(os.path.basename(img))[0]
    size_input = input('  分区大小（字节），留空自动计算 >> ').strip()
    rollback = None
    if ask_rollback:
        rollback = input('  回滚索引（默认0）>> ').strip() or '0'
    props = _ask_props()
    return {
        'img': img,
        'part_name': part_name,
        'size_input': size_input,
        'props': props,
        'rollback': rollback,
    }


# Phase 2: copy, trim, decrypt key, resolve size, run avbtool silently.
def _process_sign_spec(spec, cmd_name, algorithm, need_key, trim_zeros):
    """Run avbtool on one spec with captured output. Returns (ok, log_or_path)."""
    img = spec['img']
    part_name = spec['part_name']

    if need_key:
        key_path = _avb_key_path()
        if not key_path:
            return False, '未找到 avb.key，请先生成密钥'
    else:
        key_path = None

    base, ext = os.path.splitext(img)
    out_img = f'{base}_signed{ext}'
    try:
        shutil.copy2(img, out_img)
    except OSError as error:
        return False, f'复制镜像失败: {error}'

    img_size = os.path.getsize(out_img)
    if trim_zeros:
        trimmed = _trim_trailing_zeros(out_img)
        if trimmed == 0:
            os.remove(out_img)
            return False, '镜像文件全为零，无法签名'
        img_size = os.path.getsize(out_img)

    unenc_key = None
    unenc_cleanup = None
    if algorithm != 'NONE':
        pass_path = _pass_file_path()
        unenc_key, unenc_cleanup = _unencrypted_key_path(pass_path)
        if pass_path and not unenc_key:
            if os.path.isfile(out_img):
                os.remove(out_img)
            return False, '解密 avb.key 失败'
        key_path = unenc_key or key_path

    key_args = [] if algorithm == 'NONE' else ['--key', key_path]
    alg_args = ['--algorithm', algorithm] + key_args

    import math
    size_input = spec['size_input']
    if size_input:
        v = int(size_input)
        aligned_ps = str(v) if v % 4096 == 0 else str((v + 4095) // 4096 * 4096)
    elif cmd_name == 'add_hashtree_footer':
        trial_ps = (img_size + 64 * 1024 * 1024 + 4095) // 4096 * 4096
        r = subprocess.run([AVBTOOL, 'add_hashtree_footer',
            '--image', out_img, '--partition_name', part_name,
            '--hash_algorithm', 'sha256'] + alg_args +
            ['--partition_size', str(trial_ps), '--calc_max_image_size'],
            capture_output=True, text=True)
        max_img = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
        aligned_ps = str(math.ceil(img_size / max_img * trial_ps / 4096) * 4096) if max_img else str(trial_ps)
        r2 = subprocess.run([AVBTOOL, 'add_hashtree_footer',
            '--image', out_img, '--partition_name', part_name,
            '--hash_algorithm', 'sha256'] + alg_args +
            ['--partition_size', aligned_ps, '--calc_max_image_size'],
            capture_output=True, text=True)
        max_img2 = int(r2.stdout.strip()) if r2.stdout.strip().isdigit() else 0
        if max_img2 and max_img2 < img_size:
            aligned_ps = str(math.ceil(img_size / max_img2 * int(aligned_ps) / 4096) * 4096)
    else:
        aligned_ps = str((img_size + 69632 + 4095) // 4096 * 4096)

    args = [cmd_name, '--image', out_img, '--partition_name', part_name,
            '--hash_algorithm', 'sha256'] + alg_args + ['--partition_size', aligned_ps]
    for k, v in spec['props']:
        args.extend(['--prop', f'{k}:{v}'])
    if cmd_name == 'add_hash_footer' and spec.get('rollback') is not None:
        args.extend(['--rollback_index', spec['rollback']])

    result = subprocess.run([AVBTOOL] + args, capture_output=True, text=True)
    log = (result.stdout + result.stderr).strip()

    if unenc_cleanup:
        try:
            os.remove(unenc_cleanup)
        except OSError:
            pass

    if result.returncode == 0:
        return True, out_img
    if os.path.isfile(out_img):
        os.remove(out_img)
    return False, log


# Two-phase batch signer: collect all specs, then process silently.
def _sign_batch(cmd_name, title, algorithm, need_key, trim_zeros, ask_rollback):
    """Clear screen, collect signing specs for all images, then process them
    silently with avbtool. Shows green Success ! or red Failed ! + Process log."""
    os.system('clear')
    print(f'\n> {title}\n')
    imgs = _select_files('选择要签名的镜像')
    if not imgs:
        input('> 按回车继续')
        return

    # Phase 1: collect requirements for every image up front (no babysitting).
    specs = []
    for idx, img in enumerate(imgs, 1):
        specs.append(_collect_sign_spec(img, idx, len(imgs), ask_rollback))
        if idx < len(imgs):
            print('\n  NEXT ONE')

    # Phase 2: process all images silently; capture avbtool output per image.
    print('\nProcessing images signature...')
    failures = []
    for spec in specs:
        ok, log = _process_sign_spec(spec, cmd_name, algorithm, need_key, trim_zeros)
        if not ok:
            failures.append((os.path.basename(spec['img']), log))

    if failures:
        print(f'\n{RED}Failed !{CLOSE}')
        for name, log in failures:
            print(f'  {name}:')
            for line in log.splitlines():
                print(f'    {line}')
    else:
        print(f'\n{GREEN}Success !{CLOSE}')
    input('> 按回车继续')


def cmd_add_hash_footer():
    """[02] Add a hash footer to small partitions such as boot, recovery, and dtbo"""
    _sign_batch('add_hash_footer', '添加哈希签名 (小分区)',
                'SHA256_RSA4096', need_key=True, trim_zeros=True, ask_rollback=True)


def cmd_add_hashtree_footer():
    """[03] Add a hashtree footer to large partitions such as system and vendor"""
    _sign_batch('add_hashtree_footer', '添加哈希树签名 (大分区)',
                'SHA256_RSA4096', need_key=True, trim_zeros=False, ask_rollback=False)


def cmd_add_hashtree_footer_plain():
    """[04] Add an unencrypted hashtree footer (NONE) so avbroot treats the
    partition as unsigned and copies its hashtree into the parent vbmeta."""
    _sign_batch('add_hashtree_footer', '添加哈希树签名 (不加密)',
                'NONE', need_key=False, trim_zeros=False, ask_rollback=False)


def cmd_verify_image():
    """[04] Verify the image signature"""
    imgs = _select_files('选择要验证的镜像')
    if not imgs:
        input('> 按回车继续')
        return

    for img in imgs:
        print(f'\n  验证: {img}')
        # Omit --key so avbtool extracts the public key from the image.
        result = subprocess.run(
            [AVBTOOL, 'verify_image', '--image', img],
            capture_output=True, text=True,
        )
        output = result.stdout + result.stderr
        if 'Successfully verified' in output:
            print(f'  {GREEN}验证通过。{CLOSE}')
        else:
            print(f'  {RED}验证失败{CLOSE}')
            print(output, end='')
    input('> 按回车继续')


def cmd_erase_footer():
    """[05] Remove the image AVB footer"""
    imgs = _select_files('选择要去除签名的镜像')
    if not imgs:
        input('> 按回车继续')
        return

    for img in imgs:
        base, ext = os.path.splitext(img)
        out_img = f'{base}_unsign{ext}'
        shutil.copy2(img, out_img)

        print(f'\n  去除签名: {img}')
        ok = _run(['erase_footer', '--image', out_img])
        if ok:
            print(f'  {GREEN}Patch has been completed.{CLOSE}')
        else:
            print(f'  {RED}> 失败{CLOSE}')
            os.remove(out_img)
    input('> 按回车继续')


# AVB submenu dispatcher.
def main():
    if not os.path.isfile(AVBTOOL):
        print(f'\n{RED}> 未找到 avbtool: {AVBTOOL}{CLOSE}')
        input('> 按回车继续')
        return

    actions = {
        '01': cmd_info_image,
        '02': cmd_add_hash_footer,
        '03': cmd_add_hashtree_footer,
        '04': cmd_add_hashtree_footer_plain,
        '05': cmd_verify_image,
        '06': cmd_erase_footer,
        '1': cmd_info_image,
        '2': cmd_add_hash_footer,
        '3': cmd_add_hashtree_footer,
        '4': cmd_add_hashtree_footer_plain,
        '5': cmd_verify_image,
        '6': cmd_erase_footer,
    }

    while True:
        os.system("clear")
        print(f'\n{BOLD}> 镜像签名与VBMeta工具{CLOSE}\n')
        print(f'  {YELLOW}[00]{CLOSE}\t返回上级菜单')
        print()
        print(f'  {YELLOW}[01]{CLOSE}\t解析镜像签名信息')
        print()
        print(f'  {GREEN}[02]{CLOSE}\t添加哈希签名 (小分区)')
        print()
        print(f'  {GREEN}[03]{CLOSE}\t添加哈希树签名 (大分区)')
        print()
        print(f'  {GREEN}[04]{CLOSE}\t添加哈希树签名 (不加密)')
        print()
        print(f'  {CYAN}[05]{CLOSE}\t验证镜像签名')
        print()
        print(f'  {CYAN}[06]{CLOSE}\t去除镜像签名')
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
