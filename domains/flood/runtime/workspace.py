from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_DIR = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = PROJECT_DIR / "local" / "runtime" / "flood"
WORKSPACES_DIR = RUNTIME_ROOT / "workspaces"
SHARED_CACHE_DIR = RUNTIME_ROOT / "cache"

_scoped_workspace_id: ContextVar[str | None] = ContextVar(
    "flood_workspace_id",
    default=None,
)


def workspace_retention_count() -> int:
    try:
        return max(1, int(os.environ.get("FLOOD_WORKSPACE_RETENTION_COUNT", "3")))
    except ValueError:
        return 3


class WorkspaceManager:
    def __init__(self, root: Path = WORKSPACES_DIR,
                 retention_count: int | None = None):
        self.root = root
        self.retention_count = (
            max(1, retention_count)
            if retention_count is not None
            else workspace_retention_count()
        )
        self._active_id: str | None = None
        self._lock = threading.RLock()

    @property
    def active_id(self) -> str | None:
        scoped = _scoped_workspace_id.get()
        if scoped:
            return scoped
        with self._lock:
            return self._active_id

    def create(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        workspace_id = f"run_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        path = self.root / workspace_id
        path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "workspace_id": workspace_id,
            "domain": "flood",
            "created_at": now.isoformat(),
            "status": "active",
        }
        self._write_json(path / "manifest.json", manifest)
        with self._lock:
            self._active_id = workspace_id
        self.prune()
        return manifest

    def prune(self) -> list[str]:
        if not self.root.exists():
            return []
        with self._lock:
            active_id = self._active_id
            workspace_paths = sorted(
                (
                    path for path in self.root.iterdir()
                    if path.is_dir() and path.name.startswith("run_")
                ),
                key=self._created_key,
                reverse=True,
            )
            keep = {path.name for path in workspace_paths[:self.retention_count]}
            if active_id:
                keep.add(active_id)
            removed = []
            for path in workspace_paths:
                if path.name in keep:
                    continue
                shutil.rmtree(path)
                removed.append(path.name)
            return removed

    @staticmethod
    def _created_key(path: Path) -> tuple[str, str]:
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            created_at = str(manifest.get("created_at") or "")
        except (OSError, json.JSONDecodeError):
            created_at = ""
        return created_at, path.name

    def path(self, workspace_id: str | None = None, *, create: bool = False) -> Path:
        selected = workspace_id or self.active_id
        if not selected:
            if create:
                selected = self.create()["workspace_id"]
            else:
                selected = "_inactive"
        path = self.root / selected
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def update_manifest(self, **values: Any) -> None:
        workspace_id = self.active_id
        if not workspace_id:
            return
        path = self.path(workspace_id, create=True) / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {"workspace_id": workspace_id, "domain": "flood"}
        manifest.update(values)
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(path, manifest)

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f"{path.suffix}.tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp.replace(path)


WORKSPACES = WorkspaceManager()


def active_workspace_id() -> str | None:
    return WORKSPACES.active_id


def workspace_dir(*, create: bool = False) -> Path:
    return WORKSPACES.path(create=create)


@contextmanager
def workspace_scope(workspace_id: str | None) -> Iterator[None]:
    token = _scoped_workspace_id.set(workspace_id)
    try:
        yield
    finally:
        _scoped_workspace_id.reset(token)
