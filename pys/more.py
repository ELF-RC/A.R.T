"""MORE - 更多功能入口"""

import os

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
CYAN = '\x1b[1;36m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'


def main():
    while True:
        os.system("clear")
        print(f'\n{BOLD}> 更多功能{CLOSE}\n')
        print(f'  {YELLOW}[00]{CLOSE}\t返回上级菜单')
        print()
        print(f'  {GREEN}[01]{CLOSE}\t生成payload.bin卡刷包')
        print()
        print(f'  {CYAN}[02]{CLOSE}\t镜像签名与VBMeta工具')
        print()
        VALID = {'00', '0', '01', '1', '02', '2'}
        choice = input(f'> {RED}输入序号{CLOSE} >> ').strip()
        if choice not in VALID:
            input(f'> 无效序号: {choice}')
            continue
        if choice in ('00', '0'):
            return
        elif choice in ('01', '1'):
            from pys import mkbin
            mkbin.main()
            input('> 任意键继续')
        elif choice in ('02', '2'):
            from pys import avbtool
            avbtool.main()
            input('> 任意键继续')
        else:
            input(f'> 无效序号: {choice}')


if __name__ == '__main__':
    main()
