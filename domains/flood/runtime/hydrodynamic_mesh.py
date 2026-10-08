"""SQLite persistence for the hydrodynamic mesh."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable


class MeshDatabase:
    """Build and query the mesh database without forecast concerns."""

    def __init__(
        self,
        db_path: Path,
        grid_path: Path,
        project_dir: Path,
        parse_cells: Callable[[], tuple[list[tuple], dict[str, Any]]],
        tile_index_rows: Callable[[list[tuple]], list[tuple[int, int, int, int]]],
    ) -> None:
        self.db_path = db_path
        self.grid_path = grid_path
        self.project_dir = project_dir
        self.parse_cells = parse_cells
        self.tile_index_rows = tile_index_rows
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_ready(self) -> None:
        if self.is_ready():
            return
        with self._lock:
            if not self.is_ready():
                self.build()

    def is_ready(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            with self.connect() as conn:
                version = conn.execute(
                    "select value from mesh_meta where key = 'schema_version'",
                ).fetchone()
                return bool(version and version["value"] == "1")
        except sqlite3.Error:
            return False

    def build(self) -> None:
        if not self.grid_path.exists():
            raise FileNotFoundError(
                f"hydrodynamic grid not found: {self.grid_path}"
            )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.db_path.with_suffix(".sqlite.tmp")
        if temp_path.exists():
            temp_path.unlink()

        with sqlite3.connect(temp_path) as conn:
            conn.execute("pragma journal_mode = off")
            conn.execute("pragma synchronous = off")
            conn.execute(
                """
                create table cells(
                    cell_id integer primary key,
                    min_lon real not null,
                    min_lat real not null,
                    max_lon real not null,
                    max_lat real not null,
                    lon1 real not null,
                    lat1 real not null,
                    lon2 real not null,
                    lat2 real not null,
                    lon3 real not null,
                    lat3 real not null
                )
                """
            )
            conn.execute(
                """
                create table tile_cells(
                    z integer not null,
                    x integer not null,
                    y integer not null,
                    cell_id integer not null,
                    primary key(z, x, y, cell_id)
                )
                """
            )
            conn.execute("create table mesh_meta(key text primary key, value text not null)")
            cells, meta = self.parse_cells()
            conn.executemany(
                """
                insert into cells(
                    cell_id, min_lon, min_lat, max_lon, max_lat,
                    lon1, lat1, lon2, lat2, lon3, lat3
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cells,
            )
            conn.executemany(
                "insert into tile_cells(z, x, y, cell_id) values (?, ?, ?, ?)",
                self.tile_index_rows(cells),
            )
            meta.update({
                "schema_version": "1",
                "source_crs": "EPSG:4546",
                "map_crs": "EPSG:4326",
                "source_grid": str(self.grid_path.relative_to(self.project_dir)),
            })
            conn.executemany(
                "insert into mesh_meta(key, value) values (?, ?)",
                [(key, str(value)) for key, value in sorted(meta.items())],
            )
            conn.execute("create index idx_tile_cells on tile_cells(z, x, y)")
            conn.execute("create index idx_cells_bbox on cells(min_lon, min_lat, max_lon, max_lat)")
        temp_path.replace(self.db_path)


__all__ = ["MeshDatabase"]
