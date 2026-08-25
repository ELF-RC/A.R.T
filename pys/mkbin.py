"""MKBIN - avbroot 工具入口"""

import os
import subprocess

from pys.cyrus import BIN_PATH


def main():
    os.system("clear")
    avbroot = os.path.join(BIN_PATH, "avbroot")
    if not os.path.isfile(avbroot):
        print(f'\x1b[91m> 未找到 avbroot 二进制: {avbroot}\x1b[0m')
        return
    subprocess.run([avbroot, "--version"])


if __name__ == '__main__':
    main()
