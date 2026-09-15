"""A.R.T main module - backward-compatible facade.

All functional code has been split into focused submodules by domain:

Core:
- scripts.utils          : general utilities (call, display, etc.)
- scripts.config         : configuration/setup management
- scripts.workspace      : project/partition path management

Extractors (unpack_<format>):
- scripts.unpack_payload : payload.bin extraction
- scripts.unpack_dat     : new.dat / new.dat.br extraction
- scripts.unpack_ext4    : EXT4 / sparse image extraction
- scripts.unpack_erofs   : EROFS image extraction
- scripts.unpack_super   : super.img extraction
- scripts.unpack_boot    : boot / vendor_boot image extraction
- scripts.unpack_win     : .win archive extraction
- scripts.unpack_dispatch: dispatcher (decompress_img, decompress, extract_zrom)

Repackers (repack_<target>):
- scripts.repack_img     : EXT4/EROFS partition image recompression
- scripts.repack_super   : super.img synthesis
- scripts.repack_boot    : boot / vendor_boot repack

UI:
- scripts.menu           : menu/UI functions

This file re-exports every public name so that existing imports
(`from scripts.cyrus import V, BIN_PATH`, etc.) continue to work.
"""

# ── utils ──────────────────────────────────────────────────────────────
from scripts.utils import (  # noqa: F401
    PWD_DIR, BIN_PATH, V,
    RED, WHITE, CYAN, YELLOW, MAGENTA, GREEN, BOLD, CLOSE,
    change_permissions_recursive, init_bin_path,
    call, CoastTime, display, get_dir_size, ceil,
    find_file, rmdire, appendf, _human_size,
    safe_extract_zip, safe_extract_tar,
    GlobalValue,
    gettype, findfile,
)

# ── config ─────────────────────────────────────────────────────────────
from scripts.config import (  # noqa: F401
    SETUP_JSON, _SETUP_DEFAULTS,
    set_default_env_setup, validate_default_env_setup,
    load_setup_json, env_setup, check_permissions,
)

# ── workspace ──────────────────────────────────────────────────────────
from scripts.workspace import (  # noqa: F401
    LayoutError, UnsupportedLayoutError, ProjectLayout,
    partition_name, workspace_partition, workspace_temp,
    partition_metadata_names, metadata_path,
    normalize_erofs_metadata, ensure_contexts_file,
    create_partition_stage, workspace_relative_path,
    _get_image_logical_size, _destination_partition,
    _safe_remove_workspace_dir, _stage_work_source,
    _canonical_stage_source, _commit_extracted_partition,
    load_image_json, envelop_project, _super_images_to_process,
)

# ── backward compat: gettype module ────────────────────────────────────
import scripts.utils as _gettype_compat  # noqa: F401
import sys as _sys
_sys.modules['scripts.gettype'] = _gettype_compat

# ── unpack: boot ───────────────────────────────────────────────────────
from scripts.unpack_boot import unpackboot, boot_unpack  # noqa: F401

# ── unpack: payload ────────────────────────────────────────────────────
from scripts.unpack_payload import decompress_bin, _decompress_payload_images  # noqa: F401

# ── unpack: dat / dat.br ──────────────────────────────────────────────
from scripts.unpack_dat import (  # noqa: F401
    decompress_dat, decompress_bro, decompress_dat_batch,
    _numbered_fragments, _combine_fragments,
    _list_dat_partitions, _decompress_single_partition,
)

# ── unpack: ext4 / sparse ─────────────────────────────────────────────
from scripts.unpack_ext4 import extract_ext4, convert_sparse  # noqa: F401

# ── unpack: erofs ──────────────────────────────────────────────────────
from scripts.unpack_erofs import extract_erofs  # noqa: F401

# ── unpack: super ──────────────────────────────────────────────────────
from scripts.unpack_super import extract_super  # noqa: F401

# ── unpack: win ────────────────────────────────────────────────────────
from scripts.unpack_win import decompress_win, _win_partition  # noqa: F401

# ── unpack: dispatcher ─────────────────────────────────────────────────
from scripts.unpack_dispatch import decompress_img, decompress, extract_zrom  # noqa: F401

# ── repack: img ────────────────────────────────────────────────────────
from scripts.repack_img import recompress, walk_contexts  # noqa: F401

# ── repack: super ──────────────────────────────────────────────────────
from scripts.repack_super import repack_super  # noqa: F401

# ── repack: boot ───────────────────────────────────────────────────────
from scripts.repack_boot import dboot, boot_repack  # noqa: F401

# ── menu ───────────────────────────────────────────────────────────────
from scripts.menu import (  # noqa: F401
    lists_project, creat_project,
    menu_once, menu_super, menu_modules, menu_main,
    quiet, _tool_info_handler, menu_actions,
    MOD_DIR,
)

# ── Backward compat aliases ────────────────────────────────────────────
# boot_utils(source, dist, flag) → boot_unpack / boot_repack
def boot_utils(source, distance, flag=1):  # noqa: F401
    if flag == 1:
        return boot_unpack(source, distance)
    elif flag == 2:
        return boot_repack(source, distance)
    return False

# ── Ensure binary path is set up on import ─────────────────────────────
init_bin_path()
