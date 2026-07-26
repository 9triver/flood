from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .boundary_flow import (
    configured_boundary_flow_csv_path,
    parse_boundary_flow_time,
)
from .workspace import RUNTIME_ROOT


BUILTIN_PLAYBACK_SOURCE_ID = "builtin-boundary-flow"
PLAYBACK_SOURCES_DIR = RUNTIME_ROOT / "playback_sources"
MAX_PLAYBACK_SOURCE_BYTES = 5 * 1024 * 1024
MIN_PLAYBACK_SOURCE_ROWS = 25
REQUIRED_BOUNDARY_FLOW_COLUMNS = (
    "time_period_end",
    "rainfall_mm",
    "interval1_outlet_flow_m3s",
    "interval2_outlet_flow_m3s",
    "reservoir_outlet_flow_m3s",
    "release_m3s",
    "end_level_m",
)
_UPLOADED_SOURCE_ID = re.compile(r"source_[0-9a-f]{12}")


class PlaybackSourceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PlaybackSource:
    source_id: str
    name: str
    original_filename: str
    kind: str
    csv_path: Path
    row_count: int
    start_time: str
    end_time: str
    uploaded_at: str | None
    validation_status: str = "valid"

    def public(self, *, selected: bool = False) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "original_filename": self.original_filename,
            "kind": self.kind,
            "row_count": self.row_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "uploaded_at": self.uploaded_at,
            "validation_status": self.validation_status,
            "selected": selected,
        }


class PlaybackSourceRegistry:
    """Validate and retain reusable boundary-flow playback inputs."""

    def __init__(
        self,
        root: Path = PLAYBACK_SOURCES_DIR,
        builtin_path: Path | None = None,
    ):
        self.root = root
        self.builtin_path = builtin_path or configured_boundary_flow_csv_path()
        self._lock = threading.RLock()

    @property
    def selected_source_id(self) -> str:
        with self._lock:
            try:
                value = json.loads(self._selected_path.read_text(encoding="utf-8"))
                source_id = str(value.get("source_id") or "")
                self.get(source_id)
                return source_id
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                return BUILTIN_PLAYBACK_SOURCE_ID

    def list_sources(self) -> dict[str, Any]:
        with self._lock:
            selected_id = self.selected_source_id
            sources = [self._builtin_source()]
            if self.root.exists():
                for directory in sorted(self.root.glob("source_*")):
                    try:
                        sources.append(self._uploaded_source(directory.name))
                    except (
                        OSError,
                        json.JSONDecodeError,
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        continue
            uploaded = sorted(
                (source for source in sources if source.kind == "uploaded"),
                key=lambda source: source.uploaded_at or "",
                reverse=True,
            )
            ordered = [sources[0], *uploaded]
            return {
                "selected_source_id": selected_id,
                "sources": [
                    source.public(selected=source.source_id == selected_id)
                    for source in ordered
                ],
            }

    def get(self, source_id: str | None) -> PlaybackSource:
        selected = str(source_id or BUILTIN_PLAYBACK_SOURCE_ID)
        if selected == BUILTIN_PLAYBACK_SOURCE_ID:
            return self._builtin_source()
        if not _UPLOADED_SOURCE_ID.fullmatch(selected):
            raise ValueError("演进数据不存在")
        return self._uploaded_source(selected)

    def upload(self, filename: str, content: bytes) -> PlaybackSource:
        original_filename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not original_filename or Path(original_filename).suffix.lower() != ".csv":
            raise PlaybackSourceValidationError("请选择 CSV 文件")
        if not content:
            raise PlaybackSourceValidationError("CSV 文件为空")
        if len(content) > MAX_PLAYBACK_SOURCE_BYTES:
            raise PlaybackSourceValidationError("CSV 文件不能超过 5 MB")

        summary = validate_playback_source(content)
        source_id = f"source_{uuid.uuid4().hex[:12]}"
        source_dir = self.root / source_id
        uploaded_at = datetime.now(timezone.utc).isoformat()
        metadata = {
            "source_id": source_id,
            "name": Path(original_filename).stem,
            "original_filename": original_filename,
            "kind": "uploaded",
            "row_count": summary["row_count"],
            "start_time": summary["start_time"],
            "end_time": summary["end_time"],
            "uploaded_at": uploaded_at,
            "validation_status": "valid",
        }
        with self._lock:
            source_dir.mkdir(parents=True, exist_ok=False)
            try:
                (source_dir / "source.csv").write_bytes(content)
                _write_json(source_dir / "metadata.json", metadata)
            except Exception:
                shutil.rmtree(source_dir, ignore_errors=True)
                raise
        return self._source_from_metadata(metadata, source_dir / "source.csv")

    def select(self, source_id: str) -> PlaybackSource:
        with self._lock:
            source = self.get(source_id)
            _write_json(self._selected_path, {
                "source_id": source.source_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            return source

    def snapshot(
        self,
        source_id: str,
        workspace_path: Path,
    ) -> tuple[Path, dict[str, Any]]:
        with self._lock:
            source = self.get(source_id)
            inputs_dir = workspace_path / "inputs"
            inputs_dir.mkdir(parents=True, exist_ok=True)
            csv_path = inputs_dir / "boundary_flow.csv"
            shutil.copy2(source.csv_path, csv_path)
            metadata = source.public(selected=True)
            metadata["workspace_path"] = "inputs/boundary_flow.csv"
            _write_json(inputs_dir / "playback_source.json", metadata)
            self.select(source.source_id)
            return csv_path, metadata

    @property
    def _selected_path(self) -> Path:
        return self.root / ".selected.json"

    def _builtin_source(self) -> PlaybackSource:
        content = self.builtin_path.read_bytes()
        summary = validate_playback_source(content)
        return PlaybackSource(
            source_id=BUILTIN_PLAYBACK_SOURCE_ID,
            name="内置演进数据",
            original_filename=self.builtin_path.name,
            kind="builtin",
            csv_path=self.builtin_path,
            row_count=summary["row_count"],
            start_time=summary["start_time"],
            end_time=summary["end_time"],
            uploaded_at=None,
        )

    def _uploaded_source(self, source_id: str) -> PlaybackSource:
        if not _UPLOADED_SOURCE_ID.fullmatch(source_id):
            raise ValueError("演进数据不存在")
        source_dir = self.root / source_id
        metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
        if str(metadata.get("source_id") or "") != source_id:
            raise ValueError("演进数据元数据无效")
        csv_path = source_dir / "source.csv"
        if not csv_path.is_file():
            raise ValueError("演进数据文件不存在")
        return self._source_from_metadata(metadata, csv_path)

    @staticmethod
    def _source_from_metadata(metadata: dict[str, Any], csv_path: Path) -> PlaybackSource:
        return PlaybackSource(
            source_id=str(metadata["source_id"]),
            name=str(metadata["name"]),
            original_filename=str(metadata["original_filename"]),
            kind=str(metadata["kind"]),
            csv_path=csv_path,
            row_count=int(metadata["row_count"]),
            start_time=str(metadata["start_time"]),
            end_time=str(metadata["end_time"]),
            uploaded_at=(
                str(metadata["uploaded_at"])
                if metadata.get("uploaded_at")
                else None
            ),
            validation_status=str(metadata.get("validation_status") or "valid"),
        )


def validate_playback_source(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PlaybackSourceValidationError("CSV 必须使用 UTF-8 编码") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = tuple(reader.fieldnames or ())
    if set(columns) != set(REQUIRED_BOUNDARY_FLOW_COLUMNS) or len(columns) != len(
        REQUIRED_BOUNDARY_FLOW_COLUMNS
    ):
        raise PlaybackSourceValidationError(
            "CSV 字段必须为：" + ", ".join(REQUIRED_BOUNDARY_FLOW_COLUMNS)
        )

    previous_time: datetime | None = None
    start_time = ""
    end_time = ""
    row_count = 0
    numeric_columns = REQUIRED_BOUNDARY_FLOW_COLUMNS[1:]
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise PlaybackSourceValidationError(
                f"第 {line_number} 行包含字段之外的值"
            )
        try:
            observed_at = parse_boundary_flow_time(
                str(row.get("time_period_end") or "")
            )
        except ValueError as exc:
            raise PlaybackSourceValidationError(
                f"第 {line_number} 行 time_period_end 格式应为 "
                "YYYY-MM-DD HH:MM 或 YYYY/M/D H:MM"
            ) from exc
        if previous_time is not None and observed_at != previous_time + timedelta(hours=1):
            raise PlaybackSourceValidationError(
                f"第 {line_number} 行时间必须比上一行晚 1 小时"
            )
        for column in numeric_columns:
            raw = str(row.get(column) or "").strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise PlaybackSourceValidationError(
                    f"第 {line_number} 行 {column} 必须是数值"
                ) from exc
            if not math.isfinite(value):
                raise PlaybackSourceValidationError(
                    f"第 {line_number} 行 {column} 必须是有限数值"
                )
        if not start_time:
            start_time = observed_at.strftime("%Y-%m-%d %H:%M")
        end_time = observed_at.strftime("%Y-%m-%d %H:%M")
        previous_time = observed_at
        row_count += 1

    if row_count < MIN_PLAYBACK_SOURCE_ROWS:
        raise PlaybackSourceValidationError(
            f"CSV 至少需要 {MIN_PLAYBACK_SOURCE_ROWS} 条逐小时数据"
        )
    return {
        "row_count": row_count,
        "start_time": start_time,
        "end_time": end_time,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
