"""AVBTOOL - 镜像签名与VBMeta工具（待实现）"""

import os

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'


def main():
    while True:
        os.system("clear")
        print(f'\n{BOLD}> 镜像签名与VBMeta工具{CLOSE}\n')
        print(f'  {YELLOW}[00]{CLOSE}\t返回上级菜单')
        print()
        print(f'  {YELLOW}[01]{CLOSE}\t查看镜像信息')
        print()
        print(f'  {YELLOW}[02]{CLOSE}\t添加哈希签名（boot/recovery/dtbo）')
        print()
        print(f'  {YELLOW}[03]{CLOSE}\t添加哈希树签名（system/vendor）')
        print()
        print(f'  {YELLOW}[04]{CLOSE}\t验证镜像签名')
        print()
        choice = input(f'> {RED}输入序号{CLOSE} >> ').strip()
        if not choice.isdigit():
            continue
        if choice == '00' or choice == '0':
            return
        else:
            print(f'\n{YELLOW}> 功能开发中...{CLOSE}')
            input('> 任意键继续')


if __name__ == '__main__':
    main()
