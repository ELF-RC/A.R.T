"""Android fsconfig scanning and metadata patching."""

import os
import re
from collections import deque

from Scripts.Primary.Utils import GREEN, CLOSE


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
    Spaces in the path or target columns would desync the config from
    the on-disk tree (which uses underscored components), so they are
    rewritten to underscores.

    Parsing: the first space-delimited word is the path column; the
    fixed columns (uid, gid, mode, capabilities=) follow it verbatim.
    A symlink target, when present, is the *last* logical field and may
    contain spaces — it is recovered by taking everything to the right
    of the fixed-column block and joining it back with single spaces.
    Spaces in the path column are rewritten to underscores.
    """
    stripped = line.rstrip('\n')
    if not stripped:
        return line
    tokens = stripped.split(' ')
    # path is the leftmost word (guaranteed space-free by extraction,
    # which underscored it on disk; a copied config with a raw space is
    # handled by the target logic below, since the repacker would have
    # already desynced that entry).
    path = tokens[0]
    # fixed columns: walk from index 1 collecting numeric / 4-digit-octal
    # / capabilities= tokens in order; the first non-fixed token starts
    # the symlink target (may span multiple words).
    i = 1
    fixed = []
    while i < len(tokens):
        t = tokens[i]
        if t.isdigit() or (len(t) == 4 and all(c in '01234567' for c in t)) \
                or t.startswith('capabilities='):
            fixed.append(t)
            i += 1
        else:
            break
    target = ' '.join(tokens[i:])
    escaped_path = _escape_component(path)
    escaped_target = _escape_component(target)
    if escaped_path != path:
        rewrites.append(path)
    if escaped_target != target:
        rewrites.append(target)
    parts = [escaped_path] + fixed + ([escaped_target] if target else [])
    return ' '.join(p for p in parts if p) + '\n'



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
    new_lines = [sanitize_line(line, rewrites) for line in lines]
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
    if not os.path.isfile(fs_config):
        return
    new_fs, new_add = fs_patch(scanfs(os.path.abspath(fs_config)), dir_path)
    with open(fs_config, "w", encoding='utf-8', newline='\n') as f:
        f.writelines([f"{i} {' '.join(new_fs[i])}\n" for i in sorted(new_fs.keys())])
    if new_add:
        print(f'{GREEN}FsPatcher: Added {new_add} entries{CLOSE}')


# Public file_contexts patching entry point used by EXT4/EROFS repacking.
def patch_file_contexts(dir_path: str, contexts: str):
    """Add file_contexts label rules for on-disk paths missing one.

    Mirrors patch_fsconfig: e2fsdroid resolves an SELinux label for every
    on-disk path via ``-S contexts``; a path with no matching rule aborts
    with "No such file or directory searching for label". This walks the
    source tree and back-fills missing paths with the nearest ancestor's
    label (defaulting to system_file), so user-added or renamed entries no
    longer break ext4 repacking. Paths are regex-escaped so special
    characters match literally, matching AOSP file_contexts convention.
    """
    if not os.path.isfile(contexts):
        return
    label = os.path.basename(os.path.abspath(dir_path))
    mount = '/' + label
    DEFAULT_LABEL = 'u:object_r:system_file:s0'

    # Read existing rules: escaped_path -> label, preserving first-seen.
    existing = {}
    with open(contexts, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split(None, 1)
            if len(parts) != 2:
                continue
            path, lbl = parts
            if path not in existing:
                existing[path] = lbl

    # Un-escape to literal paths for ancestor-label lookup.
    literal_labels = {
        re.sub(r'\\(.)', r'\1', esc): lbl for esc, lbl in existing.items()
    }

    def ancestor_label(full):
        parts = full.split('/')
        for i in range(len(parts), 0, -1):
            ancestor = '/'.join(parts[:i])
            if ancestor in literal_labels:
                return literal_labels[ancestor]
        return None

    added = 0
    new_lines = []
    for root, dirs, files in os.walk(dir_path):
        rel = os.path.relpath(root, dir_path)
        prefix = mount if rel == '.' else mount + '/' + rel.replace(os.sep, '/')
        for name in dirs + files:
            full = prefix + '/' + name
            esc = re.escape(full)
            if esc in existing:
                continue
            lbl = ancestor_label(full) or DEFAULT_LABEL
            new_lines.append(f'{esc} {lbl}\n')
            existing[esc] = lbl
            literal_labels[full] = lbl
            added += 1

    if added:
        with open(contexts, 'a', encoding='utf-8', newline='\n') as f:
            f.writelines(new_lines)
        print(f'{GREEN}ContextsPatcher: Added {added} entries{CLOSE}')
