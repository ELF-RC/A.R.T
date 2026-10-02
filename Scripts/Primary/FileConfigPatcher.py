"""Android fsconfig scanning and metadata patching."""

import os
from collections import deque


# ---------------------------------------------------------------------------
# fsconfig / file_contexts sanitization
# ---------------------------------------------------------------------------
# The Android repackers (e2fsdroid, mkfs.erofs) match fsconfig / file_contexts
# paths against the source tree by *literal* path: an entry whose path does not
# exist on disk is reported "failed to find" and its files are skipped — the
# repacked image then silently loses them. So every config path has to equal
# the real on-disk name.
#
# The one case that desyncs config and disk is a *space*: the ext4 extractor
# rewrites "sp dir" -> "sp_dir" on disk (recorded in space.txt), while a
# config generated elsewhere (the erofs unpacker keeps original names, copied
# fs_config keeps the original string) may still say "sp dir". This module
# rewrites exactly that case — space -> underscore — in fsconfig paths,
# symlink targets, and context regexes, and logs every rewrite in a companion
# map file so the original name is never lost.
#
# Non-ASCII names are intentionally left as-is: they are valid UTF-8 on disk,
# the repacker accepts them, and escaping them to \\uXXXX form breaks the
# literal match (the old behaviour, which dropped every non-ASCII entry on
# repack). \\uXXXX is only meaningful to the AOSP build-time fs_config
# generator, not to e2fsdroid / mkfs.erofs.
def _escape_component(component):
    """Packer-safe form of one fsconfig / contexts path component.

    Spaces are rewritten to underscores (the on-disk tree and the space-aware
    column parser both need them gone); every other character, including
    non-ASCII, is kept verbatim so the path still matches the repacker's
    literal lookup.
    """
    return component.replace(' ', '_')


def _sanitize_fsconfig_line(line, rewrites):
    """Packer-safe rewrite of one fsconfig line (path + optional target).

    Column order: `path uid gid [mode [capabilities=... [target]]]`.
    Spaces in the path or the symlink target would desync the config
    from the underscored on-disk tree, so they are rewritten to
    underscores.

    The fixed columns (uid, gid, mode, capabilities=) are the *only*
    unambiguous column boundaries: uid/gid are plain decimal integers,
    mode is a 4-digit octal, capabilities is the `capabilities=...`
    keyword.  The path is everything before the first fixed column
    (joined back with underscores), and the target — if present — is the
    word(s) after the last fixed column.  This works for paths that
    themselves contain spaces (the case that requires sanitising), which
    a plain `split(' ')` cannot recover.
    """
    stripped = line.rstrip('\n')
    if not stripped:
        return line
    tokens = stripped.split(' ')

    def _is_fixed(t):
        if t.isdigit():
            return True
        if len(t) == 4 and all(c in '01234567' for c in t):
            return True
        return t.startswith('capabilities=')

    # Walk from the right collecting the maximal run of fixed tokens
    # (uid, gid, mode, capabilities= — at most 4).  Everything left of
    # that run is the path (which may contain spaces, joined back with
    # underscores); anything right of it would be a symlink target,
    # which this extractor never emits, so the path run already ends at
    # the last token in practice.
    n = len(tokens)
    i = n
    while i > 1 and _is_fixed(tokens[i - 1]) and (n - i) < 4:
        i -= 1
    # tokens[1:i] = words between the path and the fixed block: symlink
    # target (if any).  The path is tokens[0] plus those words.
    path = '_'.join([tokens[0]] + tokens[1:i])
    fixed = tokens[i:]
    escaped_path = _escape_component(path)
    if escaped_path != path:
        rewrites.append(path)
    return ' '.join([escaped_path] + fixed) + '\n'



def _sanitize_contexts_line(line, rewrites):
    """Packer-safe rewrite of one file_contexts line (path-regex + label).

    Layout: "path-regex u:object_r:label:s0" — exactly one space separates
    the two fields. The regex may contain escaped specials but no raw
    spaces (a space inside it would be misread as the field separator), so
    spaces become underscores — matching the underscored on-disk tree. A
    single rsplit is safe either way.
    """
    stripped = line.rstrip('\n')
    if not stripped:
        return line
    parts = stripped.rsplit(' ', 1)
    if len(parts) == 1:
        path, label = parts[0], ''
    else:
        path, label = parts
    escaped_path = _escape_component(path)
    new_line = (escaped_path + ' ' + label + '\n') if label else escaped_path + '\n'
    if new_line.rstrip('\n') != stripped:
        rewrites.append(path)
    return new_line


def _sanitize_file(path, sanitize_line, rewrites):
    if not os.path.isfile(path):
        return
    with open(path, 'r', encoding='utf-8') as source:
        lines = source.readlines()
    new_lines = []
    for line in lines:
        line_rewrites = []
        new_lines.append(sanitize_line(line, line_rewrites))
        rewrites.extend(line_rewrites)
    if any(old.rstrip('\n') != new.rstrip('\n') for old, new in zip(lines, new_lines)):
        with open(path, 'w', encoding='utf-8', newline='\n') as target:
            target.writelines(new_lines)


def sanitize_metadata_files(fsconfig_path, contexts_path):
    """Packer-safe rewrite of fsconfig / file_contexts path columns.

    Spaces become underscores (the on-disk tree was written with
    underscored components); non-ASCII characters are kept verbatim so
    the paths still match the repacker's literal lookup. The
    uid/gid/mode/capabilities/label columns are preserved verbatim.
    Returns the deduplicated, order-preserved list of original tokens
    that were rewritten.
    """
    rewrites = []
    _sanitize_file(fsconfig_path, _sanitize_fsconfig_line, rewrites)
    _sanitize_file(contexts_path, _sanitize_contexts_line, rewrites)
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
