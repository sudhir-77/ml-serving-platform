"""Recompute scenario metrics from CSV artifacts. Numbers are not typed by hand."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


def percentile(values: list[float], q: float) -> float:
    """Linear percentile, NumPy's default (Hyndman-Fan type 7), q in [0, 100]."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if q < 0 or q > 100:
        raise ValueError("q must be between 0 and 100")
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    rank = (q / 100.0) * (len(xs) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    frac = rank - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def check_op(value: float, op: str, threshold: float, threshold_high: float | None = None) -> bool:
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == "between":
        if threshold_high is None:
            raise ValueError("between needs threshold_high")
        return threshold <= value <= threshold_high
    raise ValueError(f"unknown op {op}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def success_rate(rows: list[dict[str, str]]) -> float:
    if not rows:
        raise ValueError("no rows")
    ok = sum(1 for row in rows if int(float(row["status"])) == 200)
    return ok / len(rows)


def latency_percentile(rows: list[dict[str, str]], q: float, successes_only: bool = True) -> float:
    vals: list[float] = []
    for row in rows:
        if successes_only and int(float(row["status"])) != 200:
            continue
        vals.append(float(row["latency_ms"]))
    return percentile(vals, q)


def filter_rows(rows: list[dict[str, str]], scenario: str, mode: str) -> list[dict[str, str]]:
    return [row for row in rows if row["scenario"] == scenario and row["mode"] == mode]


def paired_overhead_p95(rows: list[dict[str, str]]) -> float:
    gateway = {
        row["request_id"]: float(row["latency_ms"])
        for row in rows
        if row["mode"] == "gateway" and int(float(row["status"])) == 200
    }
    direct = {
        row["request_id"]: float(row["latency_ms"])
        for row in rows
        if row["mode"] == "direct" and int(float(row["status"])) == 200
    }
    diffs = [gateway[key] - direct[key] for key in gateway if key in direct]
    if not diffs:
        raise ValueError("no paired overhead rows")
    return percentile(diffs, 95)


def relative_drop(baseline: float, candidate: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline latency must be positive")
    return (baseline - candidate) / baseline


def auth_reject_rate(rows: list[dict[str, str]]) -> float:
    bad = [row for row in rows if int(row["expected_reject"]) == 1]
    if not bad:
        raise ValueError("auth matrix has no unauthorized cases")
    rejected = sum(1 for row in bad if int(row["status"]) >= 400)
    return rejected / len(bad)


def parity_max_abs(rows: list[dict[str, str]]) -> float:
    if not rows:
        raise ValueError("empty parity file")
    return max(float(row["abs_diff"]) for row in rows)


def hpa_slo_attainment(rows: list[dict[str, str]], latency_slo_ms: float = 100.0) -> float:
    scoped = [row for row in rows if row["mode"] == "gateway"]
    if not scoped:
        raise ValueError("hpa load test is empty")
    ok = 0
    for row in scoped:
        if int(float(row["status"])) == 200 and float(row["latency_ms"]) <= latency_slo_ms:
            ok += 1
    return ok / len(scoped)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assemble_from_results(results: Path) -> dict[str, float | int | str | bool]:
    """Read the evaluation artifacts and return the flat metric map."""
    podkill = read_csv(results / "loadtest_podkill.csv")
    spike = read_csv(results / "loadtest_spike.csv")
    overhead = read_csv(results / "loadtest_overhead.csv")
    features = read_csv(results / "loadtest_features.csv")
    hpa_rows = read_csv(results / "loadtest_hpa.csv")
    canary_rows = read_csv(results / "loadtest_canary.csv")
    parity_rows = read_csv(results / "onnx_parity.csv")
    auth_rows = read_csv(results / "auth_matrix.csv")
    hpa_meta = _load_json(results / "hpa_meta.json")
    canary_meta = _load_json(results / "canary_meta.json")
    train_meta = _load_json(results / "train_meta.json")
    cover_meta = _load_json(results / "coverage_meta.json")

    gw_kill = filter_rows(podkill, "podkill", "gateway")
    naive_kill = filter_rows(podkill, "podkill", "naive")
    gw_spike = filter_rows(spike, "spike", "gateway")
    naive_spike = filter_rows(spike, "spike", "naive")
    rust_rows = filter_rows(features, "features", "gateway")
    py_rows = filter_rows(features, "features", "naive")
    canary_gw = filter_rows(canary_rows, "canary", "gateway")

    success_gw = success_rate(gw_kill)
    success_naive = success_rate(naive_kill)
    p99_gw = latency_percentile(gw_spike, 99)
    p99_naive = latency_percentile(naive_spike, 99)
    fs_p99 = latency_percentile(rust_rows, 99)
    fs_py = latency_percentile(py_rows, 99)
    cut = relative_drop(p99_naive, p99_gw)
    speedup = fs_py / fs_p99 if fs_p99 > 0 else float("inf")
    onnx_diff = parity_max_abs(parity_rows)
    auth_rate = auth_reject_rate(auth_rows)
    overhead_p95 = paired_overhead_p95(overhead)
    hpa_reaction = float(hpa_meta["hpa_reaction_s"])
    slo = hpa_slo_attainment(hpa_rows)
    rollback_s = float(canary_meta["canary_rollback_s"])
    failed = 1.0 - success_rate(canary_gw)

    return {
        "success_rate_podkill": success_gw,
        "success_rate_podkill_naive": success_naive,
        "success_rate_podkill_pct": round(success_gw * 100.0, 2),
        "success_rate_podkill_naive_pct": round(success_naive * 100.0, 2),
        "p99_spike_ms": p99_gw,
        "p99_spike_naive_ms": p99_naive,
        "p99_cut": cut,
        "p99_cut_pct": round(cut * 100.0, 2),
        "p95_overhead_ms": overhead_p95,
        "fs_p99_ms": fs_p99,
        "fs_python_p99_ms": fs_py,
        "fs_speedup": speedup,
        "fs_speedup_rounded": round(speedup, 2),
        "hpa_reaction_s": hpa_reaction,
        "hpa_slo_attainment": slo,
        "canary_rollback_s": rollback_s,
        "canary_failed_frac": failed,
        "onnx_max_abs_diff": onnx_diff,
        "auth_reject_rate": auth_rate,
        "test_rmse": float(train_meta["test_rmse"]),
        "baseline_rmse": float(train_meta["baseline_rmse"]),
        "go_coverage": float(cover_meta["go_coverage"]),
        "python_coverage": float(cover_meta["python_coverage"]),
        "rust_tests_passed": bool(cover_meta["rust_tests_passed"]),
        "execution_mode": str(train_meta.get("execution_mode", "local_processes")),
        "train_rows": int(train_meta["splits"]["movielens_100k"]["train"]),
        "val_rows": int(train_meta["splits"]["movielens_100k"]["val"]),
        "test_rows": int(train_meta["splits"]["movielens_100k"]["test"]),
    }


def evaluate_criteria(metrics: dict) -> list[dict]:
    rust_ok = 1.0 if metrics["rust_tests_passed"] else 0.0
    specs = [
        (
            "Success rate when one model pod is killed every 30 s under load",
            metrics["success_rate_podkill"],
            ">=",
            0.995,
            None,
        ),
        (
            "p99 latency under a 5x latency spike on one replica is at least 40% lower than the naive proxy",
            metrics["p99_cut"],
            ">=",
            0.40,
            None,
        ),
        ("p95 gateway overhead without faults", metrics["p95_overhead_ms"], "<", 5.0, None),
        (
            "Rust feature server p99 lookup latency at 2,000 RPS",
            metrics["fs_p99_ms"],
            "<",
            2.0,
            None,
        ),
        (
            "Rust feature server at least 3x faster than a Python feature-lookup baseline",
            metrics["fs_speedup"],
            ">=",
            3.0,
            None,
        ),
        (
            "HPA scale-out reaction time from load step to added ready replica",
            metrics["hpa_reaction_s"],
            "between",
            0.01,
            30.0,
        ),
        ("Canary rollback within 60 s", metrics["canary_rollback_s"], "<=", 60.0, None),
        ("Canary failed fraction", metrics["canary_failed_frac"], "<", 0.01, None),
        ("ONNX serving parity", metrics["onnx_max_abs_diff"], "<", 1e-5, None),
        (
            "Unauthorized or out-of-scope requests rejected",
            metrics["auth_reject_rate"],
            ">=",
            1.0,
            None,
        ),
        ("Go test coverage", metrics["go_coverage"], ">=", 0.75, None),
        ("Python test coverage", metrics["python_coverage"], ">=", 0.80, None),
        ("Rust tests for every public function", rust_ok, ">=", 1.0, None),
    ]
    out = []
    for name, value, op, threshold, high in specs:
        out.append(
            {
                "name": name,
                "value": value,
                "op": op,
                "threshold": threshold,
                "threshold_high": high,
                "passed": bool(check_op(float(value), op, float(threshold), None if high is None else float(high))),
            }
        )
    return out


def build_report(
    metrics: dict,
    splits: dict,
    seeds: list[int],
    hardware: dict,
    runtime_seconds: float,
) -> dict:
    criteria = evaluate_criteria(metrics)
    baseline = {
        "success_rate_podkill": metrics["success_rate_podkill_naive"],
        "p99_spike_ms": metrics["p99_spike_naive_ms"],
        "fs_p99_ms": metrics["fs_python_p99_ms"],
        "ranking_rmse": metrics["baseline_rmse"],
    }
    flat = dict(metrics)
    return {
        "project": "ml-serving-platform",
        "success_criteria": criteria,
        "metrics": flat,
        "baseline": baseline,
        "splits": splits,
        "seeds": seeds,
        "hardware": hardware,
        "runtime_seconds": runtime_seconds,
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
