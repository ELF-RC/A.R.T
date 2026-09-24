"""Project directory layout, path-safety helpers, and workspace management.

Merged from project_layout.py + workspace.py.
"""

from __future__ import annotations

import json as _json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from Scripts.Primary.Utils import LayoutError, PWD_DIR, V, get_dir_size, ceil


# ═══════════════════════════════════════════════════════════════════════
#  Layout errors
# ═══════════════════════════════════════════════════════════════════════



class UnsupportedLayoutError(LayoutError):
    """Raised when an existing project does not use the current layout."""


# ═══════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════

_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ROOT_DIRS = frozenset({"INPUT", "OUT", "WORKSPACE", "OTA_WORK"})
_RESERVED_WORKSPACE_NAMES = frozenset({"config", "INPUT", "OUT", "WORKSPACE"})


# ═══════════════════════════════════════════════════════════════════════
#  ProjectLayout (from project_layout.py)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
# Canonical project directory layout and path-safety policy.
class ProjectLayout:
    """The only supported on-disk layout for an A.R.T project.

    The project root must contain only INPUT, OUT, and WORKSPACE. This rejects
    both the legacy layout and partially mixed projects before the program
    writes into them.
    """

    project_dir: Path

    def __post_init__(self):
        raw_path = Path(self.project_dir)
        if raw_path.is_symlink():
            raise LayoutError(f"工程目录不能是符号链接: {raw_path}")
        object.__setattr__(self, "project_dir", raw_path.resolve())

    @property
    def input_dir(self) -> Path:
        return self.project_dir / "INPUT"

    @property
    def out_dir(self) -> Path:
        return self.project_dir / "OUT"

    @property
    def workspace_dir(self) -> Path:
        return self.project_dir / "WORKSPACE"

    @property
    def config_dir(self) -> Path:
        return self.workspace_dir / "config"

    @property
    def ota_work_dir(self) -> Path:
        return self.project_dir / "OTA_WORK"

    @property
    def ota_signkey_dir(self) -> Path:
        return self.ota_work_dir / "sign-key"

    @property
    def ota_stockzip_dir(self) -> Path:
        return self.ota_work_dir / "stock-zip"

    @property
    def ota_inputimg_dir(self) -> Path:
        return self.ota_work_dir / "input-img"

    @property
    def required_dirs(self) -> tuple[Path, ...]:
        return (
            self.input_dir,
            self.out_dir,
            self.workspace_dir,
            self.config_dir,
        )

    @staticmethod
    def _is_safe_directory(path: Path, root: Path) -> bool:
        if path.is_symlink():
            return False
        if not path.exists():
            return True
        if not path.is_dir():
            return False
        try:
            path.resolve().relative_to(root)
        except ValueError:
            return False
        return True

    def state(self) -> str:
        """Return empty, new, incomplete, or unsupported for this project."""
        if self.project_dir.is_symlink():
            return "unsupported"
        if not self.project_dir.exists():
            return "empty"
        if not self.project_dir.is_dir():
            return "invalid"

        root_entries = tuple(self.project_dir.iterdir())
        entries = {entry.name for entry in root_entries}
        if entries - _ROOT_DIRS:
            return "unsupported"
        if any(entry.name in _ROOT_DIRS and (entry.is_symlink() or not entry.is_dir())
               for entry in root_entries):
            return "unsupported"

        directories = {
            entry.name for entry in root_entries
            if entry.is_dir()
        }
        present = directories & _ROOT_DIRS
        if not present:
            return "empty"
        if not all(self._is_safe_directory(path, self.project_dir) for path in self.required_dirs):
            return "unsupported"
        if present != _ROOT_DIRS:
            return "incomplete"
        return "new"

    def initialize(self) -> "ProjectLayout":
        """Create a new layout or finish a safe, partially-created new layout."""
        state = self.state()
        if state == "invalid":
            raise LayoutError(f"工程路径不是目录: {self.project_dir}")
        if state == "unsupported":
            raise UnsupportedLayoutError(
                "检测到非 INPUT/OUT/WORKSPACE 工程目录结构；本版本不支持旧版或混合工程。"
            )

        self.project_dir.mkdir(parents=True, exist_ok=True)
        for path in self.required_dirs:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise LayoutError(f"工程目录项无效: {path}")
            path.mkdir(parents=True, exist_ok=True)
            if not self._is_safe_directory(path, self.project_dir):
                raise LayoutError(f"工程目录越界: {path}")
        return self

    def _require_safe_workspace(self) -> None:
        if not self._is_safe_directory(self.workspace_dir, self.project_dir):
            raise LayoutError(f"工作目录无效: {self.workspace_dir}")

    @staticmethod
    def validate_component(name: str, component_type: str = "目录") -> str:
        if not isinstance(name, str) or not _SAFE_COMPONENT.fullmatch(name):
            raise LayoutError(f"非法{component_type}名称: {name!r}")
        if name in {".", ".."}:
            raise LayoutError(f"非法{component_type}名称: {name!r}")
        return name

    def partition_dir(self, partition: str) -> Path:
        self._require_safe_workspace()
        partition = self.validate_component(partition, "分区")
        if partition in _RESERVED_WORKSPACE_NAMES:
            raise LayoutError(f"保留分区名称: {partition}")
        path = self.workspace_dir / partition
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise LayoutError(f"分区目录无效: {path}")
        resolved = self.require_workspace_path(path)
        for protected in (self.workspace_dir.resolve(), self.config_dir.resolve()):
            if resolved == protected:
                raise LayoutError(f"分区目录指向受保护路径: {path}")
        return path

    def create_stage_dir(self, category: str) -> Path:
        """Create a temporary directory inside WORKSPACE for multi-step pipelines."""
        self.validate_component(category, "临时任务")
        state = self.state()
        if state == "unsupported":
            raise UnsupportedLayoutError("工程目录结构不受支持，不能创建临时任务目录。")
        if state != "new":
            raise LayoutError("工程目录尚未完成初始化，不能创建临时任务目录。")
        self._require_safe_workspace()
        return Path(tempfile.mkdtemp(prefix=f".{category}-", dir=self.workspace_dir))

    def is_within_workspace(self, path: str | os.PathLike[str]) -> bool:
        if not self._is_safe_directory(self.workspace_dir, self.project_dir):
            return False
        try:
            Path(path).resolve().relative_to(self.workspace_dir.resolve())
            return True
        except ValueError:
            return False

    def require_workspace_path(self, path: str | os.PathLike[str]) -> Path:
        resolved = Path(path).resolve()
        if not self.is_within_workspace(resolved):
            raise LayoutError(f"工作路径越界: {resolved}")
        return resolved


# ═══════════════════════════════════════════════════════════════════════
#  Workspace helpers (from workspace.py)
# ═══════════════════════════════════════════════════════════════════════

# Partition naming and workspace path helpers.
def partition_name(image_path):
    """Return the validated partition name represented by an image path."""
    name = os.path.basename(image_path)
    for suffix in ('.unsparse.img', '.new.dat.br', '.new.dat', '.img', '.win'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return ProjectLayout.validate_component(name, "分区")


def workspace_partition(partition):
    return str(V.layout.partition_dir(partition))


def workspace_temp(category):
    """Create a temporary directory inside WORKSPACE for intermediate files."""
    return str(V.layout.create_stage_dir(category)) + os.sep


# Metadata filename conventions.
def partition_metadata_names(partition):
    return (
        f'{partition}_contexts.txt',
        f'{partition}_fsconfig.txt',
        f'{partition}_info.txt',
        f'{partition}_space.txt',
        f'{partition}_size.txt',
        f'{partition}_kernel.txt',
        f'{partition}_file_contexts',
        f'{partition}_fs_config',
    )


def metadata_path(config_dir, partition, suffix):
    return Path(config_dir) / f'{partition}{suffix}'


# Safe extraction staging and metadata commit.
def create_partition_stage(partition, category, create_partition=True):
    """Prepare WORKSPACE/<partition>/ and WORKSPACE/config/ for direct extraction."""
    partition = ProjectLayout.validate_component(partition, '分区')
    partition_dir = Path(workspace_partition(partition))
    config_dir = Path(V.config)
    config_dir.mkdir(parents=True, exist_ok=True)
    if partition_dir.exists():
        shutil.rmtree(partition_dir)
    if create_partition:
        partition_dir.mkdir(parents=True, exist_ok=True)
    return partition_dir.parent, partition_dir, config_dir


def _destination_partition(distance, source):
    if distance:
        candidate = os.path.basename(os.path.normpath(distance))
    else:
        candidate = partition_name(source)
    V.layout.partition_dir(candidate)
    return candidate


def _stage_work_source(source, category):
    """Return the source path directly; INPUT is read but never modified."""
    return str(Path(source).resolve())


# Validate required extraction metadata before commit.
def _commit_extracted_partition(partition, stage_root, required_metadata, preserve_existing_metadata=False):
    """Direct extraction mode: files are already in WORKSPACE, just verify metadata."""
    config_dir = Path(V.config)
    required = set(required_metadata)
    available = set()
    for name in partition_metadata_names(partition):
        candidate = config_dir / name
        if candidate.exists() and candidate.is_file():
            available.add(name)
    if not required.issubset(available):
        missing = ', '.join(sorted(required - available))
        print(f'> {partition} 缺少必要 metadata: {missing}')
        return False
    return True


# Read previous image sizing metadata for repacking.
def load_image_json(dumpinfo, source_dir):
    with open(dumpinfo, "a+", encoding="utf-8") as f:
        f.seek(0)
        info = _json.load(f)
    inodes = info["a"]
    block_size = info["b"]
    per_group = info["c"]
    mount_point = info["d"]
    if mount_point != "/":
        mount_point = "/" + mount_point
    fsize = info["s"]
    blocks = ceil(int(fsize) / int(block_size))
    dsize = get_dir_size(source_dir)
    if dsize > int(fsize):
        minsize = dsize - int(fsize)
        if int(minsize) < 20971520:
            isize = int(dsize * 1.08)
            dsize = str(isize)
    else:
        dsize = fsize
    return fsize, dsize, inodes, block_size, blocks, per_group, mount_point


# Bind the selected project layout to workflow paths.
def envelop_project():
    """Initialize project layout from V.project."""
    project_name = os.path.basename(os.path.normpath(V.project))
    ProjectLayout.validate_component(project_name, "工程")
    V.project = project_name
    V.layout = ProjectLayout(os.path.join(PWD_DIR, project_name)).initialize()
    V.project_dir = str(V.layout.project_dir) + os.sep
    V.input = str(V.layout.input_dir) + os.sep
    V.out = str(V.layout.out_dir) + os.sep
    V.workspace = str(V.layout.workspace_dir) + os.sep
    V.config = str(V.layout.config_dir) + os.sep
