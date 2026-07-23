# -*- coding: utf-8 -*-
"""
Flood CNN v2: train directly from binary inundation .dat files.

Expected directory layout:

    .
    |-- GT.txt
    |-- TIME.txt        optional
    |-- CANSHU.txt      optional
    |-- EPSG.txt        optional
    |-- TRAIN/
    |   |-- case_001/
    |   |   |-- *_inundation*.dat or *_淹没过程*.dat
    |   |   |-- *.csv   four flow boundary time series
    |   |-- ...
    |-- TEST/
        |-- case_x/
            |-- optional *_淹没过程*.dat for evaluation
            |-- *.csv

Binary .dat format used here:

    int32   cell_count
    repeat frame_count:
        float64 ole_time
        float32[cell_count, 4] = [Z, H, U, V]

The model does not use boundary-cell locations. All boundary CSV files are
treated as global hydrologic forcing features and concatenated in a stable
name order.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import struct
from collections.abc import Iterable
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

def configure_numeric_precision() -> None:
    if torch.cuda.is_available():
        if hasattr(torch.backends.cuda.matmul, "fp32_precision"):
            torch.backends.cuda.matmul.fp32_precision = "ieee"
            torch.backends.cudnn.conv.fp32_precision = "ieee"
        else:
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = False
        print("[precision] FP32 highest, TF32 disabled")
    else:
        print("[precision] FP32 highest")


def set_random_seed(seed: int) -> None:
    """Seed all RNGs and use deterministic CUDA kernels where available."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    # adaptive pooling backward has no deterministic CUDA implementation in
    # some PyTorch versions, so unsupported operations warn instead of aborting.
    torch.use_deterministic_algorithms(True, warn_only=True)
    print(f"[seed] random_seed={seed}, deterministic=True, unsupported_ops=warn")


CONFIG = {
    # ===== Run mode for direct execution =====
    # "auto": train if model is missing or force_retrain=True, then predict TEST.
    # "train": only train and save the model.
    # "predict": load saved model and only predict TEST.
    "mode": "auto",

    # ===== Paths =====
    "train_dir": "./TRAIN",
    "test_dir": "./TEST",
    "grid_file": "./GT.txt",
    "model_path": "./OUTPUT/FLOOD_CNN.pth",
    "output_dir": "./OUTPUT",
    "test_output_dir": "./OUTPUT/TEST_RESULTS",
    "cache_dir": "./OUTPUT/CACHE",
    "force_retrain": False,
    "random_seed": 20260628,

    # ===== Boundary flow input =====
    "num_boundaries": 4,
    "flow_history": 48,
    "csv_time_step": 1.0,

    # ===== Time sampling =====
    # Large .dat files are expensive and different cases have different native
    # dt values. Prefer hour-based sampling so all cases use the same clock.
    # 0.5 means use 00:30, 01:00, ..., 24:00.
    "train_sample_interval_hours": 0.5,
    "output_interval_hours": 0.5,
    # Legacy fallback if train_sample_interval_hours/output_interval_hours is
    # set to None. Kept for experiments with native frame strides.
    "train_frame_stride": 6,

    # ===== Normalization and cache =====
    # The depth normalizer is fitted on the same frames used for training.
    "normalizer_frame_stride": 24,
    "normalizer_cell_sample": 80000,
    "cache_labels": True,
    "rebuild_cache": False,
    "cache_dtype": "float32",

    # ===== Training =====
    "epochs": 1000,
    "batch_size": 8,
    "lr": 1e-3,
    "weight_decay": 1e-5,
    "val_split": 0.15,
    "early_stopping": True,
    "early_stop_patience": 100,
    "early_stop_min_delta": 1e-4,
    "lr_scheduler": True,
    "lr_reduce_factor": 0.5,
    "lr_reduce_patience": 12,
    "min_lr": 1e-5,

    # ===== Model =====
    "conv_channels": [16, 32, 64],
    "fc_units": [128, 64],
    "dropout": 0.2,

    # ===== Loss and depth processing =====
    "depth_log_shift": 0.01,
    # Use 0.05 for practical inundation-area statistics. A 0.01m threshold is
    # very sensitive to centimeter-level shallow false positives.
    "depth_threshold": 0.05,
    "max_depth_cap": 10.0,
    "miss_penalty": 2.0,
    "dry_penalty": 4.0,
    "loss_mse": 0.30,
    "loss_bce": 0.20,
    "loss_flooded": 0.40,
    "loss_dry": 0.10,
    "predict_frame_stride": 1,
    "export_time_series_csv": True,
    "coord_system": "EPSG:4546",
}


def get_device() -> torch.device:
    if torch.cuda.is_available():
        print(f"[device] CUDA: {torch.cuda.get_device_name(0)}")
        return torch.device("cuda")
    print("[device] CPU")
    return torch.device("cpu")


def read_key_value_config(path: Path) -> dict[str, float]:
    result: dict[str, float] = {}
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            value_part, _, key_part = line.partition("#")
            key = key_part.strip().split()[0] if key_part.strip() else ""
            value = value_part.strip()
            if key and value:
                try:
                    result[key] = float(value)
                except ValueError:
                    pass
    return result


def load_local_config() -> None:
    root = Path(".")

    epsg_path = root / "EPSG.txt"
    if epsg_path.exists():
        digits = "".join(ch for ch in epsg_path.read_text(encoding="utf-8").strip() if ch.isdigit())
        if digits:
            CONFIG["coord_system"] = f"EPSG:{digits}"

    time_cfg = read_key_value_config(root / "TIME.txt")
    apply_time_config(time_cfg)

    train_cfg = read_key_value_config(root / "CANSHU.txt")
    for key in ["epochs", "batch_size", "random_seed"]:
        if key in train_cfg:
            CONFIG[key] = int(train_cfg[key])
    for key in [
        "lr",
        "weight_decay",
        "val_split",
        "dropout",
        "early_stop_min_delta",
        "lr_reduce_factor",
        "min_lr",
    ]:
        if key in train_cfg:
            CONFIG[key] = float(train_cfg[key])
    for key in ["early_stop_patience", "lr_reduce_patience"]:
        if key in train_cfg:
            CONFIG[key] = int(train_cfg[key])

    print("[config]")
    for key in [
        "train_dir",
        "test_dir",
        "grid_file",
        "flow_history",
        "csv_time_step",
        "train_sample_interval_hours",
        "output_interval_hours",
        "train_frame_stride",
        "cache_labels",
        "cache_dtype",
        "depth_threshold",
        "dry_penalty",
        "loss_dry",
        "epochs",
        "batch_size",
        "random_seed",
        "lr",
        "weight_decay",
        "val_split",
        "early_stopping",
        "early_stop_patience",
        "lr_scheduler",
        "lr_reduce_patience",
        "dropout",
        "coord_system",
    ]:
        print(f"  {key}: {CONFIG[key]}")


def apply_time_config(time_cfg: dict[str, float]) -> None:
    if "flow_history" in time_cfg:
        CONFIG["flow_history"] = int(time_cfg["flow_history"])
    if "csv_time_step" in time_cfg:
        CONFIG["csv_time_step"] = float(time_cfg["csv_time_step"])
    for source_key in ["train_sample_interval_hours", "train_interval"]:
        if source_key in time_cfg:
            CONFIG["train_sample_interval_hours"] = float(time_cfg[source_key])
            break
    for source_key in ["output_interval_hours", "output_interval"]:
        if source_key in time_cfg:
            CONFIG["output_interval_hours"] = float(time_cfg[source_key])
            break


def make_regular_hours(duration_hours: float, interval_hours: float) -> list[float]:
    """Return interval, 2*interval, ..., duration_hours."""
    if duration_hours <= 0:
        return []
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    count = int(math.floor(duration_hours / interval_hours + 1e-9))
    hours = (np.arange(1, count + 1, dtype=np.float64) * interval_hours).tolist()
    if not hours or abs(hours[-1] - duration_hours) > 1e-6:
        hours.append(float(duration_hours))
    return [float(h) for h in hours]


def nearest_frame_indices(frame_hours: np.ndarray, target_hours: Iterable[float]) -> list[int]:
    indices = sorted({int(np.argmin(np.abs(frame_hours - hour))) for hour in target_hours})
    return indices


class GridParser:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.cell_ids: list[int] = []
        self.bounds: dict[str, float] | None = None

    def parse(self) -> "GridParser":
        if not self.path.exists():
            raise FileNotFoundError(f"Grid file not found: {self.path}")

        with self.path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        header = lines[0].split()
        node_count = int(header[0])
        quad_count = int(header[2])
        tri_count = int(header[3])

        idx = 1
        xs, ys = [], []
        for _ in range(node_count):
            parts = lines[idx].split()
            xs.append(float(parts[1]))
            ys.append(float(parts[2]))
            idx += 1

        cell_ids = []
        for _ in range(quad_count):
            parts = lines[idx].split()
            cell_ids.append(int(parts[0]))
            idx += 1
        for _ in range(tri_count):
            parts = lines[idx].split()
            cell_ids.append(int(parts[0]))
            idx += 1

        self.cell_ids = sorted(cell_ids)
        self.bounds = {
            "xmin": min(xs),
            "xmax": max(xs),
            "ymin": min(ys),
            "ymax": max(ys),
        }
        print(f"[grid] cells={len(self.cell_ids)}, elevation=not used")
        return self


class DatFloodReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.file = None
        self.cell_count = 0
        self.frame_bytes = 0
        self.frame_count = 0
        self.ole_times: np.ndarray | None = None
        self.hours: np.ndarray | None = None

    def open(self) -> "DatFloodReader":
        self.file = self.path.open("rb")
        header = self.file.read(4)
        if len(header) != 4:
            raise ValueError(f"Empty or invalid dat file: {self.path}")
        self.cell_count = struct.unpack("<i", header)[0]
        self.frame_bytes = 8 + self.cell_count * 16
        size = self.path.stat().st_size
        payload = size - 4
        if payload <= 0 or payload % self.frame_bytes != 0:
            raise ValueError(
                f"Unexpected dat size: {self.path}, size={size}, "
                f"cell_count={self.cell_count}, frame_bytes={self.frame_bytes}"
            )
        self.frame_count = payload // self.frame_bytes
        self.ole_times = self._read_ole_times()
        # The integer part is an OLE date. For these model outputs the useful
        # simulation time is the time-of-day fraction.
        self.hours = ((self.ole_times - np.floor(self.ole_times[0])) * 24.0).astype(np.float64)
        dt = self.dt_seconds
        print(
            f"[dat] {self.path.name}: cells={self.cell_count}, frames={self.frame_count}, "
            f"dt={dt:.3f}s, hours=[{self.hours[0]:.3f}, {self.hours[-1]:.3f}]"
        )
        return self

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    @property
    def dt_seconds(self) -> float:
        if self.ole_times is None or len(self.ole_times) < 2:
            return 0.0
        return float(np.median(np.diff(self.ole_times)) * 86400.0)

    def _read_ole_times(self) -> np.ndarray:
        assert self.file is not None
        times = np.empty(self.frame_count, dtype=np.float64)
        for i in range(self.frame_count):
            self.file.seek(4 + i * self.frame_bytes)
            times[i] = struct.unpack("<d", self.file.read(8))[0]
        return times

    def read_frame(self, frame_idx: int) -> np.ndarray:
        assert self.file is not None
        if frame_idx < 0 or frame_idx >= self.frame_count:
            raise IndexError(frame_idx)
        offset = 4 + frame_idx * self.frame_bytes + 8
        self.file.seek(offset)
        raw = self.file.read(self.cell_count * 16)
        if len(raw) != self.cell_count * 16:
            raise EOFError(f"Truncated frame {frame_idx} in {self.path}")
        return np.frombuffer(raw, dtype="<f4").reshape(self.cell_count, 4).copy()

    def read_depth(self, frame_idx: int) -> np.ndarray:
        return self.read_frame(frame_idx)[:, 1].astype(np.float32, copy=False)

    def read_depth_at_hour(self, hour: float) -> np.ndarray:
        assert self.hours is not None
        idx = int(np.argmin(np.abs(self.hours - hour)))
        return self.read_depth(idx)


class FlowBoundary:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.hours: np.ndarray | None = None
        self.flows: np.ndarray | None = None

    def parse(self) -> "FlowBoundary":
        times, flows, header = [], [], ""
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t ")
            reader = csv.reader(f, dialect)
            rows = [[c.strip() for c in row if c.strip()] for row in reader]

        rows = [row for row in rows if row]
        if not rows:
            raise ValueError(f"Empty flow csv: {self.path}")

        first = rows[0]
        start_idx = 0
        if any(re.search(r"[A-Za-z\u4e00-\u9fff]", col) for col in first):
            header = ",".join(first).lower()
            start_idx = 1

        for row in rows[start_idx:]:
            if len(row) < 2:
                continue
            try:
                times.append(float(row[0]))
                flows.append(float(row[1]))
            except ValueError:
                continue

        if not times:
            raise ValueError(f"No numeric time/flow rows in {self.path}")

        t = np.asarray(times, dtype=np.float64)
        q = np.asarray(flows, dtype=np.float32)
        if "time(s)" in header or "秒" in header or t.max() > 1000:
            h = t / 3600.0
        elif t.max() <= 10.0:
            # Old files often used days.
            h = t * 24.0
        else:
            h = t
        self.hours = h
        self.flows = q
        print(f"[flow] {self.path.name}: points={len(q)}, hours=[{h[0]:.2f}, {h[-1]:.2f}], peak={q.max():.3f}")
        return self

    def sequence(self, end_hour: float, history_hours: int, step_hours: float = 1.0) -> np.ndarray:
        assert self.hours is not None and self.flows is not None
        count = int(history_hours)
        sample_hours = end_hour - np.arange(count - 1, -1, -1, dtype=np.float64) * step_hours
        sample_hours = np.maximum(sample_hours, self.hours[0])
        return np.interp(sample_hours, self.hours, self.flows).astype(np.float32)


class Scenario:
    def __init__(self, path: str | Path, require_dat: bool):
        self.path = Path(path)
        self.name = self.path.name
        self.dat_path: Path | None = None
        self.reader: DatFloodReader | None = None
        self.flows: list[FlowBoundary] = []
        self.require_dat = require_dat

    def parse(self) -> "Scenario":
        dat_candidates = [p for p in self.path.glob("*.dat") if p.is_file()]
        if dat_candidates:
            # The inundation process file is by far the largest .dat in this layout.
            self.dat_path = max(dat_candidates, key=lambda p: p.stat().st_size)
            self.reader = DatFloodReader(self.dat_path).open()
        elif self.require_dat:
            raise FileNotFoundError(f"No inundation .dat found in {self.path}")

        csv_paths = sorted(self.path.glob("*.csv"), key=self._boundary_sort_key)
        if not csv_paths:
            raise FileNotFoundError(f"No boundary csv files found in {self.path}")

        self.flows = [FlowBoundary(p).parse() for p in csv_paths]
        expected = CONFIG.get("num_boundaries")
        if expected and len(self.flows) != expected:
            print(f"[warn] {self.name}: expected {expected} boundary csv files, found {len(self.flows)}")
        return self

    @staticmethod
    def _boundary_sort_key(path: Path) -> tuple[int, str]:
        name = path.name
        match = re.match(r"^(\d+)[_-]", name)
        if match:
            return int(match.group(1)), name
        return 10_000, name

    @property
    def num_boundaries(self) -> int:
        return len(self.flows)

    @property
    def duration_hours(self) -> float:
        candidates = []
        if self.reader is not None and self.reader.hours is not None:
            candidates.append(float(self.reader.hours[-1]))
        for flow in self.flows:
            if flow.hours is not None:
                candidates.append(float(flow.hours[-1]))
        return max(candidates) if candidates else 0.0

    def feature_vector(self, hour: float, history_hours: int, total_boundaries: int) -> np.ndarray:
        pieces = [
            flow.sequence(hour, history_hours, CONFIG["csv_time_step"])
            for flow in self.flows
        ]
        if len(pieces) < total_boundaries:
            pieces.extend([np.zeros(history_hours, dtype=np.float32) for _ in range(total_boundaries - len(pieces))])
        elif len(pieces) > total_boundaries:
            pieces = pieces[:total_boundaries]
        flow_features = np.concatenate(pieces)
        time_norm = np.array([hour / max(self.duration_hours, 1e-6)], dtype=np.float32)
        return np.concatenate([flow_features, time_norm]).astype(np.float32)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()


class DepthNormalizer:
    def __init__(self):
        self.params: dict[str, float] = {}

    def fit(self, samples: np.ndarray) -> "DepthNormalizer":
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            samples = np.asarray([0.0], dtype=np.float32)
        pos = samples[samples > 0]
        if pos.size == 0:
            pos = np.asarray([CONFIG["depth_threshold"]], dtype=np.float32)
        p99 = float(np.percentile(pos, 99.5))
        max_value = max(float(pos.max()), p99, CONFIG["depth_threshold"])
        self.params = {
            "log_shift": float(CONFIG["depth_log_shift"]),
            "norm_max": float(min(max_value * 1.15, CONFIG["max_depth_cap"])),
            "raw_max": float(pos.max()),
            "p99_5": p99,
        }
        print(
            f"[normalizer] raw_max={self.params['raw_max']:.4f}, "
            f"p99.5={p99:.4f}, norm_max={self.params['norm_max']:.4f}"
        )
        return self

    def transform(self, depth: np.ndarray) -> np.ndarray:
        shift = self.params["log_shift"]
        log_max = math.log1p(self.params["norm_max"] / shift)
        y = np.log1p(np.maximum(depth, 0.0) / shift) / log_max
        return np.clip(y, 0.0, 1.5).astype(np.float32)

    def inverse(self, y: np.ndarray) -> np.ndarray:
        shift = self.params["log_shift"]
        log_max = math.log1p(self.params["norm_max"] / shift)
        depth = shift * np.expm1(np.clip(y, 0.0, 1.5) * log_max)
        return np.clip(depth, 0.0, CONFIG["max_depth_cap"]).astype(np.float32)

    def state_dict(self) -> dict[str, float]:
        return dict(self.params)

    def load_state_dict(self, params: dict[str, float]) -> None:
        self.params = dict(params)


class DatFloodDataset(Dataset):
    def __init__(
        self,
        scenarios: list[Scenario],
        normalizer: DepthNormalizer | None = None,
        flow_min: float | None = None,
        flow_max: float | None = None,
        training: bool = True,
    ):
        self.scenarios = scenarios
        self.training = training
        self.flow_history = int(CONFIG["flow_history"])
        self.num_boundaries = max(s.num_boundaries for s in scenarios)
        self.samples: list[tuple[int, int]] = []
        self.label_cache_paths: list[Path | None] = []
        self.num_cells = scenarios[0].reader.cell_count if scenarios[0].reader else 0

        for si, scenario in enumerate(scenarios):
            if scenario.reader is None:
                continue
            if scenario.reader.cell_count != self.num_cells:
                raise ValueError(f"Cell count mismatch in {scenario.name}")
            interval = CONFIG.get("train_sample_interval_hours")
            if interval is None:
                stride = max(1, int(CONFIG["train_frame_stride"]))
                frame_indices = list(range(0, scenario.reader.frame_count, stride))
                if frame_indices[-1] != scenario.reader.frame_count - 1:
                    frame_indices.append(scenario.reader.frame_count - 1)
            else:
                assert scenario.reader.hours is not None
                target_hours = make_regular_hours(float(scenario.reader.hours[-1]), float(interval))
                frame_indices = nearest_frame_indices(scenario.reader.hours, target_hours)
            self.samples.extend((si, fi) for fi in frame_indices)

        if not self.samples:
            raise ValueError("No training samples were created")

        all_flow_values = []
        for scenario in scenarios:
            for flow in scenario.flows:
                all_flow_values.append(flow.flows)
        all_flow = np.concatenate(all_flow_values) if all_flow_values else np.asarray([0.0], dtype=np.float32)
        self.flow_min = float(all_flow.min()) if flow_min is None else float(flow_min)
        self.flow_max = float(all_flow.max()) if flow_max is None else float(flow_max)

        if normalizer is None:
            normalizer = DepthNormalizer().fit(self._collect_depth_samples())
        self.normalizer = normalizer

        self.input_dim = self.num_boundaries * self.flow_history + 1
        self._prepare_label_cache()
        print(
            f"[dataset] scenarios={len(scenarios)}, samples={len(self.samples)}, "
            f"input_dim={self.input_dim}, cells={self.num_cells}, flow=[{self.flow_min:.3f}, {self.flow_max:.3f}]"
        )

    def _cache_fingerprint(self) -> str:
        payload = {
            "normalizer": self.normalizer.state_dict(),
            "num_cells": self.num_cells,
            "sample_interval_hours": CONFIG.get("train_sample_interval_hours"),
            "depth_threshold": CONFIG["depth_threshold"],
            "dry_penalty": CONFIG["dry_penalty"],
            "loss_dry": CONFIG["loss_dry"],
            "cache_dtype": CONFIG["cache_dtype"],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:16]

    def _label_cache_path(self, scenario: Scenario, frame_idx: int) -> Path:
        reader_name = scenario.dat_path.stem if scenario.dat_path is not None else "no_dat"
        root = Path(CONFIG["cache_dir"]) / self._cache_fingerprint() / scenario.name / reader_name
        return root / f"h_norm_frame_{frame_idx:06d}.npy"

    def _prepare_label_cache(self) -> None:
        self.label_cache_paths = [None] * len(self.samples)
        if not CONFIG.get("cache_labels", True):
            print("[cache] label cache disabled")
            return

        cache_dtype = np.dtype(CONFIG.get("cache_dtype", "float16"))
        missing: list[tuple[int, Path]] = []
        rebuild = bool(CONFIG.get("rebuild_cache", False))

        for i, (scenario_idx, frame_idx) in enumerate(self.samples):
            scenario = self.scenarios[scenario_idx]
            path = self._label_cache_path(scenario, frame_idx)
            self.label_cache_paths[i] = path
            if rebuild or not path.exists():
                missing.append((i, path))

        if not missing:
            print(f"[cache] using cached labels: {len(self.samples)} files")
            return

        print(
            f"[cache] building label cache: {len(missing)}/{len(self.samples)} files "
            f"-> {Path(CONFIG['cache_dir']).resolve()}"
        )
        for done, (sample_idx, path) in enumerate(missing, 1):
            scenario_idx, frame_idx = self.samples[sample_idx]
            scenario = self.scenarios[scenario_idx]
            reader = scenario.reader
            if reader is None:
                raise RuntimeError(f"Cannot cache sample without dat reader: {scenario.name}")
            depth = reader.read_depth(frame_idx)
            y = self.normalizer.transform(depth).astype(cache_dtype, copy=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, y)
            if done == 1 or done % 25 == 0 or done == len(missing):
                print(f"[cache] {done}/{len(missing)} {scenario.name} frame={frame_idx}")

    def _collect_depth_samples(self) -> np.ndarray:
        rng = np.random.default_rng(20260617)
        pieces = []
        cell_sample = int(CONFIG["normalizer_cell_sample"])
        for scenario_idx, frame_idx in self.samples:
            scenario = self.scenarios[scenario_idx]
            reader = scenario.reader
            if reader is None:
                continue
            depth = reader.read_depth(frame_idx)
            if 0 < cell_sample < depth.size:
                idx = rng.choice(depth.size, size=cell_sample, replace=False)
                depth = depth[idx]
            pieces.append(depth)
        return np.concatenate(pieces) if pieces else np.asarray([0.0], dtype=np.float32)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        scenario_idx, frame_idx = self.samples[idx]
        scenario = self.scenarios[scenario_idx]
        reader = scenario.reader
        assert reader is not None and reader.hours is not None
        hour = float(reader.hours[frame_idx])

        x = scenario.feature_vector(hour, self.flow_history, self.num_boundaries)
        x[:-1] = (x[:-1] - self.flow_min) / (self.flow_max - self.flow_min + 1e-8)
        if self.training:
            x[:-1] = x[:-1] + np.random.normal(0.0, 0.005, x[:-1].shape)

        cache_path = self.label_cache_paths[idx] if idx < len(self.label_cache_paths) else None
        if cache_path is not None and cache_path.exists():
            y = np.load(cache_path).astype(np.float32, copy=False)
            if y.shape[0] != self.num_cells:
                raise ValueError(f"Bad cache shape in {cache_path}: {y.shape}, expected {self.num_cells}")
        else:
            depth = reader.read_depth(frame_idx)
            y = self.normalizer.transform(depth)
        return torch.from_numpy(x.astype(np.float32)), torch.from_numpy(y)

    def norm_state(self) -> dict:
        return {
            "flow_min": self.flow_min,
            "flow_max": self.flow_max,
            "depth_normalizer": self.normalizer.state_dict(),
            "flow_history": self.flow_history,
            "num_boundaries": self.num_boundaries,
            "input_dim": self.input_dim,
            "num_cells": self.num_cells,
            "cache_fingerprint": self._cache_fingerprint(),
        }


class FloodCNN(nn.Module):
    def __init__(self, input_dim: int, num_cells: int):
        super().__init__()
        c1, c2, c3 = CONFIG["conv_channels"]
        f1, f2 = CONFIG["fc_units"]
        dropout = float(CONFIG["dropout"])
        self.encoder = nn.Sequential(
            nn.Conv1d(1, c1, kernel_size=5, padding=2),
            nn.BatchNorm1d(c1),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(c1, c2, kernel_size=5, padding=2),
            nn.BatchNorm1d(c2),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(c2, c3, kernel_size=3, padding=1),
            nn.BatchNorm1d(c3),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(4),
        )
        encoded = c3 * 4
        self.shared = nn.Sequential(
            nn.Linear(encoded, f1),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.prob_head = nn.Sequential(
            nn.Linear(f1, f2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(f2, num_cells),
            nn.Sigmoid(),
        )
        self.depth_head = nn.Sequential(
            nn.Linear(f1, f2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(f2, num_cells),
            nn.Sigmoid(),
        )
        self.scale = nn.Parameter(torch.ones(num_cells) * 1.2)
        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Conv1d):
                nn.init.kaiming_normal_(module.weight, nonlinearity="relu")
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x.unsqueeze(1)).flatten(1)
        z = self.shared(z)
        prob = self.prob_head(z)
        depth = self.depth_head(z)
        return torch.clamp(prob * depth * torch.clamp(self.scale, 0.5, 1.5), 0.0, 1.5)


class FloodLoss(nn.Module):
    def __init__(self, threshold_norm: float):
        super().__init__()
        self.threshold = threshold_norm

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        mse = F.mse_loss(pred, target)
        logits = (pred - self.threshold) * 20.0
        target_bin = (target > self.threshold).float()
        pos_weight = torch.tensor(float(CONFIG["miss_penalty"]), device=pred.device)
        bce = F.binary_cross_entropy_with_logits(logits, target_bin, pos_weight=pos_weight)
        flooded = target > self.threshold
        flooded_mse = F.mse_loss(pred[flooded], target[flooded]) if flooded.any() else pred.new_tensor(0.0)
        dry = target <= self.threshold
        if dry.any():
            dry_excess = torch.relu(pred[dry] - self.threshold)
            dry_loss = torch.mean(dry_excess ** 2) * float(CONFIG["dry_penalty"])
        else:
            dry_loss = pred.new_tensor(0.0)
        return (
            CONFIG["loss_mse"] * mse
            + CONFIG["loss_bce"] * bce
            + CONFIG["loss_flooded"] * flooded_mse
            + CONFIG["loss_dry"] * dry_loss
        )


class FloodPredictor:
    def __init__(self, input_dim: int, num_cells: int, normalizer: DepthNormalizer | None = None):
        self.device = get_device()
        self.input_dim = input_dim
        self.num_cells = num_cells
        self.model = FloodCNN(input_dim, num_cells).to(self.device)
        self.normalizer = normalizer or DepthNormalizer()
        self.norm_state: dict | None = None
        params = sum(p.numel() for p in self.model.parameters())
        print(f"[model] parameters={params:,}")

    def train_model(self, train_ds: DatFloodDataset, val_ds: DatFloodDataset | None = None) -> None:
        self.normalizer = train_ds.normalizer
        self.norm_state = train_ds.norm_state()
        threshold_norm = float(self.normalizer.transform(np.asarray([CONFIG["depth_threshold"]], dtype=np.float32))[0])
        criterion = FloodLoss(threshold_norm)
        optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=float(CONFIG["lr"]),
            weight_decay=float(CONFIG["weight_decay"]),
        )
        scheduler = None
        if CONFIG.get("lr_scheduler", True):
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
                optimizer,
                mode="min",
                factor=float(CONFIG["lr_reduce_factor"]),
                patience=int(CONFIG["lr_reduce_patience"]),
                min_lr=float(CONFIG["min_lr"]),
            )
        train_loader = DataLoader(
            train_ds,
            batch_size=int(CONFIG["batch_size"]),
            shuffle=True,
            num_workers=0,
            pin_memory=torch.cuda.is_available(),
        )
        val_loader = None
        if val_ds is not None:
            val_loader = DataLoader(
                val_ds,
                batch_size=int(CONFIG["batch_size"]),
                shuffle=False,
                num_workers=0,
                pin_memory=torch.cuda.is_available(),
            )

        best_val = float("inf")
        best_epoch = 0
        no_improve = 0
        min_delta = float(CONFIG.get("early_stop_min_delta", 0.0))
        epochs = int(CONFIG["epochs"])
        for epoch in range(1, epochs + 1):
            self.model.train()
            total = 0.0
            count = 0
            for x, y in train_loader:
                x = x.to(self.device, non_blocking=True)
                y = y.to(self.device, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                pred = self.model(x)
                loss = criterion(pred, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                total += float(loss.item()) * x.size(0)
                count += x.size(0)
            train_loss = total / max(count, 1)

            val_loss = None
            if val_loader is not None:
                val_loss = self.evaluate_loss(val_loader, criterion)
                if scheduler is not None:
                    scheduler.step(val_loss)
                if val_loss < best_val - min_delta:
                    best_val = val_loss
                    best_epoch = epoch
                    no_improve = 0
                    self.save(CONFIG["model_path"])
                else:
                    no_improve += 1
            elif epoch == epochs:
                self.save(CONFIG["model_path"])

            if epoch == 1 or epoch % 10 == 0 or epoch == epochs:
                lr_now = optimizer.param_groups[0]["lr"]
                if val_loss is None:
                    print(f"[epoch {epoch:04d}/{epochs}] train_loss={train_loss:.6f}, lr={lr_now:.2e}")
                else:
                    print(
                        f"[epoch {epoch:04d}/{epochs}] train_loss={train_loss:.6f}, "
                        f"val_loss={val_loss:.6f}, best={best_val:.6f}@{best_epoch}, "
                        f"wait={no_improve}, lr={lr_now:.2e}"
                    )

            if (
                val_loader is not None
                and CONFIG.get("early_stopping", True)
                and no_improve >= int(CONFIG["early_stop_patience"])
            ):
                print(
                    f"[early-stop] no val improvement for {no_improve} epochs. "
                    f"best_val={best_val:.6f} at epoch {best_epoch}."
                )
                break

        if val_loader is not None and Path(CONFIG["model_path"]).exists():
            self.load(CONFIG["model_path"])
            print(f"[train] restored best checkpoint: epoch={best_epoch}, val_loss={best_val:.6f}")

    @torch.no_grad()
    def evaluate_loss(self, loader: DataLoader, criterion: nn.Module) -> float:
        self.model.eval()
        total = 0.0
        count = 0
        for x, y in loader:
            x = x.to(self.device, non_blocking=True)
            y = y.to(self.device, non_blocking=True)
            loss = criterion(self.model(x), y)
            total += float(loss.item()) * x.size(0)
            count += x.size(0)
        return total / max(count, 1)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "model": self.model.state_dict(),
                "config": CONFIG,
                "norm_state": self.norm_state,
                "normalizer": self.normalizer.state_dict(),
                "input_dim": self.input_dim,
                "num_cells": self.num_cells,
            },
            path,
        )
        with path.with_suffix(".json").open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "config": CONFIG,
                    "norm_state": self.norm_state,
                    "input_dim": self.input_dim,
                    "num_cells": self.num_cells,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"[model] saved: {path}")

    def load(self, path: str | Path) -> None:
        path = Path(path)
        ckpt = torch.load(path, map_location=self.device)
        self.model.load_state_dict(ckpt["model"])
        self.input_dim = int(ckpt["input_dim"])
        self.num_cells = int(ckpt["num_cells"])
        self.norm_state = ckpt.get("norm_state")
        normalizer_state = ckpt.get("normalizer") or self.norm_state.get("depth_normalizer")
        self.normalizer.load_state_dict(normalizer_state)
        print(f"[model] loaded: {path}")

    @torch.no_grad()
    def predict_depth(self, x: np.ndarray) -> np.ndarray:
        self.model.eval()
        xt = torch.from_numpy(x.astype(np.float32)).unsqueeze(0).to(self.device)
        pred_norm = self.model(xt).squeeze(0).detach().cpu().numpy()
        depth = self.normalizer.inverse(pred_norm)
        depth[depth < CONFIG["depth_threshold"]] = 0.0
        return depth


def find_case_dirs(base: str | Path, require_dat: bool) -> list[Path]:
    base = Path(base)
    if not base.exists():
        return []
    dirs = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        has_csv = bool(list(path.glob("*.csv")))
        has_dat = bool(list(path.glob("*.dat")))
        if has_csv and (has_dat or not require_dat):
            dirs.append(path)
    return dirs


def load_scenarios(base: str | Path, require_dat: bool) -> list[Scenario]:
    dirs = find_case_dirs(base, require_dat=require_dat)
    scenarios = []
    print(f"[cases] {base}: found {len(dirs)} case dirs")
    for i, path in enumerate(dirs, 1):
        print(f"[cases] {i}/{len(dirs)} {path.name}")
        scenarios.append(Scenario(path, require_dat=require_dat).parse())
    return scenarios


def split_train_val(scenarios: list[Scenario]) -> tuple[list[Scenario], list[Scenario] | None]:
    if len(scenarios) < 2:
        return scenarios, None
    mid_idx = len(scenarios) // 2
    val_idx = {mid_idx}
    train = [s for i, s in enumerate(scenarios) if i not in val_idx]
    val = [s for i, s in enumerate(scenarios) if i in val_idx]
    print(f"[split] train={len(train)}, val={len(val)}, val_case={val[0].name}")
    return train, val


def make_predict_feature(scenario: Scenario, hour: float, norm_state: dict) -> np.ndarray:
    x = scenario.feature_vector(
        hour,
        int(norm_state["flow_history"]),
        int(norm_state["num_boundaries"]),
    )
    x[:-1] = (x[:-1] - norm_state["flow_min"]) / (norm_state["flow_max"] - norm_state["flow_min"] + 1e-8)
    return x.astype(np.float32)


def export_max_depth_csv(path: Path, depth: np.ndarray, grid: GridParser | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cell_id", "max_depth"])
        for i, value in enumerate(depth):
            cell_id = grid.cell_ids[i] if grid is not None else i + 1
            writer.writerow([cell_id, f"{float(value):.4f}"])
    print(f"[export] max depth csv: {path}")


def export_time_series_csv(path: Path, hours: list[float], depths: list[np.ndarray], grid: GridParser | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        header = ["cell_id"] + [f"h_{h:.4f}" for h in hours]
        writer.writerow(header)
        num_cells = len(depths[0]) if depths else 0
        for i in range(num_cells):
            cell_id = grid.cell_ids[i] if grid is not None else i + 1
            row = [cell_id] + [f"{float(d[i]):.4f}" for d in depths]
            writer.writerow(row)
    print(f"[export] time series csv: {path}")


def export_eval_report(path: Path, pred_max: np.ndarray, target_max: np.ndarray) -> None:
    threshold = CONFIG["depth_threshold"]
    wet_pred = pred_max > threshold
    wet_true = target_max > threshold
    intersection = int(np.logical_and(wet_pred, wet_true).sum())
    union = int(np.logical_or(wet_pred, wet_true).sum())
    mae = float(np.mean(np.abs(pred_max - target_max)))
    rmse = float(np.sqrt(np.mean((pred_max - target_max) ** 2)))
    report = {
        "mae_max_depth": mae,
        "rmse_max_depth": rmse,
        "iou_flooded": intersection / max(union, 1),
        "pred_flooded_cells": int(wet_pred.sum()),
        "target_flooded_cells": int(wet_true.sum()),
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[eval] {path}: MAE={mae:.4f}, RMSE={rmse:.4f}, IOU={report['iou_flooded']:.4f}")


def train() -> FloodPredictor:
    scenarios = load_scenarios(CONFIG["train_dir"], require_dat=True)
    if not scenarios:
        raise RuntimeError("No training scenarios found")

    try:
        train_scenarios, val_scenarios = split_train_val(scenarios)
        train_ds = DatFloodDataset(train_scenarios, training=True)
        val_ds = None
        if val_scenarios:
            val_ds = DatFloodDataset(
                val_scenarios,
                normalizer=train_ds.normalizer,
                flow_min=train_ds.flow_min,
                flow_max=train_ds.flow_max,
                training=False,
            )

        predictor = FloodPredictor(train_ds.input_dim, train_ds.num_cells, train_ds.normalizer)
        predictor.train_model(train_ds, val_ds)
        return predictor
    finally:
        for scenario in scenarios:
            scenario.close()


def predict(predictor: FloodPredictor, grid: GridParser | None = None) -> dict[str, dict]:
    scenarios = load_scenarios(CONFIG["test_dir"], require_dat=False)
    if not scenarios:
        print(f"[predict] no test cases found in {CONFIG['test_dir']}")
        return {}
    if predictor.norm_state is None:
        raise RuntimeError("Predictor has no normalization state")

    out_base = Path(CONFIG["test_output_dir"])
    out_base.mkdir(parents=True, exist_ok=True)
    results = {}
    for scenario in scenarios:
        try:
            print(f"[predict] {scenario.name}")
            out_dir = out_base / scenario.name
            out_dir.mkdir(parents=True, exist_ok=True)

            output_interval = CONFIG.get("output_interval_hours")
            if output_interval is None and scenario.reader is not None and scenario.reader.hours is not None:
                hours = scenario.reader.hours[:: int(CONFIG["predict_frame_stride"])].tolist()
                if hours[-1] != float(scenario.reader.hours[-1]):
                    hours.append(float(scenario.reader.hours[-1]))
            else:
                interval = float(output_interval if output_interval is not None else CONFIG["csv_time_step"])
                hours = make_regular_hours(scenario.duration_hours, interval)

            pred_depths = []
            max_depth = np.zeros(predictor.num_cells, dtype=np.float32)
            for hour in hours:
                x = make_predict_feature(scenario, float(hour), predictor.norm_state)
                depth = predictor.predict_depth(x)
                pred_depths.append(depth)
                max_depth = np.maximum(max_depth, depth)

            np.save(out_dir / f"{scenario.name}_pred_depths.npy", np.stack(pred_depths, axis=0))
            export_max_depth_csv(out_dir / f"{scenario.name}_max_depth.csv", max_depth, grid)
            if CONFIG["export_time_series_csv"]:
                export_time_series_csv(out_dir / f"{scenario.name}_time_series.csv", hours, pred_depths, grid)

            if scenario.reader is not None:
                target_frames = [int(np.argmin(np.abs(scenario.reader.hours - h))) for h in hours]
                target_max = np.zeros(scenario.reader.cell_count, dtype=np.float32)
                for frame_idx in target_frames:
                    target_max = np.maximum(target_max, scenario.reader.read_depth(frame_idx))
                export_eval_report(out_dir / f"{scenario.name}_eval.json", max_depth, target_max)

            results[scenario.name] = {
                "hours": len(hours),
                "max_depth": float(max_depth.max()),
                "flooded_cells": int((max_depth > CONFIG["depth_threshold"]).sum()),
                "output_dir": str(out_dir),
            }
        finally:
            scenario.close()

    (out_base / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def load_predictor_from_checkpoint(path: str | Path) -> FloodPredictor:
    ckpt = torch.load(path, map_location="cpu")
    predictor = FloodPredictor(int(ckpt["input_dim"]), int(ckpt["num_cells"]))
    predictor.load(path)
    return predictor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flood CNN v2 direct-dat training")
    parser.add_argument("--mode", choices=["auto", "train", "predict"], default=None)
    parser.add_argument("--train-dir", default=None)
    parser.add_argument("--test-dir", default=None)
    parser.add_argument("--grid-file", default=None)
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--train-sample-interval-hours", type=float, default=None)
    parser.add_argument("--output-interval-hours", type=float, default=None)
    parser.add_argument("--train-frame-stride", type=int, default=None)
    parser.add_argument("--predict-frame-stride", type=int, default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--cache-dtype", choices=["float16", "float32"], default=None)
    parser.add_argument("--no-cache-labels", action="store_true")
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--no-timeseries-csv", action="store_true")
    parser.add_argument("--force-retrain", action="store_true")
    return parser.parse_args()


def apply_args(args: argparse.Namespace) -> None:
    for arg_name, cfg_name in [
        ("train_dir", "train_dir"),
        ("mode", "mode"),
        ("test_dir", "test_dir"),
        ("grid_file", "grid_file"),
        ("model_path", "model_path"),
        ("output_dir", "output_dir"),
        ("epochs", "epochs"),
        ("batch_size", "batch_size"),
        ("random_seed", "random_seed"),
        ("train_sample_interval_hours", "train_sample_interval_hours"),
        ("output_interval_hours", "output_interval_hours"),
        ("train_frame_stride", "train_frame_stride"),
        ("predict_frame_stride", "predict_frame_stride"),
        ("cache_dir", "cache_dir"),
        ("cache_dtype", "cache_dtype"),
    ]:
        value = getattr(args, arg_name)
        if value is not None:
            CONFIG[cfg_name] = value
    if args.no_cache_labels:
        CONFIG["cache_labels"] = False
    if args.rebuild_cache:
        CONFIG["rebuild_cache"] = True
    if args.no_timeseries_csv:
        CONFIG["export_time_series_csv"] = False
    if args.force_retrain:
        CONFIG["force_retrain"] = True
    if args.output_dir is None:
        CONFIG["output_dir"] = str(Path(CONFIG["model_path"]).parent)
    CONFIG["test_output_dir"] = str(Path(CONFIG["output_dir"]) / "TEST_RESULTS")
    if args.cache_dir is None:
        CONFIG["cache_dir"] = str(Path(CONFIG["output_dir"]) / "CACHE")


def main() -> None:
    args = parse_args()
    load_local_config()
    apply_args(args)
    set_random_seed(int(CONFIG["random_seed"]))
    configure_numeric_precision()
    Path(CONFIG["output_dir"]).mkdir(parents=True, exist_ok=True)
    mode = CONFIG["mode"]

    grid = None
    if Path(CONFIG["grid_file"]).exists():
        grid = GridParser(CONFIG["grid_file"]).parse()

    model_path = Path(CONFIG["model_path"])
    if mode == "train" or CONFIG["force_retrain"] or not model_path.exists():
        predictor = train()
    else:
        predictor = load_predictor_from_checkpoint(model_path)

    if mode in ("auto", "predict"):
        predict(predictor, grid)


if __name__ == "__main__":
    main()
