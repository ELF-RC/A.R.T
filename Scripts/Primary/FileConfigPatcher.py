"""Android fsconfig scanning and metadata patching."""

import os
from collections import deque


# ---------------------------------------------------------------------------
# fsconfig / file_contexts sanitization
# ---------------------------------------------------------------------------
# The Android packers (e2fsdroid, mkfs.erofs) only accept ASCII in their
# config files, and their line parsers are whitespace-sensitive:
#   - fsconfig:  "path uid gid [mode [capabilities=... [link_target]]]"
#   - contexts:  "path-regex u:object_r:label:s0"
# Non-ASCII path bytes break both parsers outright, and a space inside the
# path column misaligns the uid/gid columns. sanitize_metadata_files()
# rewrites such path fields to ASCII \uXXXX form, and records every
# rewrite in a companion .map file so a later repack step can restore the
# original names.
def _escape_component(component):
    """ASCII-safe form of one fsconfig / contexts path component.

    Every byte >= 0x80 becomes \\uXXXX; ASCII characters are kept as-is
    (SELinux special characters in contexts are already escaped by the
    extractor, so they need no second pass here).
    """
    out = []
    for char in component:
        code = ord(char)
        if code < 0x80:
            out.append(char)
        else:
            out.append('\\u%04x' % code)
    return ''.join(out)


def _sanitize_one_line(line, rewrites):
    """Return the sanitized line; record the original path field when changed."""
    stripped = line.rstrip('\n')
    if not stripped:
        return line
    fields = stripped.split(' ')
    if len(fields) < 2:
        # fsconfig: "path uid ..." | contexts: "path label"
        # path is the first field in both layouts.
        pass
    original_path = fields[0]
    escaped = _escape_component(original_path)
    if escaped != original_path:
        rewrites.append(original_path)
    if len(fields) == 1:
        return escaped + '\n'
    rest = fields[1:]
    return escaped + ' ' + ' '.join(rest) + '\n'


def sanitize_metadata_files(fsconfig_path, contexts_path):
    """ASCII-sanitize the path column of fsconfig / file_contexts in place.

    fsconfig lines look like "path uid gid mode [cap] [link]" and contexts
    lines look like "path-regex label"; in both the first column is the path
    and every other column is preserved verbatim. Returns the list of
    original path fields that were rewritten (empty when already safe).
    """
    rewrites = []
    for path in (fsconfig_path, contexts_path):
        if not os.path.isfile(path):
            continue
        with open(path, 'r', encoding='utf-8') as source:
            lines = source.readlines()
        new_lines = [_sanitize_one_line(line, rewrites) for line in lines]
        if any(old.rstrip('\n') != new.rstrip('\n') for old, new in zip(lines, new_lines)):
            with open(path, 'w', encoding='utf-8', newline='\n') as target:
                target.writelines(new_lines)
    # Deduplicate while preserving order.
    seen = set()
    unique = []
    for item in rewrites:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def write_map_file(map_path, rewrites):
    """Record the rewritten paths so repack can restore original names."""
    with open(map_path, 'w', encoding='utf-8', newline='\n') as target:
        for item in rewrites:
            target.write(item + '\n')


def load_map_file(map_path):
    """Read back the rewrite map; empty list when the file is absent."""
    if not os.path.isfile(map_path):
        return []
    with open(map_path, 'r', encoding='utf-8') as source:
        return [line.rstrip('\n') for line in source if line.strip()]


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
