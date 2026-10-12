"""Gateway JSON configs and sampled workloads."""

from __future__ import annotations

import numpy as np

from mlserving.features import vector_for


def make_gateway_config(
    listen: str,
    mode: str,
    feature_url: str,
    ranking_backends: list[dict],
    text_backends: list[dict],
    hedge_after_ms: int,
    fetch_features: bool,
    canary_weight: float = 0.0,
    max_attempts: int | None = None,
) -> dict:
    if mode not in {"gateway", "naive"}:
        raise ValueError("mode must be gateway or naive")
    attempts = max_attempts
    if attempts is None:
        attempts = 1 if mode == "naive" else 3
    return {
        "listen": listen,
        "mode": mode,
        "feature_url": feature_url,
        "fetch_features": fetch_features and mode == "gateway",
        "api_keys": [
            {"secret": "rank-key", "scopes": ["ranking"], "rate_rps": 50000, "burst": 50000},
            {"secret": "text-key", "scopes": ["text"], "rate_rps": 50000, "burst": 50000},
            {"secret": "both-key", "scopes": ["ranking", "text"], "rate_rps": 50000, "burst": 50000},
        ],
        "models": {
            "ranking": {
                "backends": ranking_backends,
                "canary_weight": canary_weight,
                "canary_error_threshold": 0.4,
                "canary_min_samples": 20,
                "canary_window_ms": 2000,
            },
            "text": {
                "backends": text_backends,
                "canary_weight": 0.0,
                "canary_error_threshold": 0.5,
                "canary_min_samples": 20,
                "canary_window_ms": 2000,
            },
        },
        "hedge_after_ms": 0 if mode == "naive" else hedge_after_ms,
        "request_timeout_ms": 2000,
        "max_attempts": attempts,
        "breaker_failures": 3,
        "breaker_cooldown_ms": 800,
        "feature_cache_ms": 5000,
        "retry_base_ms": 1,
        "retry_max_ms": 15,
    }


def make_workload(
    user_ids: np.ndarray,
    item_ids: np.ndarray,
    row_ids: np.ndarray,
    aggregates: dict,
    n: int,
    seed: int,
    texts: list[str] | None = None,
) -> list[dict]:
    if n < 1:
        raise ValueError("n must be positive")
    if len(row_ids) == 0:
        raise ValueError("no rows to sample")
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(row_ids), size=n)
    texts = texts or ["toy story"]
    rows = []
    for request_id, pick in enumerate(picks.tolist()):
        user_id = int(user_ids[pick])
        item_id = int(item_ids[pick])
        rows.append(
            {
                "request_id": int(request_id),
                "row_id": int(row_ids[pick]),
                "user_id": user_id,
                "item_id": item_id,
                "features": vector_for(aggregates, user_id, item_id),
                "text": texts[request_id % len(texts)],
            }
        )
    return rows
