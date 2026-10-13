"""Kubernetes-style event log written next to the load-test CSVs."""

from __future__ import annotations

import time
from pathlib import Path


class EventLog:
    """CSV with columns ts, object, event, source.

    `source` is `kubectl` for rows copied from the API server and `harness`
    for actions this process issued.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8", newline="")
        self.handle.write("ts,object,event,source\n")

    def add(self, obj: str, event: str, source: str = "harness", ts: float | None = None) -> None:
        obj = obj.replace(",", "-")
        event = event.replace(",", "-")
        source = source.replace(",", "-")
        stamp = time.time() if ts is None else float(ts)
        self.handle.write(f"{stamp:.3f},{obj},{event},{source}\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def reaction_seconds(rows: list[dict[str, str]]) -> float:
    """Seconds from the load-step event to the API server's ready observation."""
    starts = [float(row["ts"]) for row in rows if row["event"] == "load_step"]
    readies = [
        float(row["ts"])
        for row in rows
        if row["event"] == "scale_out_ready" and row.get("source") == "kubectl"
    ]
    if not starts or not readies:
        raise ValueError("hpa events are missing a load_step or kubectl scale_out_ready")
    start = min(starts)
    after = [stamp for stamp in readies if stamp >= start]
    if not after:
        raise ValueError("scale_out_ready is earlier than the load step")
    return min(after) - start
