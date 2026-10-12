"""Unit tests for split integrity, features, ONNX parity, and metric math."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mlserving.autoscale import desired_replicas
from mlserving.configgen import make_gateway_config, make_workload
from mlserving.data import (
    EXPECTED_MD5,
    assert_split_integrity,
    count_ratings,
    ensure_dataset,
    load_ratings,
    md5_file,
    time_split,
)
from mlserving.dict_store import DictFeatureStore, expiry
from mlserving.features import feature_events, fit_aggregates, vector_for
from mlserving.hardware import PeakTracker, hardware_snapshot
from mlserving.metrics import (
    assemble_from_results,
    auth_reject_rate,
    build_report,
    check_op,
    evaluate_criteria,
    paired_overhead_p95,
    percentile,
    relative_drop,
    success_rate,
)
from mlserving.onnx_export import max_abs_diff, mlp_forward
from mlserving.plots import plot_success_bars
from mlserving.resume import BANNED, render_resume
from mlserving.textfeat import hash_text
from mlserving.train import train_ranking_arrays, train_text_model, write_parity_csv


def test_percentile_matches_hand_calculation():
    # rank = 0.95 * (5 - 1) = 3.8 -> 4 * 0.2 + 5 * 0.8 = 4.8
    assert percentile([5, 1, 4, 2, 3], 95) == pytest.approx(4.8)
    assert percentile([7], 99) == 7
    assert percentile([1, 3], 0) == 1
    assert percentile([1, 3], 100) == 3


def test_percentile_rejects_empty_and_bad_q():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_check_op_and_between():
    assert check_op(1.0, ">=", 1.0)
    assert check_op(0.5, "<", 1.0)
    assert not check_op(0.2, ">", 0.2)
    assert check_op(5, "between", 0.01, 30)
    assert not check_op(0.001, "between", 0.01, 30)
    with pytest.raises(ValueError):
        check_op(1, "nope", 1)


def test_time_split_is_ordered_and_disjoint():
    timestamps = np.arange(100, dtype=np.int64)
    # Equal timestamps at the cut must not create overlapping indices.
    timestamps[69] = timestamps[70] = 69
    users = np.arange(100)
    items = np.arange(100, 200)
    split = time_split(timestamps)
    assert_split_integrity(timestamps, users, items, split)
    assert split["train"].shape[0] == 70
    assert split["val"].shape[0] == 10
    assert split["test"].shape[0] == 20
    with pytest.raises(ValueError):
        time_split(timestamps, train_frac=0.9, val_frac=0.2)


def test_bad_split_is_rejected():
    timestamps = np.arange(10)
    users = np.arange(10)
    items = np.arange(10)
    split = {"train": np.array([0, 1]), "val": np.array([1, 2]), "test": np.array([3])}
    with pytest.raises(RuntimeError):
        assert_split_integrity(timestamps, users, items, split)


def test_aggregates_match_a_hand_example_and_ignore_held_out_rows():
    users = np.array([1, 1, 2])
    items = np.array([10, 11, 10])
    ratings = np.array([4.0, 2.0, 5.0])
    model = fit_aggregates(users, items, ratings)
    assert model["global_mean"] == pytest.approx(11.0 / 3.0)
    # user 1 mean is 3, count 2, log1p(2) = log(3)
    user = model["users"]["1"]
    assert user[2] == pytest.approx(3.0)
    assert user[1] == pytest.approx(np.log1p(2))
    assert user[0] == pytest.approx(3.0 - model["global_mean"])
    # Held-out user 9 is not in the map and falls back to the default.
    defaulted = vector_for(model, 9, 10)
    assert defaulted[:3] == pytest.approx(model["defaults"]["user"])
    assert defaulted[3:] == pytest.approx(model["items"]["10"])
    events = feature_events(model, ttl_ms=10)
    keys = {event["key"] for event in events}
    assert "u:1" in keys and "u:default" in keys and "i:default" in keys


def test_dict_store_ttl_and_vector():
    store = DictFeatureStore()
    assert expiry(100, 0) > 10**12
    store.apply(
        [
            {"key": "u:default", "values": [0, 0, 1], "ttl_ms": 0},
            {"key": "i:default", "values": [0, 0, 1], "ttl_ms": 0},
            {"key": "u:5", "values": [1, 2, 3], "ttl_ms": 10},
        ],
        now_ms=1_000,
    )
    assert store.vector(5, 7, 1_005)[:3] == pytest.approx([1, 2, 3])
    assert store.get("u:5", 1_010) is None
    assert store.vector(5, 7, 1_010)[:3] == pytest.approx([0, 0, 1])


def test_hash_text_is_stable_and_counts_repeats():
    assert hash_text("Toy Story", 32) == hash_text("toy story", 32)
    assert sum(hash_text("", 8)) == 0
    doubled = hash_text("toy toy", 32)
    assert max(doubled) == 2
    with pytest.raises(ValueError):
        hash_text("toy", 0)


def test_mlp_onnx_parity_on_a_known_split():
    rng = np.random.default_rng(0)
    n = 360
    users = np.arange(n) // 18 + 1
    items = np.arange(n) % 18 + 1
    ratings = 1 + (np.arange(n) % 5)
    timestamps = np.arange(n)
    # Add a little noise that still depends only on the row index.
    ratings = ratings + rng.normal(0, 0.05, size=n)
    out = Path("data") / "test-models"
    result = train_ranking_arrays(users, items, ratings, timestamps, out, seed=0)
    assert result["onnx_max_abs_diff"] < 1e-5
    x = np.array([[0.2, 0.1, 3.0, -0.1, 0.4, 3.5]])
    coefs = [np.asarray(c, dtype=np.float64) for c in []]
    del coefs
    # Numpy forward on the fitted weights is checked inside train_ranking_arrays.
    assert result["split_sizes"]["train"] + result["split_sizes"]["val"] + result["split_sizes"]["test"] == n
    text = train_text_model(["Toy Story", "Heat", "A drama"], np.array([0, 0, 1], dtype=np.float64), out)
    assert text["max_abs_diff"] < 1e-5
    gap = write_parity_csv(
        out / "parity.csv",
        result["test_row_ids"],
        result["y_test"],
        result["y_framework"],
        result["y_onnx"],
    )
    assert gap == pytest.approx(result["onnx_max_abs_diff"])


def test_mlp_forward_single_relu_layer():
    coefs = [np.array([[1.0, -1.0], [0.5, 0.0]]), np.array([[1.0], [1.0]])]
    intercepts = [np.array([0.0, 0.0]), np.array([0.25])]
    pred = mlp_forward(coefs, intercepts, np.array([[1.0, 2.0]]))
    # hidden = relu([1+1, -1+0]) = [2, 0]; y = 2 + 0 + 0.25
    assert pred[0] == pytest.approx(2.25)
    with pytest.raises(ValueError):
        max_abs_diff(np.array([1.0]), np.array([1.0, 2.0]))


def test_metric_helpers_against_hand_values():
    rows = [
        {"status": "200"},
        {"status": "200"},
        {"status": "500"},
        {"status": "200"},
    ]
    assert success_rate(rows) == pytest.approx(0.75)
    overhead = [
        {"request_id": "1", "mode": "gateway", "status": "200", "latency_ms": "10"},
        {"request_id": "2", "mode": "gateway", "status": "200", "latency_ms": "12"},
        {"request_id": "3", "mode": "gateway", "status": "200", "latency_ms": "9"},
        {"request_id": "1", "mode": "direct", "status": "200", "latency_ms": "8"},
        {"request_id": "2", "mode": "direct", "status": "200", "latency_ms": "7"},
        {"request_id": "3", "mode": "direct", "status": "200", "latency_ms": "8"},
    ]
    # diffs 2, 5, 1. p95 rank = 0.95*2 = 1.9 -> 2*0.1 + 5*0.9 = 4.7
    assert paired_overhead_p95(overhead) == pytest.approx(4.7)
    assert relative_drop(10, 4) == pytest.approx(0.6)
    auth = [
        {"status": "401", "expected_reject": "1"},
        {"status": "403", "expected_reject": "1"},
        {"status": "200", "expected_reject": "0"},
    ]
    assert auth_reject_rate(auth) == pytest.approx(1.0)


def _write_load(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "request_id,scenario,mode,backend_final,attempts,status,latency_ms,ts,row_id"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(header + "\n")
        for row in rows:
            handle.write(",".join(str(row[col]) for col in header.split(",")) + "\n")


def test_assemble_and_criteria_from_fixture_files(tmp_path: Path):
    def row(i, scenario, mode, status, latency):
        return {
            "request_id": i,
            "scenario": scenario,
            "mode": mode,
            "backend_final": "b",
            "attempts": 1,
            "status": status,
            "latency_ms": latency,
            "ts": "0",
            "row_id": i,
        }

    pod = [row(i, "podkill", "gateway", 200, 5) for i in range(200)]
    pod += [row(i, "podkill", "naive", 200 if i % 20 else 500, 8) for i in range(200)]
    spike = [row(i, "spike", "gateway", 200, 4) for i in range(50)]
    spike += [row(i, "spike", "naive", 200, 20) for i in range(50)]
    overhead = []
    for i in range(10):
        overhead.append(row(i, "overhead", "gateway", 200, 6))
        overhead.append(row(i, "overhead", "direct", 200, 4))
    features = [row(i, "features", "gateway", 200, 0.4) for i in range(30)]
    features += [row(i, "features", "naive", 200, 2.0) for i in range(30)]
    hpa = [row(i, "hpa", "gateway", 200, 10) for i in range(10)]
    canary = [row(i, "canary", "gateway", 200, 5) for i in range(100)]
    _write_load(tmp_path / "loadtest_podkill.csv", pod)
    _write_load(tmp_path / "loadtest_spike.csv", spike)
    _write_load(tmp_path / "loadtest_overhead.csv", overhead)
    _write_load(tmp_path / "loadtest_features.csv", features)
    _write_load(tmp_path / "loadtest_hpa.csv", hpa)
    _write_load(tmp_path / "loadtest_canary.csv", canary)
    (tmp_path / "onnx_parity.csv").write_text(
        "row_id,y_true,y_train_framework,y_onnx,abs_diff\n0,1,1,1,1.0e-8\n",
        encoding="utf-8",
    )
    (tmp_path / "auth_matrix.csv").write_text(
        "case,status,expected_reject\nmissing,401,1\nbad,403,1\nok,200,0\n",
        encoding="utf-8",
    )
    (tmp_path / "hpa_meta.json").write_text(json.dumps({"hpa_reaction_s": 1.5}), encoding="utf-8")
    (tmp_path / "canary_meta.json").write_text(json.dumps({"canary_rollback_s": 2.0}), encoding="utf-8")
    (tmp_path / "train_meta.json").write_text(
        json.dumps(
            {
                "test_rmse": 0.9,
                "baseline_rmse": 1.1,
                "execution_mode": "local_processes",
                "splits": {"movielens_100k": {"train": 70, "val": 10, "test": 20}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "coverage_meta.json").write_text(
        json.dumps({"go_coverage": 0.9, "python_coverage": 0.9, "rust_tests_passed": True}),
        encoding="utf-8",
    )
    metrics = assemble_from_results(tmp_path)
    assert metrics["success_rate_podkill"] == pytest.approx(1.0)
    assert metrics["p99_cut"] == pytest.approx(0.8)
    assert metrics["fs_speedup"] == pytest.approx(5.0)
    assert metrics["onnx_max_abs_diff"] == pytest.approx(1e-8)
    report = build_report(
        metrics,
        {"movielens_100k": {"train": 70, "val": 10, "test": 20}},
        [0],
        {"cpu": "test", "ram_gb": 16, "gpu": None, "python": "3.12", "os": "test"},
        1.0,
    )
    assert report["project"] == "ml-serving-platform"
    assert all(item["passed"] for item in report["success_criteria"])
    text = render_resume(report)
    assert "100.0%" in text
    for word in BANNED:
        assert word.lower() not in text.lower()
    plot_success_bars(metrics, tmp_path / "success.png")
    assert (tmp_path / "success.png").stat().st_size > 0


def test_failing_criterion_is_marked():
    metrics = {
        "success_rate_podkill": 0.5,
        "p99_cut": 0.1,
        "p95_overhead_ms": 9,
        "fs_p99_ms": 5,
        "fs_speedup": 1.2,
        "hpa_reaction_s": 0.0,
        "canary_rollback_s": 90,
        "canary_failed_frac": 0.2,
        "onnx_max_abs_diff": 1e-3,
        "auth_reject_rate": 0.5,
        "go_coverage": 0.1,
        "python_coverage": 0.1,
        "rust_tests_passed": False,
    }
    flags = evaluate_criteria(metrics)
    assert not any(item["passed"] for item in flags)


def test_config_and_workload_are_deterministic():
    cfg = make_gateway_config(
        "127.0.0.1:1",
        "gateway",
        "http://127.0.0.1:2",
        [{"name": "a", "url": "http://127.0.0.1:3", "canary": False}],
        [{"name": "t", "url": "http://127.0.0.1:4", "canary": False}],
        hedge_after_ms=3,
        fetch_features=True,
    )
    assert cfg["fetch_features"] is True
    assert cfg["max_attempts"] == 3
    naive = make_gateway_config(
        "127.0.0.1:1",
        "naive",
        "http://127.0.0.1:2",
        [{"name": "a", "url": "http://127.0.0.1:3", "canary": False}],
        [],
        hedge_after_ms=3,
        fetch_features=True,
    )
    assert naive["hedge_after_ms"] == 0
    assert naive["fetch_features"] is False
    with pytest.raises(ValueError):
        make_gateway_config("x", "other", "y", [], [], 1, True)
    aggregates = fit_aggregates(
        np.array([1, 1]),
        np.array([2, 3]),
        np.array([4.0, 2.0]),
    )
    rows = make_workload(np.array([1, 1]), np.array([2, 3]), np.array([0, 1]), aggregates, 5, seed=0)
    again = make_workload(np.array([1, 1]), np.array([2, 3]), np.array([0, 1]), aggregates, 5, seed=0)
    assert rows == again
    assert len(rows[0]["features"]) == 6
    assert desired_replicas(200, 80, 3) == 3
    assert desired_replicas(0, 80, 3) == 1
    with pytest.raises(ValueError):
        desired_replicas(1, 0, 1)


def test_checksum_and_real_movielens_split(tmp_path: Path):
    sample = tmp_path / "bytes.bin"
    sample.write_bytes(b"abc")
    assert md5_file(sample) == "900150983cd24fb0d6963f7d28e17f72"
    root = Path(__file__).resolve().parents[1]
    raw = ensure_dataset(root)
    assert count_ratings(raw / "u.data") == 100_000
    users, items, _ratings, timestamps = load_ratings(raw / "u.data")
    split = time_split(timestamps)
    assert_split_integrity(timestamps, users, items, split)
    zip_path = root / "data" / "ml-100k.zip"
    assert md5_file(zip_path) == EXPECTED_MD5


def test_hardware_snapshot_and_peak_tracker():
    snap = hardware_snapshot()
    assert snap["ram_gb"] > 1
    assert snap["gpu"] is None
    tracker = PeakTracker(interval_s=0.05)
    tracker.start()
    _ = [bytearray(1_000_000) for _ in range(4)]
    peak = tracker.stop()
    assert peak >= 0
