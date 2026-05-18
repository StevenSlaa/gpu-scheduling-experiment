from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResultWriter:
    def __init__(self, result_dir: str | Path) -> None:
        self.result_dir = Path(result_dir)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.result_dir / "events.jsonl"

    def write_json(self, name: str, payload: dict[str, Any]) -> None:
        with (self.result_dir / name).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def append_event(self, event: str, **payload: Any) -> None:
        record = {"time": utc_now_iso(), "event": event, **payload}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def write_csv(self, name: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
        with (self.result_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def snapshot_configs(self, paths: list[str | Path]) -> None:
        snapshot_dir = self.result_dir / "config_snapshot"
        snapshot_dir.mkdir(exist_ok=True)
        for path in paths:
            source = Path(path)
            if source.exists():
                shutil.copy2(source, snapshot_dir / source.name)


def to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value)
