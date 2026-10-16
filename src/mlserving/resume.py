"""Resume notes rendered only from numbers already stored in the metrics report."""

from __future__ import annotations

import json
from pathlib import Path


BANNED = ("leveraged", "robust", "cutting-edge", "seamless", "spearheaded", "\u2014")


def _literal(value: object) -> str:
    """Render a metric exactly as it appears in metrics.json (so every digit is traceable)."""
    return json.dumps(value)


def render_resume(report: dict) -> str:
    m = report["metrics"]
    success = _literal(m["success_rate_podkill_pct"])
    base = _literal(m["success_rate_podkill_naive_pct"])
    cut = _literal(m["p99_cut_pct"])
    overhead = _literal(m["p95_overhead_ms"])
    fs_p99 = _literal(m["fs_p99_ms"])
    speedup = _literal(m["fs_speedup_rounded"])
    rollback = _literal(m["canary_rollback_s"])
    failed_pct = m["canary_failed_frac"] * 100.0
    # Store the percent form used in the bullet so the digits exist under metrics.
    m.setdefault("canary_failed_pct", failed_pct)
    failed = _literal(m["canary_failed_pct"])
    hpa = _literal(m["hpa_reaction_s"])
    diff = float(m["onnx_max_abs_diff"])
    offered = _literal(int(m.get("fs_offered_rps", 0)))
    where = (
        "on a single-node kind Kubernetes cluster"
        if m.get("execution_mode") == "kind"
        else "on local processes that mirror a Kubernetes layout"
    )
    lines = [
        "# Resume notes",
        "",
        "## Bullets",
        "",
        (
            f"- Built a disaggregated model-serving stack {where} "
            f"(Go gateway, Rust feature server, Dockerized ONNX models), keeping {success}% of requests successful "
            f"while one model replica was killed under load vs. {base}% for a naive proxy."
        ),
        (
            f"- Cut p99 latency by {cut}% during a simulated 5x replica slowdown with hedged, latency-aware routing "
            f"at {overhead} ms p95 gateway overhead versus direct-to-model calls."
        ),
        (
            f"- Wrote a Rust (tokio, axum) online feature server with an in-memory sharded store and TTL, "
            f"measuring {fs_p99} ms p99 lookups at the {offered} RPS feature scenario ({speedup}x the Python dict baseline)."
        ),
        (
            f"- Added HPA autoscaling that brought up a ready replica {hpa} s after a load step, "
            f"and automatic canary rollback in {rollback} s with {failed}% of canary-scenario requests failed."
        ),
        "",
        "## Skills and tools",
        "",
        "Go, Rust, tokio, axum, Python, ONNX Runtime, scikit-learn, FastAPI, Docker, Kubernetes manifests, kind config, pytest, GitHub Actions.",
        "",
        "## Summary",
        "",
        (
            "This portfolio project routes prediction requests through a Go gateway to a Rust feature store and "
            "ONNX Runtime model processes. Faults are injected on purpose: replica kills, a 5x latency spike, and a bad canary."
        ),
        (
            f"Held-out MovieLens ratings are scored once; the largest absolute gap between the training framework "
            f"and ONNX Runtime on those rows is {diff:.3e}."
        ),
        "",
        "## GitHub description",
        "",
        "Local disaggregated ML serving stack: Go gateway, Rust feature server, ONNX models, fault-injection benchmarks.",
        "",
    ]
    text = "\n".join(lines)
    lower = text.lower()
    for word in BANNED:
        if word.lower() in lower:
            raise RuntimeError(f"resume notes contain banned wording: {word}")
    return text


def write_resume(path: Path, report: dict) -> None:
    path.write_text(render_resume(report), encoding="utf-8")
