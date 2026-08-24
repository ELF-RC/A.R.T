#!/usr/bin/env python3
"""LP Unpack v2 - super 分区选择性提取工具。
支持：预览 super.img 内所有分区（名称+大小+组），用户勾选后只提取选中项，
      输出到 OUT 目录。sparse 自动 unsparse。原 lpunpack.py 不动。
"""
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 复用原 lpunpack 模块做元数据解析；使用 cyrus.V 获取 INPUT/OUT 路径
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import lpunpack as _lp
except ImportError:
    print('> 无法导入 lpunpack 模块，请确认 pys/ 目录下有 lpunpack.py')
    sys.exit(1)

try:
    from pys.cyrus import V
except Exception:
    V = None  # 独立运行时降级，不依赖 V

YELLOW = '\x1b[1;33m'
GREEN = '\x1b[1;32m'
RED = '\x1b[91m'
BOLD = '\x1b[1m'
CLOSE = '\x1b[0m'


# ---------------------------------------------------------------------------
# 路径获取：优先使用 V（来自 cyrus 已初始化工程），否则回退到 cwd 扫描
# ---------------------------------------------------------------------------
def _get_input_dir():
    if V and getattr(V, 'input', None):
        return V.input
    for root in (os.getcwd(), os.path.dirname(os.getcwd())):
        for name in ("INPUT", "input"):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                return p + os.sep
    return ""


def _get_out_dir():
    if V and getattr(V, 'out', None):
        return V.out
    for root in (os.getcwd(), os.path.dirname(os.getcwd())):
        for name in ("OUT", "out"):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                return p + os.sep
    return ""


def _human_size(b):
    """Convert bytes to human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# 核心：读取 super 元数据，返回分区列表 [(name, group, size_bytes), ...]
# ---------------------------------------------------------------------------
def _list_partitions(super_img_path):
    """Parse super metadata and return sorted partition info list."""
    # 直接复用 LpUnpack.get_info() 获取元数据
    # get_info() 内部会做 sparse 检测和 metadata 读取，最后关闭 fd
    # 所以我们在外面自己控制 sparse 检测，然后调 _read_metadata
    job = _lp.LpUnpack(SUPER_IMAGE=super_img_path, SHOW_INFO=False)
    # 手动做 sparse 检测（与 get_info 内部逻辑一致）
    if _lp.SparseImage(job._fd).check():
        print('Sparse image detected.')
        print('Process conversion to non sparse image...')
        unsparse_file = _lp.SparseImage(job._fd).unsparse()
        job._fd.close()
        job._fd = open(str(unsparse_file), 'rb')
        print('Result:[ok]')
    job._fd.seek(0)
    metadata = job._read_metadata()
    # 不关闭 fd，避免 get_info 的 finally 块误关
    result = []
    for p in metadata.partitions:
        size = 0
        for ext_idx in range(p.num_extents):
            idx = p.first_extent_index + ext_idx
            if idx < len(metadata.extents):
                size += metadata.extents[idx].num_sectors * 512
        group = ""
        if 0 <= p.group_index < len(metadata.groups):
            group = metadata.groups[p.group_index].name
        result.append((p.name, group, size))
    job._fd.close()
    result.sort(key=lambda x: (-x[2], x[0]))  # 大->小，同大小按名字
    return result


# ---------------------------------------------------------------------------
# UI：显示分区列表，让用户勾选
# ---------------------------------------------------------------------------
def _show_partitions(partitions):
    """Print partition list, return indices of selected partitions."""
    if not partitions:
        print(f'{RED}> 未发现任何分区{CLOSE}')
        return []

    print(f'\n{BOLD}发现 {len(partitions)} 个分区：{CLOSE}\n')
    print(f'  {"序号":>4}  {"分区名":<20} {"组":<16} {"大小":>1}')
    print(f'  {"----":>6}  {"-" * 20} {"-" * 18} {"-" * 10}')
    for i, (name, group, size) in enumerate(partitions, 1):
        print(f'  {i:>4}    {name:<20} {group:<18} {_human_size(size):>9}')

    print(f'\n{YELLOW}请输入要提取的分区序号（多个用逗号分隔，如 1,3,5）：{CLOSE}')
    print(f'{YELLOW}  输入 0 跳过（不提取任何分区）{CLOSE}')
    print(f'{YELLOW}  输入 all 全选{CLOSE}')
    ans = input('> ').strip()
    if not ans or ans == '0':
        return []
    if ans.lower() == 'all':
        return list(range(len(partitions)))

    selected = []
    for token in ans.replace('，', ',').split(','):
        token = token.strip()
        if not token:
            continue
        try:
            idx = int(token)
            if 1 <= idx <= len(partitions):
                selected.append(idx - 1)
            else:
                print(f'  {RED}无效序号: {idx}{CLOSE}')
        except ValueError:
            print(f'  {RED}无法解析: {token}{CLOSE}')
    return selected


# ---------------------------------------------------------------------------
# 核心：抽取选中的分区
# ---------------------------------------------------------------------------
def _extract_selected(super_img_path, out_dir, partitions, selected_indices):
    """Extract selected partitions using original lpunpack module."""
    if not selected_indices:
        print(f'{YELLOW}> 未选择任何分区，跳过提取{CLOSE}')
        return

    names = [partitions[i][0] for i in selected_indices]
    print(f'\n{BOLD}> 开始提取 {len(names)} 个分区：{", ".join(names)}{CLOSE}')
    print(f'> 输出目录: {out_dir}')
    print()

    try:
        # 复用原 lpunpack：传入 NAME 过滤，SHOW_INFO=False，指定 OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        job = _lp.LpUnpack(
            SUPER_IMAGE=super_img_path,
            OUTPUT_DIR=out_dir,
            NAME=names,
            SHOW_INFO=False,
        )
        job.unpack()
        print(f'\n{GREEN}> 提取完成！文件已输出到 {out_dir}{CLOSE}\n')
    except Exception as e:
        print(f'{RED}> 提取失败: {e}{CLOSE}')


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    os.system("clear")
    input_dir = _get_input_dir()
    out_dir = _get_out_dir()

    print('\n' * 8)
    print(f'{YELLOW}          lpunpack2.py — super 分区选择性提取{CLOSE}')
    print()
    print(f'{YELLOW}          请将 super.img 放入 INPUT 目录{CLOSE}')
    if input_dir:
        print(f'\n          INPUT: {input_dir}')
    else:
        print(f'\n          {RED}[!] 未找到 INPUT 目录，请确认运行位置正确{CLOSE}')
        print()
        input('> 任意键继续')
        return

    super_path = os.path.join(input_dir, 'super.img')
    if not os.path.isfile(super_path):
        print(f'\n{RED}> INPUT 目录下未发现 super.img{CLOSE}')
        print(f'> 请将 super.img 放入 {input_dir}')
        input('> 任意键继续')
        return

    if not out_dir:
        out_dir = input_dir.replace('INPUT', 'OUT') + os.sep
        if not os.path.isdir(out_dir):
            print(f'\n{YELLOW}[!] 未找到 OUT 目录，将输出到：{out_dir}{CLOSE}')
    else:
        print(f'\n          OUT: {out_dir}')

    print()
    input('          准备好后按回车继续...')

    os.system("clear")
    print(f'\n{BOLD}> 正在读取 super 元数据...{CLOSE}')
    partitions = _list_partitions(super_path)

    if not partitions:
        print(f'{RED}> super.img 内未发现分区或解析失败{CLOSE}')
        input('> 任意键继续')
        return

    selected = _show_partitions(partitions)

    if selected:
        _extract_selected(super_path, out_dir, partitions, selected)



if __name__ == '__main__':
    main()
