"""Python dict feature store used as the latency baseline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Entry:
    values: list[float]
    expires_at_ms: int


def expiry(now_ms: int, ttl_ms: int) -> int:
    if ttl_ms <= 0:
        return 2**63 - 1
    return now_ms + ttl_ms


class DictFeatureStore:
    """Sharded by key prefix only in spirit: one locked dict is the baseline.

    The HTTP server may call these methods from several threads. A single lock
    keeps the dict correct and is part of why this baseline is slower than Rust.
    """

    def __init__(self) -> None:
        self.data: dict[str, _Entry] = {}

    def upsert(self, key: str, values: list[float], expires_at_ms: int) -> None:
        self.data[key] = _Entry(values=list(values), expires_at_ms=expires_at_ms)

    def apply(self, events: list[dict], now_ms: int) -> int:
        for event in events:
            self.upsert(
                str(event["key"]),
                [float(v) for v in event["values"]],
                expiry(now_ms, int(event.get("ttl_ms", 0))),
            )
        return len(events)

    def get(self, key: str, now_ms: int) -> list[float] | None:
        entry = self.data.get(key)
        if entry is None:
            return None
        if now_ms >= entry.expires_at_ms:
            del self.data[key]
            return None
        return list(entry.values)

    def vector(self, user_id: int, item_id: int, now_ms: int) -> list[float]:
        user = self.get(f"u:{user_id}", now_ms) or self.get("u:default", now_ms) or [0.0, 0.0, 0.0]
        item = self.get(f"i:{item_id}", now_ms) or self.get("i:default", now_ms) or [0.0, 0.0, 0.0]
        return list(user) + list(item)

    def __len__(self) -> int:
        return len(self.data)
