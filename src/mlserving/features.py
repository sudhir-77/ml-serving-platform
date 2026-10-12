"""Train-only rating aggregates. Test rows are never passed in."""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np

FEATURE_NAMES = [
    "user_bias",
    "user_log_count",
    "user_mean",
    "item_bias",
    "item_log_count",
    "item_mean",
]


def _stats(ids: np.ndarray, ratings: np.ndarray) -> dict[int, tuple[float, float, float]]:
    """Map id -> (bias, log1p(count), mean). Bias is filled by the caller."""
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for i, rating in zip(ids.tolist(), ratings.tolist()):
        key = int(i)
        sums[key] = sums.get(key, 0.0) + float(rating)
        counts[key] = counts.get(key, 0) + 1
    out: dict[int, tuple[float, float, float]] = {}
    for key, total in sums.items():
        count = counts[key]
        mean = total / count
        out[key] = (mean, float(count), math.log1p(count))
    return out


def fit_aggregates(
    user_ids: np.ndarray,
    item_ids: np.ndarray,
    ratings: np.ndarray,
) -> dict:
    """Fit user and item vectors from training ratings only.

    Each side is ``[bias, log1p(count), mean]``. Bias is mean minus the
    training global mean. Unknown ids use the default vectors.
    """
    if len(ratings) == 0:
        raise ValueError("need at least one training rating")
    global_mean = float(np.mean(ratings))
    users_raw = _stats(user_ids, ratings)
    items_raw = _stats(item_ids, ratings)

    def pack(raw: Mapping[int, tuple[float, float, float]]) -> dict[str, list[float]]:
        packed: dict[str, list[float]] = {}
        for key, (mean, _count, log_count) in raw.items():
            packed[str(key)] = [mean - global_mean, log_count, mean]
        return packed

    user_default = [0.0, 0.0, global_mean]
    item_default = [0.0, 0.0, global_mean]
    return {
        "global_mean": global_mean,
        "feature_names": list(FEATURE_NAMES),
        "users": pack(users_raw),
        "items": pack(items_raw),
        "defaults": {"user": user_default, "item": item_default},
    }


def lookup_side(table: Mapping[str, list[float]], key: int, default: list[float]) -> list[float]:
    return list(table.get(str(int(key)), default))


def compose_vector(user_vec: list[float], item_vec: list[float]) -> list[float]:
    return list(user_vec) + list(item_vec)


def vector_for(model: dict, user_id: int, item_id: int) -> list[float]:
    user_vec = lookup_side(model["users"], user_id, model["defaults"]["user"])
    item_vec = lookup_side(model["items"], item_id, model["defaults"]["item"])
    return compose_vector(user_vec, item_vec)


def design_matrix(model: dict, user_ids: np.ndarray, item_ids: np.ndarray) -> np.ndarray:
    rows = [
        vector_for(model, int(u), int(i))
        for u, i in zip(user_ids.tolist(), item_ids.tolist())
    ]
    return np.asarray(rows, dtype=np.float64)


def feature_events(model: dict, ttl_ms: int = 3_600_000) -> list[dict]:
    events = [{"key": "u:default", "values": model["defaults"]["user"], "ttl_ms": ttl_ms}]
    events.append({"key": "i:default", "values": model["defaults"]["item"], "ttl_ms": ttl_ms})
    for key, values in model["users"].items():
        events.append({"key": f"u:{key}", "values": values, "ttl_ms": ttl_ms})
    for key, values in model["items"].items():
        events.append({"key": f"i:{key}", "values": values, "ttl_ms": ttl_ms})
    return events
