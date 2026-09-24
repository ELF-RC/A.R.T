# -*- coding: utf-8 -*-
"""A.R.T command-line application entry point."""
import multiprocessing
import sys
from pathlib import Path


# Make the repository root importable when this file is executed directly.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _configure_stdio_encoding():
    """Force UTF-8 text streams for runtimes that default to ASCII."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_stdio_encoding()

from Scripts.Primary.Menu import menu_once
def exception_handler(exception_type, exception, traceback):
    del traceback
    print("很抱歉，工具出现错误， 请把以下日志提交给开发者：")
    sys.stderr.write('{}: {}\n'.format(exception_type.__name__, exception))
    if input("是否重启 [1=重启/0=退出]") == "1":
        init()
    else:
        sys.exit(1)


def init():
    from Scripts.Primary.Settings import check_permissions
    check_permissions()
    menu_once()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.excepthook = exception_handler
    init()
