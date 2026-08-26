"""MORE - 更多功能入口"""

import os

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
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
        choice = input(f'> {RED}输入序号{CLOSE} >> ').strip()
        if not choice.isdigit():
            continue
        if choice == '00' or choice == '0':
            return
        elif choice == '01':
            from pys import mkbin
            mkbin.main()
            input('> 任意键继续')
        else:
            input(f'> 无效序号: {choice}')


if __name__ == '__main__':
    main()
