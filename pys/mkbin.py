"""MKBIN - 修补 payload.bin 卡刷包"""

import os
import subprocess
import tempfile

from pys.cyrus import V, BIN_PATH

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
CYAN = '\x1b[1;36m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'

KEY_FILES = ('avb.key', 'ota.key', 'avb_pkmd.bin', 'ota.crt')


def _run_avbroot(args, stdin_data=None):
    """Run avbroot with given args, optionally pipe stdin_data."""
    avbroot = os.path.join(BIN_PATH, "avbroot")
    result = subprocess.run(
        [avbroot] + args,
        input=stdin_data,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, end='')
    return result.returncode == 0


def _write_pass_file(d, passphrase):
    """Write passphrase to a temp file, return the path."""
    pf = d / 'passphrase.txt'
    pf.write_text(passphrase, encoding='utf-8')
    return str(pf)


def _signkey_dir():
    return V.layout.ota_signkey_dir if V.layout else None


def _stockzip_dir():
    return V.layout.ota_stockzip_dir if V.layout else None


def _key_status():
    """Return key status: 'ok', 'damaged', or 'missing'."""
    d = _signkey_dir()
    if not d:
        return 'missing'
    existing = [(d / f).is_file() for f in KEY_FILES]
    if all(existing):
        return 'ok'
    if any(existing):
        return 'damaged'
    return 'missing'


def _select_file():
    """Return path to .select file in stock-zip directory."""
    d = _stockzip_dir()
    if not d:
        return None
    return d / '.select'


def _selected_zip():
    """Read selected zip name from .select file. Create if missing."""
    sf = _select_file()
    if not sf:
        return None
    if not sf.is_file():
        sf.write_text('', encoding='utf-8')
        return None
    name = sf.read_text(encoding='utf-8').strip()
    if not name:
        return None
    return name


def _generate_keys():
    """Generate AVB + OTA signing keys."""
    d = _signkey_dir()
    if not d:
        print(f'\n{RED}> 无法获取签名密钥目录{CLOSE}')
        return
    if _key_status() == 'ok':
        print(f'\n{YELLOW}> 密钥已存在，如需重新生成请先删除旧密钥{CLOSE}')
        return

    d.mkdir(parents=True, exist_ok=True)
    passphrase = input('\n  请输入密钥密码：').strip()

    # 先写入 passphrase.txt，用 --pass-file 传密码避免交互
    pass_file = _write_pass_file(d, passphrase)

    print(f'\n  生成 AVB 密钥...')
    if not _run_avbroot(
        ['key', 'generate-key', '-t', 'rsa4096', '-o', str(d / 'avb.key'), '--pass-file', pass_file],
    ):
        print(f'  {RED}✗ AVB 密钥生成失败{CLOSE}')
        return
    print(f'  {GREEN}✓{CLOSE} avb.key')

    print(f'  生成 OTA 密钥...')
    if not _run_avbroot(
        ['key', 'generate-key', '-t', 'rsa4096', '-o', str(d / 'ota.key'), '--pass-file', pass_file],
    ):
        print(f'  {RED}✗ OTA 密钥生成失败{CLOSE}')
        return
    print(f'  {GREEN}✓{CLOSE} ota.key')

    print(f'  编码 AVB 公钥...')
    if not _run_avbroot(
        ['key', 'encode-avb', '-k', str(d / 'avb.key'), '-o', str(d / 'avb_pkmd.bin'), '--pass-file', pass_file],
    ):
        print(f'  {RED}✗ AVB 公钥编码失败{CLOSE}')
        return
    print(f'  {GREEN}✓{CLOSE} avb_pkmd.bin')

    print(f'  生成 OTA 证书...')
    if not _run_avbroot(
        ['key', 'generate-cert', '-k', str(d / 'ota.key'), '-o', str(d / 'ota.crt'), '--pass-file', pass_file],
    ):
        print(f'  {RED}✗ OTA 证书生成失败{CLOSE}')
        return
    print(f'  {GREEN}✓{CLOSE} ota.crt')

    if passphrase:
        print(f'  {GREEN}✓{CLOSE} passphrase.txt（密码已保存）')
    else:
        (d / 'passphrase.txt').unlink(missing_ok=True)

    print(f'\n  {GREEN}> 密钥生成完成！{CLOSE}')


def _delete_keys():
    """Delete all key files in sign-key directory."""
    d = _signkey_dir()
    if not d or not d.is_dir():
        print(f'\n{YELLOW}> 密钥目录不存在{CLOSE}')
        return
    deleted = []
    for f in KEY_FILES:
        fp = d / f
        if fp.is_file():
            fp.unlink()
            deleted.append(f)
    pp = d / 'passphrase.txt'
    if pp.is_file():
        pp.unlink()
        deleted.append('passphrase.txt')
    if deleted:
        print(f'\n  {GREEN}> 已删除：{", ".join(deleted)}{CLOSE}')
    else:
        print(f'\n{YELLOW}> 密钥目录为空{CLOSE}')


def _list_zips():
    """List all .zip files in stock-zip directory."""
    d = _stockzip_dir()
    if not d or not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix.lower() == '.zip' and f.is_file())


def _select_ota():
    """Select an OTA zip from stock-zip directory."""
    sf = _select_file()
    if not sf:
        print(f'> {RED}无法获取 stock-zip 目录{CLOSE}')
        input('> 按回车继续')
        return

    zips = _list_zips()

    if len(zips) == 0:
        sf.write_text('', encoding='utf-8')
        print(f'> {YELLOW}OTA_WORK/stock-zip 内未发现任何 zip 包{CLOSE}')
        input('> 按回车继续')
        return

    if len(zips) == 1:
        sf.write_text(zips[0], encoding='utf-8')
        print(f'> {GREEN}已选择：{zips[0]}{CLOSE}')
        input('> 按回车继续')
        return

    print()
    print(f'  {"序号":>4}      文件名')
    print(f'  {"----":>6}    {"-" * 40}')
    for i, name in enumerate(zips, 1):
        print(f'  {i:>4}      {name}')

    print(f'\n  请输入目标OTA包序号：')
    ans = input('> ').strip()
    if not ans.isdigit():
        print(f'> {RED}无效输入{CLOSE}')
        input('> 按回车继续')
        return
    idx = int(ans)
    if idx < 1 or idx > len(zips):
        print(f'> {RED}无效序号: {idx}{CLOSE}')
        input('> 按回车继续')
        return

    sf.write_text(zips[idx - 1], encoding='utf-8')
    print(f'> {GREEN}已选择：{zips[idx - 1]}{CLOSE}')
    input('> 按回车继续')


def _show_status():
    """Print current status."""
    ks = _key_status()
    if ks == 'ok':
        print(f'  密钥文件状态：{GREEN}已生成{CLOSE}')
    elif ks == 'damaged':
        print(f'  密钥文件状态：{YELLOW}已损坏{CLOSE}')
    else:
        print(f'  密钥文件状态：{RED}未生成{CLOSE}')

    zip_name = _selected_zip()
    if zip_name:
        print(f'  目标OTA包：{GREEN}{zip_name}{CLOSE}')
    else:
        print(f'  目标OTA包：{YELLOW}未选择{CLOSE}')


def main():
    avbroot = os.path.join(BIN_PATH, "avbroot")
    if not os.path.isfile(avbroot):
        print(f'\n{RED}> 未找到 avbroot 二进制: {avbroot}{CLOSE}')
        return

    while True:
        os.system("clear")
        print(f'\n{BOLD}> 修补payload.bin卡刷包{CLOSE}\n')
        print(f'  请将正常刷入的zip放入 {CYAN}OTA_WORK/stock-zip{CLOSE}')
        print(f'  请将要 替换/添加 的IMG放入 {CYAN}OTA_WORK/input-img{CLOSE}\n')

        _show_status()

        print()
        print(f'  {YELLOW}[00]{CLOSE} 返回上级目录            {YELLOW}[01]{CLOSE} 刷新状态')
        print(f'  {GREEN}[02]{CLOSE} 生成密钥(必须)          {GREEN}[03]{CLOSE} 删除密钥')
        print(f'  {RED}[04]{CLOSE} 修补OTA                 {RED}[05]{CLOSE} 验证签名')
        print(f'  {CYAN}[06]{CLOSE} 修补OTA(禁用AVB)        {CYAN}[07]{CLOSE} 选择OTA包')

        print()
        choice = input(f'> {RED}输入序号{CLOSE} >> ').strip()
        if not choice.isdigit():
            continue
        if choice in ('00', '0'):
            return
        elif choice == '01':
            continue
        elif choice == '02':
            _generate_keys()
            continue
        elif choice == '03':
            _delete_keys()
            continue
        elif choice == '04':
            print(f'\n{YELLOW}> 修补功能待实现{CLOSE}')
            input('> 任意键继续')
        elif choice == '05':
            print(f'\n{YELLOW}> 验证功能待实现{CLOSE}')
            input('> 任意键继续')
        elif choice == '06':
            print(f'\n{YELLOW}> 禁用AVB修补功能待实现{CLOSE}')
            input('> 任意键继续')
        elif choice == '07':
            _select_ota()
            continue
        else:
            input(f'> 无效序号: {choice}')


if __name__ == '__main__':
    main()
