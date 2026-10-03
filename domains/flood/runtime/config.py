"""Runtime configuration lookup shared by domain adapters."""

from __future__ import annotations

import os

from .common import PROJECT_DIR


def runtime_setting(name: str, default: str = "") -> str:
    """Read an environment variable, then the local ``.env`` file.

    The lookup intentionally mirrors the existing behavior while keeping
    external adapters independent from the route-planning module.
    """

    if os.environ.get(name):
        return str(os.environ[name])
    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'") or default
    return default


__all__ = ["runtime_setting"]
