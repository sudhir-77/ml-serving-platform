"""Local autoscaler policy: one replica per `target_rps` of observed traffic."""

from __future__ import annotations

import math


def desired_replicas(observed_rps: float, target_rps: float, max_replicas: int) -> int:
    if target_rps <= 0:
        raise ValueError("target_rps must be positive")
    if max_replicas < 1:
        raise ValueError("max_replicas must be positive")
    if observed_rps <= 0:
        return 1
    needed = int(math.ceil(observed_rps / target_rps))
    return max(1, min(max_replicas, needed))
