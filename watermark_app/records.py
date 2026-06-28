from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RecordStore:
    def __init__(self, path: str | Path = "watermark_records.json") -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"records": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, record: dict[str, Any]) -> None:
        data = self._read()
        data.setdefault("records", []).append(record)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def find(self, watermark_id: str) -> dict[str, Any] | None:
        for record in self._read().get("records", []):
            if record.get("watermark_id") == watermark_id:
                return record
        return None
