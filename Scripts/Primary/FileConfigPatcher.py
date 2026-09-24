"""Android fsconfig scanning and metadata patching."""

import os
from collections import deque

# ---------------------------------------------------------------------------
# fsconfig scanning and metadata completion for image repacking
# ---------------------------------------------------------------------------
# Read the existing fsconfig into a path -> metadata mapping.
def scanfs(file: str) -> dict:
    """
    Scan Origin File , Return A dict
    :param file:
    :return:
    """
    filesystem_config = {}
    with open(file, "r", encoding='utf-8') as file_:
        for i in file_.readlines():
            if not i.strip():
                print("[W] data is empty!")
                continue
            try:
                filepath, *other = i.strip().split()
            except (TypeError,) as e:
                print(f'[W] Skip {i} {e}')
                continue
            filesystem_config[filepath] = other
            if (long := len(other)) > 4:
                print(f"[W] {i[0]} has too much data-{long}.")
    return filesystem_config


# Enumerate directories, files, and required root entries.
def scan_dir(folder: str) -> list:
    """
    Scan Folder , Return A path One By One
    :param folder:
    :return:
    """
    allfiles = ['/', '/lost+found']
    yield os.path.basename(folder)
    for root, dirs, files in os.walk(folder, topdown=True):
        for dir_ in dirs:
            yield os.path.join(root, dir_).replace(folder, os.path.basename(folder)).replace('\\', '/')
        for file in files:
            yield os.path.join(root, file).replace(folder, os.path.basename(folder)).replace('\\', '/')
        yield from allfiles


def islink(file) -> str:
    """
    Determine if it is a SymLink
    :param file:
    :return:
    """
    if os.path.islink(file):
        return os.readlink(file)
    return ''


# Add default metadata for paths missing from the source fsconfig.
def fs_patch(fs_file, dir_path) -> tuple:  # Compare the two metadata dictionaries.
    """
    Patch fs_file, Add Missing File Config
    :param fs_file:
    :param dir_path:
    :return:
    """
    new_fs = {}
    new_add = 0
    r_fs = deque()
    print(f"FsPatcher: The original file has {len(fs_file.keys()):d} entries")
    for i in scan_dir(os.path.abspath(dir_path)):
        if not i.isprintable():
            tmp = ''
            for c in i:
                tmp += c if c.isprintable() else '*'
            i = tmp.replace(' ', '*')
        if fs_file.get(i):
            new_fs[i] = fs_file[i]
        else:
            if i in r_fs:
                continue
            filepath = os.path.abspath(dir_path + os.sep + ".." + os.sep + i)
            if os.path.isdir(filepath):
                if "system/bin" in i or "system/xbin" in i or "vendor/bin" in i:
                    gid = '2000'
                else:
                    gid = '0'
                # dir path always 755
                config = ['0', gid, '0755']
            elif not os.path.exists(filepath):
                config = ['0', '0', '0755']
            elif islink(filepath):
                if ("system/bin" in i) or ("system/xbin" in i) or ("vendor/bin" in i):
                    gid = '2000'
                else:
                    gid = '0'
                if ("/bin" in i) or ("/xbin" in i):
                    mode = '0755'
                elif ".sh" in i:
                    mode = "0750"
                else:
                    mode = "0644"
                config = ['0', gid, mode, islink(filepath)]
            elif ("/bin" in i) or ("/xbin" in i):
                mode = '0755'
                if ("system/bin" in i) or ("system/xbin" in i) or ("vendor/bin" in i):
                    gid = '2000'
                else:
                    gid = '0'
                    mode = '0755'
                if ".sh" in i:
                    mode = "0750"
                else:
                    for s in ["/bin/su", "/xbin/su", "disable_selinux.sh", "daemon", "ext/.su", "install-recovery",
                              'installed_su', 'bin/rw-system.sh', 'bin/getSPL']:
                        if s in i:
                            mode = "0755"
                config = ['0', gid, mode]
            else:
                config = ['0', '0', '0644']
            print(f'Add [{i}{config}]')
            r_fs.append(i)
            new_add += 1
            new_fs[i] = config
    return new_fs, new_add


# Public fsconfig patching entry point used by EXT4/EROFS repacking.
def patch_fsconfig(dir_path: str, fs_config: str):
    """
    List The Dir_Path and Add Missing file config
    :param dir_path:
    :param fs_config:
    :return:
    """
    new_fs, new_add = fs_patch(scanfs(os.path.abspath(fs_config)), dir_path)
    with open(fs_config, "w", encoding='utf-8', newline='\n') as f:
        f.writelines([f"{i} {' '.join(new_fs[i])}\n" for i in sorted(new_fs.keys())])
    print(f'FsPatcher: Added {new_add} entries')
