"""Sicheres, inkrementelles Lesen deklarierter Blueprint-Dateilogs."""

from __future__ import annotations

import glob
from pathlib import Path

from services.guardian_service import redact_log_text


def read_declared_log_updates(
    *,
    root: Path,
    sources: list[str],
    redactors: list[str],
    max_tail_bytes: int,
    positions: dict[str, int],
) -> list[str]:
    resolved_root = root.resolve(strict=False)
    remaining = max_tail_bytes
    lines: list[str] = []
    for source in sources:
        if source == "stdout" or remaining <= 0:
            continue
        for raw in glob.glob(str(resolved_root / source), recursive=False)[:8]:
            candidate = Path(raw)
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(resolved_root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            key = str(resolved)
            try:
                size = resolved.stat().st_size
                previous = positions.get(key)
                start = max(0, size - remaining) if previous is None else previous
                if size < start:
                    start = 0
                if size - start > remaining:
                    start = size - remaining
                if size <= start:
                    positions[key] = size
                    continue
                with resolved.open("rb") as stream:
                    stream.seek(start)
                    chunk = stream.read(min(size - start, remaining))
                positions[key] = size
            except OSError:
                continue
            remaining -= len(chunk)
            split = chunk.decode("utf-8", errors="replace").splitlines()
            if start > 0 and split:
                split = split[1:]
            lines.extend(redact_log_text(line, redactors) for line in split if line)
            if remaining <= 0:
                break
    return lines