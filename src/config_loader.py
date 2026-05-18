from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {config_path}")
    return data


def load_hardware(path: str | Path = "configs/hardware.yaml") -> dict[str, Any]:
    return load_yaml(path)["hardware"]


def load_strategy(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)["strategy"]


def load_scenario(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)["scenario"]
