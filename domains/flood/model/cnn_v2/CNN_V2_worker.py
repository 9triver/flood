from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import TextIO

import CNN_V2 as cnn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent Flood CNN v2 inference worker")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], required=True)
    parser.add_argument("--grid-file", required=True)
    parser.add_argument("--grid-cache-file", default=None)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--inference-batch-size", type=int, default=8)
    parser.add_argument("--no-timeseries-csv", action="store_true")
    return parser.parse_args()


def send(protocol: TextIO, payload: dict) -> None:
    protocol.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    protocol.flush()


def initialize(args: argparse.Namespace) -> tuple[cnn.FloodPredictor, cnn.GridParser]:
    cnn.load_local_config()
    cnn.CONFIG["mode"] = "predict"
    cnn.CONFIG["device"] = args.device
    cnn.CONFIG["grid_file"] = args.grid_file
    cnn.CONFIG["grid_cache_file"] = args.grid_cache_file
    cnn.CONFIG["model_path"] = args.model_path
    cnn.CONFIG["inference_batch_size"] = max(1, args.inference_batch_size)
    if args.no_timeseries_csv:
        cnn.CONFIG["export_time_series_csv"] = False
    cnn.set_random_seed(int(cnn.CONFIG["random_seed"]))
    cnn.configure_numeric_precision()
    grid = cnn.GridParser(args.grid_file, args.grid_cache_file).parse()
    predictor = cnn.load_predictor_from_checkpoint(args.model_path)
    return predictor, grid


def serve(protocol: TextIO, predictor: cnn.FloodPredictor,
          grid: cnn.GridParser) -> None:
    send(protocol, {
        "type": "ready",
        "pid": os.getpid(),
        "device": predictor.device.type,
    })
    for line in sys.stdin:
        request_id = ""
        try:
            request = json.loads(line)
            request_id = str(request.get("request_id") or "")
            test_dir = Path(str(request["test_dir"])).resolve()
            output_dir = Path(str(request["output_dir"])).resolve()
            cnn.CONFIG["test_dir"] = str(test_dir)
            cnn.CONFIG["output_dir"] = str(output_dir)
            cnn.CONFIG["test_output_dir"] = str(output_dir / "TEST_RESULTS")
            output_dir.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                result = cnn.predict(predictor, grid)
            send(protocol, {
                "type": "result",
                "request_id": request_id,
                "device": predictor.device.type,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "result": result,
            })
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send(protocol, {
                "type": "error",
                "request_id": request_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })


def main() -> None:
    args = parse_args()
    protocol = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        predictor, grid = initialize(args)
    serve(protocol, predictor, grid)


if __name__ == "__main__":
    main()
