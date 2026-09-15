"""TOOL_INFO - 工具说明与作者信息"""

import os

BOLD = '\x1b[1m'
CYAN = '\x1b[1;36m'
YELLOW = '\x1b[1;33m'
CLOSE = '\x1b[0m'


def show():
    os.system("clear")
    print(f"""
{CYAN}{'=' * 50}
  A.R.T - Android ROM Tool
{'=' * 50}{CLOSE}

{YELLOW}项目链接:{CLOSE}
  GitHub: https://github.com/ELF-RC/A.R.T
  原项目: https://github.com/ColdWindScholar/D.N.A3/

{YELLOW}原工具开发者:{CLOSE}
  ColdWindScholar (3590361911@qq.com)

{YELLOW}工具开发者:{CLOSE}
  ELF-RC (3580977309@qq.com)

{YELLOW}二进制文件开发者:{CLOSE}
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

{YELLOW}协议:{CLOSE}
  本工具使用 AGPL-3.0 协议

{YELLOW}感谢所有开源贡献者！{CLOSE}
{'=' * 50}
""")
    input('> 任意键返回')
