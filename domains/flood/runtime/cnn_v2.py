from __future__ import annotations

import atexit
import csv
import hashlib
import json
import os
import queue
import re
import subprocess
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from .common import DOMAIN_DIR, rel
from .workspace import SHARED_CACHE_DIR, workspace_dir


MODEL_DIR = DOMAIN_DIR / "model" / "cnn_v2"
MODEL_SCRIPT = MODEL_DIR / "CNN_V2.py"
WORKER_SCRIPT = MODEL_DIR / "CNN_V2_worker.py"
GRID_PATH = MODEL_DIR / "GT.txt"
WEIGHT_PATH = MODEL_DIR / "weights" / "FLOOD_CNN.pth"
GRID_CACHE_PATH = SHARED_CACHE_DIR / "cnn_v2" / "grid.npz"

BOUNDARY_FILES = (
    ("interval1", "00_interval1.csv"),
    ("interval2", "01_interval2.csv"),
    ("tonggu", "02_tonggu.csv"),
    ("upstream", "03_upstream.csv"),
)
CNN_DEVICE_CHOICES = {"cpu", "cuda", "auto"}


class CnnWorkerError(RuntimeError):
    pass


class CnnWorkerTimeoutError(CnnWorkerError):
    pass


class _CnnWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=200)
        self._stderr_lock = threading.Lock()
        self._configuration: tuple[Any, ...] | None = None
        self._device = ""
        self._reader_threads: list[threading.Thread] = []

    def run(self, *, test_dir: Path, output_dir: Path,
            requested_device: str, timeout: int,
            env: dict[str, str]) -> dict[str, Any]:
        started = time.perf_counter()
        configuration = self._worker_configuration(requested_device)
        if not self._lock.acquire(timeout=max(0.1, float(timeout))):
            raise CnnWorkerTimeoutError("timed out waiting for the CNN worker")
        queue_ms = (time.perf_counter() - started) * 1000
        try:
            reused = self._is_running(configuration)
            startup_ms = 0.0
            if not reused:
                startup_started = time.perf_counter()
                self._stop_locked()
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    raise CnnWorkerTimeoutError("timed out waiting for the CNN worker")
                self._start_locked(configuration, requested_device, env, remaining)
                startup_ms = (time.perf_counter() - startup_started) * 1000

            process = self._process
            if process is None or process.stdin is None:
                raise CnnWorkerError("CNN worker is unavailable")
            request_id = uuid.uuid4().hex
            payload = {
                "request_id": request_id,
                "test_dir": str(test_dir),
                "output_dir": str(output_dir),
            }
            request_started = time.perf_counter()
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._stop_locked()
                raise CnnWorkerError(f"CNN worker pipe failed: {exc}") from exc

            remaining = timeout - (time.perf_counter() - started)
            if remaining <= 0:
                raise CnnWorkerTimeoutError("CNN worker prediction timed out")
            response = self._wait_for_message(remaining)
            request_ms = (time.perf_counter() - request_started) * 1000
            if response.get("type") == "eof":
                self._stop_locked()
                raise CnnWorkerError("CNN worker exited unexpectedly")
            if response.get("request_id") != request_id:
                self._stop_locked()
                raise CnnWorkerError("CNN worker returned a mismatched response")
            if response.get("type") == "error":
                raise CnnWorkerError(str(response.get("error") or "CNN worker prediction failed"))
            if response.get("type") != "result":
                self._stop_locked()
                raise CnnWorkerError("CNN worker returned an invalid response")
            return {
                "result": response.get("result") or {},
                "device": str(response.get("device") or self._device or requested_device),
                "worker_reused": reused,
                "worker_pid": process.pid,
                "worker_queue_ms": round(queue_ms, 1),
                "worker_startup_ms": round(startup_ms, 1),
                "worker_request_ms": round(request_ms, 1),
                "worker_elapsed_ms": response.get("elapsed_ms"),
                "stderr": self.stderr_tail(),
            }
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def stderr_tail(self) -> str:
        with self._stderr_lock:
            return "".join(self._stderr)[-4000:]

    def _worker_configuration(self, requested_device: str) -> tuple[Any, ...]:
        return (
            cnn_python(),
            requested_device,
            cnn_inference_batch_size(),
            _file_identity(WORKER_SCRIPT),
            _file_identity(MODEL_SCRIPT),
            _file_identity(GRID_PATH),
            _file_identity(WEIGHT_PATH),
        )

    def _is_running(self, configuration: tuple[Any, ...]) -> bool:
        return bool(
            self._process is not None
            and self._process.poll() is None
            and self._configuration == configuration
        )

    def _start_locked(self, configuration: tuple[Any, ...],
                      requested_device: str, env: dict[str, str],
                      timeout: float) -> None:
        responses: queue.Queue[dict[str, Any]] = queue.Queue()
        stderr: deque[str] = deque(maxlen=200)
        self._responses = responses
        with self._stderr_lock:
            self._stderr = stderr
        command = [
            cnn_python(),
            str(WORKER_SCRIPT),
            "--device", requested_device,
            "--grid-file", str(GRID_PATH),
            "--grid-cache-file", str(GRID_CACHE_PATH),
            "--model-path", str(WEIGHT_PATH),
            "--inference-batch-size", str(cnn_inference_batch_size()),
            "--no-timeseries-csv",
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(MODEL_DIR),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise CnnWorkerError(f"could not start CNN worker: {exc}") from exc
        self._process = process
        self._configuration = configuration
        response_thread = threading.Thread(
            target=self._read_responses, args=(process, responses), daemon=True,
            name="cnn-worker-responses",
        )
        stderr_thread = threading.Thread(
            target=self._read_stderr, args=(process, stderr), daemon=True,
            name="cnn-worker-stderr",
        )
        self._reader_threads = [response_thread, stderr_thread]
        response_thread.start()
        stderr_thread.start()
        ready = self._wait_for_message(float(timeout))
        if ready.get("type") != "ready":
            detail = ready.get("error") or self.stderr_tail() or "no ready response"
            self._stop_locked()
            raise CnnWorkerError(f"CNN worker failed to start: {detail}")
        self._device = str(ready.get("device") or requested_device)

    def _wait_for_message(self, timeout: float) -> dict[str, Any]:
        try:
            return self._responses.get(timeout=timeout)
        except queue.Empty as exc:
            self._stop_locked()
            raise CnnWorkerTimeoutError("CNN worker prediction timed out") from exc

    def _read_responses(self, process: subprocess.Popen[str],
                        responses: queue.Queue[dict[str, Any]]) -> None:
        stream = process.stdout
        if stream is None:
            responses.put({"type": "eof"})
            return
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    message = {"type": "protocol_error", "error": line.strip()}
                responses.put(message)
        finally:
            responses.put({"type": "eof"})

    def _read_stderr(self, process: subprocess.Popen[str],
                     stderr: deque[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        for line in stream:
            with self._stderr_lock:
                stderr.append(line)

    def _stop_locked(self) -> None:
        process = self._process
        reader_threads = self._reader_threads
        self._process = None
        self._reader_threads = []
        self._configuration = None
        self._device = ""
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for thread in reader_threads:
            thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


_CNN_WORKER = _CnnWorker()
atexit.register(_CNN_WORKER.close)


def run_cnn_v2_forecast(
    boundary_flow: dict[str, Any],
    target_depth_path: Path,
    *,
    working_dir: Path | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    if not MODEL_SCRIPT.exists():
        return {"error": f"missing CNN_V2.py: {rel(MODEL_SCRIPT)}"}
    if cnn_worker_enabled() and not WORKER_SCRIPT.exists():
        return {"error": f"missing CNN_V2_worker.py: {rel(WORKER_SCRIPT)}"}
    if not GRID_PATH.exists():
        return {"error": f"missing CNN grid file: {rel(GRID_PATH)}"}
    if not WEIGHT_PATH.exists():
        return {"error": f"missing CNN weight file: {rel(WEIGHT_PATH)}"}

    summary = (boundary_flow or {}).get("summary") or {}
    if not summary:
        return {"error": "missing boundary flow summary"}

    case_name = _model_case_name(summary.get("boundary_flow_id"))
    run_dir = working_dir or workspace_dir(create=True) / "cnn_v2" / "latest"
    test_dir = run_dir / "TEST"
    case_dir = test_dir / case_name
    output_dir = run_dir / "OUTPUT"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_case_csvs(summary, case_dir)
    requested_device = cnn_device()

    env = dict(os.environ)
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    timeout = int(env.get("FLOOD_CNN_TIMEOUT_SECONDS", "300"))
    try:
        execution = _execute_prediction(
            test_dir=test_dir,
            output_dir=output_dir,
            requested_device=requested_device,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        return {
            "error": f"CNN python not found: {cnn_python()}",
            "detail": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "error": "CNN_V2 prediction timed out",
            "detail": str(exc),
        }
    except CnnWorkerTimeoutError as exc:
        return {
            "error": "CNN_V2 prediction timed out",
            "detail": str(exc),
            "stderr": _CNN_WORKER.stderr_tail(),
            "python": cnn_python(),
        }
    except CnnWorkerError as exc:
        return {
            "error": "CNN_V2 prediction failed",
            "detail": str(exc),
            "stderr": _CNN_WORKER.stderr_tail(),
            "python": cnn_python(),
        }
    if int(execution.get("returncode") or 0) != 0:
        return {
            "error": "CNN_V2 prediction failed",
            "returncode": execution.get("returncode"),
            "stdout": str(execution.get("stdout") or "")[-4000:],
            "stderr": str(execution.get("stderr") or "")[-4000:],
            "python": cnn_python(),
        }

    output_depth_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_max_depth.csv"
    output_series_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_pred_depths.npy"
    output_time_series_csv_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_time_series.csv"
    if not output_depth_path.exists():
        return {
            "error": "CNN_V2 prediction did not produce max_depth.csv",
            "expected_path": rel(output_depth_path),
            "stdout": str(execution.get("stdout") or "")[-4000:],
            "stderr": str(execution.get("stderr") or "")[-4000:],
        }

    finalize_started = time.perf_counter()
    target_depth_path.parent.mkdir(parents=True, exist_ok=True)
    target_series_path = target_depth_path.with_name("depth_series.npy")
    target_time_steps_path = target_depth_path.with_name("time_steps.json")
    time_steps = _read_time_steps(output_time_series_csv_path)
    if not time_steps:
        time_steps = _regular_time_steps(summary)
    _replace_file(output_depth_path, target_depth_path)
    for stale_path in (target_series_path, target_time_steps_path):
        if stale_path.exists():
            stale_path.unlink()
    if output_series_path.exists():
        _replace_file(output_series_path, target_series_path)
        target_time_steps_path.write_text(
            json.dumps({
                "time_steps_h": time_steps,
                "source": "FLOOD_CNN_V2 depth series",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finalize_ms = (time.perf_counter() - finalize_started) * 1000
    scan_started = time.perf_counter()
    positive_depths, stats = read_depth_csv(target_depth_path)
    scan_ms = (time.perf_counter() - scan_started) * 1000
    model_case = (execution.get("model_result") or {}).get(case_name) or {}
    model_timings = model_case.get("timings_ms") or {}
    result = {
        "status": "completed",
        "model_name": "FLOOD_CNN_V2",
        "model_description": "CNN_V2 水动力模型：四边界流量历史序列驱动，输出水动力网格多时刻水深与 max_depth。",
        "case_name": case_name,
        "hydrodynamic_depth_path": rel(target_depth_path),
        "hydrodynamic_series_path": rel(target_series_path) if target_series_path.exists() else "",
        "hydrodynamic_time_steps_path": rel(target_time_steps_path) if target_time_steps_path.exists() else "",
        "time_steps_h": time_steps,
        "time_step_count": len(time_steps),
        "python": cnn_python(),
        "device": str(execution.get("device") or requested_device),
        "persistent_worker": bool(execution.get("persistent_worker")),
        "worker_reused": bool(execution.get("worker_reused")),
        "worker_pid": execution.get("worker_pid"),
        "inference_batch_size": cnn_inference_batch_size(),
        "stdout_tail": str(execution.get("stdout") or "")[-2000:],
        "stderr_tail": str(execution.get("stderr") or "")[-2000:],
        "timings_ms": {
            "worker_queue": execution.get("worker_queue_ms", 0.0),
            "worker_startup": execution.get("worker_startup_ms", 0.0),
            "worker_request": execution.get("worker_request_ms"),
            "worker_compute": execution.get("worker_elapsed_ms"),
            "model": model_timings,
            "finalize_outputs": round(finalize_ms, 1),
            "depth_scan": round(scan_ms, 1),
            "total": round((time.perf_counter() - total_started) * 1000, 1),
        },
        "_positive_depths": positive_depths,
        **stats,
    }
    shutil.rmtree(run_dir, ignore_errors=True)
    return result


def _execute_prediction(*, test_dir: Path, output_dir: Path,
                        requested_device: str, timeout: int,
                        env: dict[str, str]) -> dict[str, Any]:
    if cnn_worker_enabled():
        result = _CNN_WORKER.run(
            test_dir=test_dir,
            output_dir=output_dir,
            requested_device=requested_device,
            timeout=timeout,
            env=env,
        )
        return {
            "returncode": 0,
            "stdout": "",
            "persistent_worker": True,
            "model_result": result.pop("result", {}),
            **result,
        }

    command = [
        cnn_python(),
        str(MODEL_SCRIPT),
        "--mode", "predict",
        "--device", requested_device,
        "--test-dir", str(test_dir),
        "--grid-file", str(GRID_PATH),
        "--grid-cache-file", str(GRID_CACHE_PATH),
        "--model-path", str(WEIGHT_PATH),
        "--output-dir", str(output_dir),
        "--inference-batch-size", str(cnn_inference_batch_size()),
        "--no-timeseries-csv",
    ]
    completed = subprocess.run(
        command,
        cwd=str(MODEL_DIR),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    summary_path = output_dir / "TEST_RESULTS" / "summary.json"
    model_result: dict[str, Any] = {}
    if summary_path.exists():
        try:
            parsed = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                model_result = parsed
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "device": _device_used(completed.stdout, requested_device),
        "persistent_worker": False,
        "worker_reused": False,
        "model_result": model_result,
    }


def cnn_python() -> str:
    configured = os.environ.get("FLOOD_CNN_PYTHON")
    if configured:
        return configured
    return sys.executable


def _model_case_name(value: Any) -> str:
    raw = str(value or "latest")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    safe = safe[:96].rstrip("._-") or "case"
    if safe == raw:
        return safe
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{digest}"


def cnn_device() -> str:
    configured = str(os.environ.get("FLOOD_CNN_DEVICE") or "cpu").strip().lower()
    if configured not in CNN_DEVICE_CHOICES:
        choices = ", ".join(sorted(CNN_DEVICE_CHOICES))
        raise ValueError(f"FLOOD_CNN_DEVICE must be one of: {choices}")
    return configured


def cnn_worker_enabled() -> bool:
    configured = str(
        os.environ.get("FLOOD_CNN_PERSISTENT_WORKER") or "true"
    ).strip().lower()
    return configured not in {"0", "false", "no", "off"}


def cnn_inference_batch_size() -> int:
    configured = str(os.environ.get("FLOOD_CNN_BATCH_SIZE") or "8").strip()
    try:
        size = int(configured)
    except ValueError as exc:
        raise ValueError("FLOOD_CNN_BATCH_SIZE must be a positive integer") from exc
    if size <= 0:
        raise ValueError("FLOOD_CNN_BATCH_SIZE must be a positive integer")
    return size


def _file_identity(path: Path) -> tuple[int, int] | tuple[None, None]:
    try:
        stat = path.stat()
    except OSError:
        return None, None
    return stat.st_mtime_ns, stat.st_size


def _device_used(stdout: str, requested: str) -> str:
    if "[device] CUDA:" in stdout:
        return "cuda"
    if "[device] CPU" in stdout:
        return "cpu"
    return requested


def _replace_file(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    source.replace(target)


def _write_case_csvs(summary: dict[str, Any], case_dir: Path) -> None:
    boundaries = summary.get("boundaries") or {}
    for boundary_key, filename in BOUNDARY_FILES:
        item = boundaries.get(boundary_key) or {}
        rows = item.get("series") or []
        path = case_dir / filename
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["time_h", "flow_m3s"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "time_h": row.get("time_h", 0),
                    "flow_m3s": row.get("flow_m3s", 0),
                })


def _read_time_steps(path: Path) -> list[float]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        header = next(csv.reader(file), [])
    steps = []
    for value in header[1:]:
        if value.startswith("h_"):
            value = value[2:]
        try:
            steps.append(round(float(value), 4))
        except ValueError:
            continue
    return steps


def _regular_time_steps(summary: dict[str, Any]) -> list[float]:
    duration = 0.0
    for boundary in (summary.get("boundaries") or {}).values():
        for row in boundary.get("series") or []:
            duration = max(duration, float(row.get("time_h") or 0))
    interval = _read_output_interval_hours()
    if duration <= 0 or interval <= 0:
        return []
    count = int(duration // interval)
    steps = [round(index * interval, 4) for index in range(1, count + 1)]
    if not steps or abs(steps[-1] - duration) > 1e-6:
        steps.append(round(duration, 4))
    return steps


def _read_output_interval_hours() -> float:
    path = MODEL_DIR / "TIME.txt"
    if not path.exists():
        return 0.5
    with path.open(encoding="utf-8") as file:
        for line in file:
            value_part, _, key_part = line.partition("#")
            if key_part.strip().split()[:1] == ["output_interval_hours"]:
                try:
                    return float(value_part.strip())
                except ValueError:
                    return 0.5
    return 0.5


def read_depth_csv(path: Path) -> tuple[dict[int, float], dict[str, Any]]:
    positive_depths: dict[int, float] = {}
    depth_count = 0
    flooded_count = 0
    max_depth = 0.0
    depth_sum = 0.0
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                cell_id = int(row["cell_id"])
                depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            depth_count += 1
            if depth > 0:
                positive_depths[cell_id] = depth
                flooded_count += 1
                depth_sum += depth
                max_depth = max(max_depth, depth)
    return positive_depths, {
        "depth_count": depth_count,
        "flooded_count": flooded_count,
        "max_depth_m": round(max_depth, 4),
        "mean_depth_m": round(depth_sum / flooded_count, 4) if flooded_count else 0.0,
    }
