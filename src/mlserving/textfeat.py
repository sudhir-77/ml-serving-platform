"""Stable hashed text features. Python's builtin hash is randomized per process."""

from __future__ import annotations

import re
import zlib

_TOKEN = re.compile(r"[a-z0-9]+")


def hash_text(text: str, dim: int = 32) -> list[float]:
    if dim <= 0:
        raise ValueError("dim must be positive")
    vec = [0.0] * dim
    for tok in _TOKEN.findall(text.lower()):
        vec[zlib.crc32(tok.encode("utf-8")) % dim] += 1.0
    return vec
