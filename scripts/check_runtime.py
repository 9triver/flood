from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
COMMON_FILES = (
    PROJECT_DIR / "agent" / "oag" / "agent.py",
    PROJECT_DIR / "domains" / "flood" / "ontology.yaml",
    PROJECT_DIR / "domains" / "flood" / "data" / "mock" / "boundary_flow.csv",
    PROJECT_DIR / "domains" / "flood" / "data" / "objects" / "manifest.json",
    PROJECT_DIR / "domains" / "flood" / "model" / "cnn_v2" / "GT.txt",
    PROJECT_DIR / "server" / "static" / "index.html",
)
MODEL_WEIGHT = (
    PROJECT_DIR
    / "domains"
    / "flood"
    / "model"
    / "cnn_v2"
    / "weights"
    / "FLOOD_CNN.pth"
)
RUNTIME_DIR = PROJECT_DIR / "local" / "runtime" / "flood"


def load_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_import(name: str, errors: list[str]) -> None:
    try:
        importlib.import_module(name)
    except Exception as exc:
        errors.append(f"Python dependency unavailable: {name} ({exc})")


def is_lfs_pointer(path: Path) -> bool:
    try:
        return path.read_bytes()[:80].startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def run_checks(profile: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    env = load_env(PROJECT_DIR / ".env")

    for path in COMMON_FILES:
        if not path.exists():
            errors.append(f"Required repository file is missing: {path.relative_to(PROJECT_DIR)}")

    for name in ("openai", "yaml", "pandas", "numpy"):
        check_import(name, errors)

    if profile == "full":
        check_import("torch", errors)
        if not MODEL_WEIGHT.exists():
            errors.append(f"CNN weight is missing: {MODEL_WEIGHT.relative_to(PROJECT_DIR)}")
        elif is_lfs_pointer(MODEL_WEIGHT):
            errors.append("CNN weight is still a Git LFS pointer; run `git lfs pull`.")

        required_env = ("LLM_API_KEY", "LLM_API_URL", "LLM_MODEL", "AMAP_WEB_SERVICE_KEY")
        for name in required_env:
            if not env.get(name):
                errors.append(f"Required .env setting is missing: {name}")
    else:
        if not all(env.get(name) for name in ("LLM_API_KEY", "LLM_API_URL", "LLM_MODEL")):
            warnings.append("LLM settings are incomplete; agent chat will be disabled.")
        if not env.get("AMAP_WEB_SERVICE_KEY"):
            warnings.append("AMAP_WEB_SERVICE_KEY is missing; route planning will be disabled.")

    try:
        (RUNTIME_DIR / "workspaces").mkdir(parents=True, exist_ok=True)
        (RUNTIME_DIR / "cache").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"Runtime directory is not writable: {RUNTIME_DIR} ({exc})")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the flood runtime installation.")
    parser.add_argument("--profile", choices=("server", "full"), default="full")
    args = parser.parse_args()

    errors, warnings = run_checks(args.profile)
    print(f"Flood runtime preflight ({args.profile})")
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK: 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
