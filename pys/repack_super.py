"""Super image repack — 打包 super.img 动态分区。"""

import os
from pathlib import Path

from pys.utils import V, RED, GREEN, YELLOW, CLOSE, display, call, CoastTime
from pys.utils import gettype, findfile
from pys import imgextractor
from pys.workspace import LayoutError


def repack_super(selected_parts, super_type, super_sparse):
    """Synthesize super.img from selected partition images.

    Args:
        selected_parts: list of (name, path) tuples from INPUT
        super_type: 0=A-only, 1=A/B, 2=Virtual A/B
        super_sparse: 1=sparse output, 0=raw output
    """
    from pys.workspace import partition_name

    group_name = V.SETUP_MANIFEST['GROUP_NAME']
    super_size = V.SETUP_MANIFEST['SUPER_SIZE']
    super_output = os.path.join(V.out, 'super.img')
    type_names = {0: 'A-only', 1: 'A/B', 2: 'Virtual A/B'}

    argvs = [
        'lpmake',
        '--metadata-size', '65536',
        '--super-name', 'super',
        '--device', f'super:{super_size}',
    ]
    image_parts = []

    raw_parts = []
    try:
        for name, path in selected_parts:
            if gettype(path) == 'sparse':
                display(f'转换 sparse: {os.path.basename(path)} ...')
                raw = imgextractor.ULTRAMAN().APPLE(path)
                if not raw or not os.path.isfile(raw):
                    print(f'> 无法转换 sparse 镜像: {path}')
                    return
                raw_parts.append((name, raw))
            else:
                raw_parts.append((name, path))
    except (LayoutError, OSError) as error:
        print(f'> 准备 super 镜像失败: {error}')
        return

    input_dir = V.input

    try:
        if super_type == 0:
            argvs.extend(['--metadata-slots', '2',
                          '--group', f'{group_name}:{super_size}'])
            for name, path in raw_parts:
                size = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}:readonly:{size}:{group_name}',
                    '--image', f'{name}={path}',
                ])
                image_parts.append(name)
        elif super_type == 1:
            argvs.extend(['--metadata-slots', '3',
                          '--group', f'{group_name}_a:{super_size}',
                          '--group', f'{group_name}_b:{super_size}'])
            for name, path in raw_parts:
                size_a = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}_a:readonly:{size_a}:{group_name}_a',
                    '--image', f'{name}_a={path}',
                ])
                b_path = os.path.join(input_dir, f'{name}_b.img')
                if os.path.isfile(b_path):
                    if gettype(b_path) == 'sparse':
                        display(f'转换 sparse: {os.path.basename(b_path)} ...')
                        b_raw = imgextractor.ULTRAMAN().APPLE(b_path)
                        if b_raw and os.path.isfile(b_raw):
                            b_path = b_raw
                    size_b = os.path.getsize(b_path)
                    argvs.extend([
                        '--partition', f'{name}_b:readonly:{size_b}:{group_name}_b',
                        '--image', f'{name}_b={b_path}',
                    ])
                else:
                    argvs.extend([
                        '--partition', f'{name}_b:readonly:0:{group_name}_b',
                    ])
                image_parts.append(name)
        else:
            argvs.extend(['--metadata-slots', '3', '--virtual-ab', '-F',
                          '--group', f'{group_name}_a:{super_size}',
                          '--group', f'{group_name}_b:{super_size}'])
            for name, path in raw_parts:
                size = os.path.getsize(path)
                argvs.extend([
                    '--partition', f'{name}_a:readonly:{size}:{group_name}_a',
                    '--image', f'{name}_a={path}',
                    '--partition', f'{name}_b:readonly:0:{group_name}_b',
                ])
                image_parts.append(name)
    except (LayoutError, OSError) as error:
        print(f'> 准备 super 镜像失败: {error}')
        return

    if not image_parts:
        print('> 未选择任何分区镜像')
        return

    if super_sparse == 1:
        argvs.append('--sparse')
    argvs.extend(['--out', super_output])

    display(f'重新合成: super.img <Size:{super_size}|Type:{type_names[super_type]}|Sparse:{super_sparse}>')
    display(f"包含分区：{'|'.join(image_parts)}")
    with CoastTime():
        result = call(argvs)
    if result != 0 or not os.path.isfile(super_output):
        print('> super.img 合成失败')
        return

    print(f'> super.img 已输出到 {V.out}')
