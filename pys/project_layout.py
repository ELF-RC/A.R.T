"""Project directory layout and path-safety helpers for A.R.T."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


class LayoutError(RuntimeError):
    """Base error for invalid A.R.T project layouts."""


class UnsupportedLayoutError(LayoutError):
    """Raised when an existing project does not use the current layout."""


_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ROOT_DIRS = frozenset({"INPUT", "OUT", "WORKSPACE"})
_RESERVED_WORKSPACE_NAMES = frozenset({"config", ".tmp", "INPUT", "OUT", "WORKSPACE"})


@dataclass(frozen=True)
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
    def tmp_dir(self) -> Path:
        return self.workspace_dir / ".tmp"

    @property
    def required_dirs(self) -> tuple[Path, ...]:
        return (
            self.input_dir,
            self.out_dir,
            self.workspace_dir,
            self.config_dir,
            self.tmp_dir,
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
        for protected in (self.workspace_dir.resolve(), self.config_dir.resolve(), self.tmp_dir.resolve()):
            if resolved == protected:
                raise LayoutError(f"分区目录指向受保护路径: {path}")
        return path

    def create_stage_dir(self, category: str) -> Path:
        category = self.validate_component(category, "临时任务")
        state = self.state()
        if state == "unsupported":
            raise UnsupportedLayoutError("工程目录结构不受支持，不能创建临时任务目录。")
        if state != "new":
            raise LayoutError("工程目录尚未完成初始化，不能创建临时任务目录。")
        self._require_safe_workspace()
        if any(path.is_symlink() or not path.is_dir() for path in self.required_dirs):
            raise LayoutError("工程目录尚未完成初始化，不能创建临时任务目录。")
        if self.tmp_dir.is_symlink() or (self.tmp_dir.exists() and not self.tmp_dir.is_dir()):
            raise LayoutError(f"临时目录无效: {self.tmp_dir}")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        if not self._is_safe_directory(self.tmp_dir, self.project_dir):
            raise LayoutError(f"临时目录无效: {self.tmp_dir}")
        workspace_device = os.stat(self.workspace_dir).st_dev
        if os.stat(self.tmp_dir).st_dev != workspace_device:
            raise LayoutError("WORKSPACE/.tmp 必须与 WORKSPACE 位于同一文件系统。")
        if self.config_dir.is_symlink() or not self.config_dir.is_dir():
            raise LayoutError(f"metadata 目录无效: {self.config_dir}")
        if os.stat(self.config_dir).st_dev != workspace_device:
            raise LayoutError("WORKSPACE/config 必须与 WORKSPACE 位于同一文件系统。")
        return Path(tempfile.mkdtemp(prefix=f"{category}-", dir=self.tmp_dir))

    def is_within_workspace(self, path: str | os.PathLike[str]) -> bool:
        if not self._is_safe_directory(self.workspace_dir, self.project_dir):
            return False
        try:
            Path(path).resolve().relative_to(self.workspace_dir.resolve())
            return True
        except ValueError:
            return False

    def is_within_tmp(self, path: str | os.PathLike[str]) -> bool:
        if not self._is_safe_directory(self.tmp_dir, self.project_dir):
            return False
        try:
            Path(path).resolve().relative_to(self.tmp_dir.resolve())
            return True
        except ValueError:
            return False

    def require_workspace_path(self, path: str | os.PathLike[str]) -> Path:
        resolved = Path(path).resolve()
        if not self.is_within_workspace(resolved):
            raise LayoutError(f"工作路径越界: {resolved}")
        return resolved
