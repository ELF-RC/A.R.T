"""Menu/UI functions extracted from cyrus.py."""

import os
import sys
from glob import glob
from pathlib import Path

from pys.utils import (
    V, PWD_DIR, RED, GREEN, YELLOW, CYAN, MAGENTA, BOLD, CLOSE,
    display, rmdire, CoastTime, change_permissions_recursive,
)
from pys.config import load_setup_json, env_setup, check_permissions
from pys.workspace import envelop_project, workspace_partition
from pys.workspace import LayoutError, UnsupportedLayoutError
from pys.unpack_dispatch import decompress, extract_zrom, decompress_img
from pys.unpack_payload import decompress_bin
from pys.unpack_win import decompress_win
from pys.repack_img import recompress
from pys.repack_super import repack_super
from pys.unpack_boot import boot_unpack
from pys.repack_boot import boot_repack

MOD_DIR = PWD_DIR + "local/sub/"

_RESERVED_MENU_IDS = {44, 66, 88}


def lists_project(dTitle, sPath, flag):
    i = 0
    V.dict0 = {i: dTitle}
    if flag == 0:
        for obj in glob(sPath):
            if os.path.isdir(obj):
                i += 1
                while i in _RESERVED_MENU_IDS:
                    i += 1
                V.dict0[i] = obj
    elif flag == 1:
        for obj in glob(sPath):
            if os.path.isfile(obj):
                i += 1
                while i in _RESERVED_MENU_IDS:
                    i += 1
                V.dict0[i] = obj
    elif flag == 2:
        for obj in glob(sPath):
            if os.path.isdir(obj):
                if os.path.isfile(obj + os.sep + "run.sh"):
                    i += 1
                    while i in _RESERVED_MENU_IDS:
                        i += 1
                    V.dict0[i] = obj

    e = 1
    print("-------------------------------------------------------\n")
    for (key, value) in V.dict0.items():
        print(f"  \x1b[0;3{e}m[{key}]\x1b[0m - \x1b[0;3{e + 4}m{os.path.basename(value)}\x1b[0m")
        e = 2

    print("\n-------------------------------------------------------")
    if flag == 0:
        print("\x1b[0;35m  [22] - 删除项目      [44] - 工具设置\n  [66] - 退出工具      [88] - 工具信息  \x1b[0m\n")
    if flag == 2:
        print("\x1b[0;35m  [33] - 安装         [44] - 删除         [88] - 退出  \x1b[0m\n")


def creat_project():
    os.system("clear")
    print("\x1b[1;31m> 新建工程:\x1b[0m\n")
    creat_name = input("  输入名称【不能有空格、特殊符号】: DNA_").strip().rstrip("\\").replace(" ", "_")
    if not creat_name:
        return

    V.project = "DNA_" + creat_name
    try:
        from pys.workspace import ProjectLayout
        ProjectLayout.validate_component(V.project, "工程")
    except LayoutError as error:
        input(f"> 工程名称无效: {error}")
        return
    if os.path.exists(os.path.join(PWD_DIR, V.project)):
        input(f"\x1b[0;31m\n 工程目录< \x1b[0;32m{V.project} \x1b[0;31m>已存在, 回车返回 ...\x1b[0m\n")
        return

    try:
        envelop_project()
    except (LayoutError, UnsupportedLayoutError) as error:
        input(f"> 创建工程失败: {error}")
        return
    return True


def quiet():
    V.JM = input('> 是否开启静默 [0/1]: ') == '1'


def menu_once():
    load_setup_json()
    while True:
        os.system("clear")
        print("\x1b[0;33m> 工程列表\x1b[0m")
        lists_project("新建工程", "DNA_*", 0)
        choice = input("> 选择: ")
        if not choice or not choice.isdigit():
            continue
        if int(choice) == 66:
            sys.exit()
        elif int(choice) == 22:
            if V.dict0:
                which = input("> 输入序号进行删除: ")
                if not which.isdigit():
                    continue
                elif int(which) > 0:
                    if int(which) < len(V.dict0):
                        if input(
                                f"\x1b[0;31m> 是否删除 \x1b[0;34mNo.{which} \x1b[0;31m工程: \x1b[0;32m{os.path.basename(V.dict0[int(which)])}\x1b[0;31m [0/1]:\x1b[0m ") == "1":
                            if os.path.isdir(V.dict0[int(which)]):
                                rmdire(V.dict0[int(which)])
                                continue
                    input(f"> Number {which} Error !")
        elif int(choice) == 44:
            env_setup()
            load_setup_json()
        elif int(choice) == 88:
            from pys import tool_info as _ti
            _ti.show()
        elif int(choice) == 0:
            if creat_project():
                menu_main()
            continue
        elif 0 < int(choice) < len(V.dict0):
            V.project = V.dict0[int(choice)]
            try:
                envelop_project()
            except (LayoutError, UnsupportedLayoutError) as error:
                input(f'> 无法打开工程: {error}')
                continue
            menu_main()
            continue
        else:
            input(f"> Number \x1b[0;33m{choice}\x1b[0m enter error !")


def menu_super():
    """Interactive super image repack."""
    os.system("clear")
    print(f'\x1b[1;36m> 合成 super.img\x1b[0m')
    print(f'> 请将需要打包的 .img 文件放入 INPUT 目录')
    print(f'> INPUT: {V.input}')
    input('> 准备好后按回车继续...')

    images = sorted(glob(os.path.join(V.input, '*.img')))
    if not images:
        print('> INPUT 目录下未发现 .img 文件')
        input('> 任意键返回')
        return

    print(f'\n发现以下镜像文件：')
    for i, img in enumerate(images, 1):
        print(f'  [{i}] {os.path.basename(img)}')

    selected = []
    for img in images:
        stem = Path(img).stem
        if stem.endswith('_a') or stem.endswith('_b'):
            name = stem[:-2]
        else:
            name = stem
        choice = input(f'\n是否要打包 {os.path.basename(img)} ? [1:YES/0:NO]: ')
        if choice == '1':
            selected.append((name, img))
            print(f'  ✓ {name}')
        else:
            print(f'  ✗ {name} (跳过)')

    if not selected:
        print('\n> 未选择任何镜像')
        input('> 任意键返回')
        return

    while True:
        super_type = input('\n打包类型 [0:Aonly/1:AB/2:VAB]: ')
        if super_type in ('0', '1', '2'):
            super_type = int(super_type)
            break
        print('> 无效输入，请输入 0、1 或 2')

    while True:
        super_sparse = input('合成 SUPER 镜像格式 [1:SPARSE/0:RAW]: ')
        if super_sparse in ('0', '1'):
            super_sparse = int(super_sparse)
            break
        print('> 无效输入，请输入 0 或 1')

    type_names = {0: 'A-only', 1: 'A/B', 2: 'Virtual A/B'}
    print(f'\n打包类型: {type_names[super_type]}')
    print(f'输出格式: {"SPARSE" if super_sparse else "RAW"}')
    print(f'包含分区: {", ".join(name for name, _ in selected)}')

    with CoastTime():
        repack_super(selected, super_type, super_sparse)


def menu_modules():
    while True:
        os.system("clear")
        print("\x1b[0;33m> 插件列表\x1b[0m")
        lists_project("返回上级", MOD_DIR + "DNA_*", 2)
        choice = input("> 选择: ")
        if not choice.isdigit():
            continue
        if int(choice) == 88:
            sys.exit()
        elif int(choice) == 33:
            extract_zrom(input("请输入插件路径："))
        elif int(choice) == 44:
            if V.dict0:
                which = input("> 输入序号进行删除: ")
                if int(which) == 0 or not which.isdigit():
                    continue
                if int(which) <= len(V.dict0):
                    if input(
                            f"\x1b[0;31m> 是否删除 \x1b[0;34mNo.{which} \x1b[0;31m插件: \x1b[0;32m{os.path.basename(V.dict0[int(which)])}\x1b[0;31m [0/1]:\x1b[0m ") == "1":
                        if os.path.isdir(V.dict0[int(which)]):
                            rmdire(V.dict0[int(which)])
                            continue
                        else:
                            input(f"> Number {which} Error !")
        elif int(choice) == 0:
            return
        if 0 < int(choice) < len(V.dict0):
            os.system("clear")
            print(f"\x1b[1;31m> 执行插件:\x1b[0m {os.path.basename(V.dict0[int(choice)])}\n")
            if os.path.isfile(shell_sub := (V.dict0[int(choice)] + os.sep + "run.sh")):
                from pys.utils import call
                call(['busybox', 'bash', shell_sub, V.workspace.replace(os.sep, '/')])
            input('> 任意键继续')
        else:
            print(f"> Number \x1b[0;33m{choice}\x1b[0m enter error !")


def _tool_info_handler():
    from pys import tool_info as _ti
    _ti.show()


menu_actions = {
    66: sys.exit,
    8: menu_modules,
    7: menu_super,
}


def menu_main():
    """Run the project menu iteratively."""
    V.JM = True
    while True:
        os.system("clear")
        print(f'\x1b[1;36m> 当前工程: \x1b[0m{V.project}')
        print('-------------------------------------------------------------\n')
        print('\x1b[0;31m\t   0 > 返回主菜单            66 > 退出工具\x1b[0m\n')
        print('\n')
        print('\x1b[0;32m\t   1 > 分解 [bin]            2 > 分解 [dat.br]        \x1b[0m\n')
        print('\x1b[0;36m\t   3 > 分解 [dat]            4 > 分解 [img]\x1b[0m\n')
        print('\x1b[0;33m\t   5 > 分解 [win]            6 > 分解 [super]\x1b[0m\n')
        print('\n')
        print('\x1b[0;35m\t   7 > 合成 [super]          8 > 插件 [sub]\x1b[0m\n')
        print('\x1b[0;34m\t   9 > 合成 [img]           10 > 合成 [dat]\x1b[0m\n')
        print('\x1b[0;32m\t  11 > 合成 [dat.br]        12 > 更多 [more]\x1b[0m\n')
        print('-------------------------------------------------------------')
        option = input(f'> {RED}输入序号{CLOSE} >> ')
        if not option.isdigit():
            input('> 输入序号数字')
            continue

        option = int(option)
        valid_options = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 66}
        if option not in valid_options:
            input(f'> 无效序号: {option}')
            continue

        if option == 0:
            return
        if option in menu_actions:
            menu_actions[option]()
        elif option == 1:
            infile = V.input + 'payload.bin'
            if not os.path.exists(infile):
                input("未发现Payload.Bin")
            else:
                decompress_bin(infile, V.input,
                               input(f'> {RED}选择提取方式:  [0]全盘提取  [1]指定镜像{CLOSE} >> '))
        elif int(option) in [2, 3, 4]:
            quiet()
            decompress(glob(V.input + {2: "*.br", 3: "*.new.dat", 4: "*.img"}[int(option)]), int(option))
        elif int(option) == 5:
            infile = glob(V.input + '*.win*')
            for i in glob(V.input + '*.win'):
                infile.append(i)
            quiet()
            decompress_win(list(set(sorted(infile))))
        elif int(option) == 6:
            from pys import lpunpack2
            lpunpack2.main()
            input('> 任意键继续')
            continue
        elif int(option) == 12:
            from pys import more
            more.main()
            continue
        elif int(option) in [9, 10, 11]:
            quiet()
            if int(option) == 9:
                for file in glob(V.config + '*_kernel.txt'):
                    f_basename = os.path.basename(file).rsplit('_', 1)[0]
                    source = workspace_partition(f_basename)
                    if os.path.isdir(source):
                        if not V.JM:
                            display(f'是否合成: {f_basename}.img [1/0]: ', end='')
                            if input() != '1':
                                continue
                        boot_repack(source, V.out)
            for file in glob(V.config + '*_contexts.txt'):
                f_basename = os.path.basename(file).rsplit('_', 1)[0]
                source = workspace_partition(f_basename)
                if os.path.isdir(source):
                    fsconfig = V.config + f_basename + '_fsconfig.txt'
                    contexts = V.config + f_basename + '_contexts.txt'
                    infojson = V.config + f_basename + '_info.txt'
                    if not os.path.isfile(infojson):
                        infojson = None
                    if os.path.isfile(contexts) and os.path.isfile(fsconfig):
                        if not V.JM:
                            txts = {9: "img", 10: "new.dat", 11: "new.dat.br"}
                            display(f'是否合成: {f_basename}.{txts.get(int(option), ".new.dat.br")} [1/0]: ', end='')
                            if input() != '1':
                                continue
                        recompress(source, fsconfig, contexts, infojson, int(option))
        else:
            input(f'\x1b[0;33m{option}\x1b[0m enter error !')
            continue
        input('> 任意键继续')
