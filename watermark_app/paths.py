from __future__ import annotations

from pathlib import Path


def unique_output_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        next_candidate = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not next_candidate.exists():
            return next_candidate
    raise FileExistsError(f"No available output filename near {candidate}")
