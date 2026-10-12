"""Smoke test: the quick pipeline must finish and write the metric schema."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_quick_pipeline_writes_metrics():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "4"
    env["OPENBLAS_NUM_THREADS"] = "4"
    env["MKL_NUM_THREADS"] = "4"
    completed = subprocess.run(
        [sys.executable, "run.py", "--quick"],
        cwd=root,
        env=env,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, "python run.py --quick failed"
    report = json.loads((root / "results" / "metrics.json").read_text(encoding="utf-8"))
    assert report["project"] == "ml-serving-platform"
    assert report["success_criteria"]
    for key in (
        "success_rate_podkill",
        "success_rate_podkill_naive",
        "p99_spike_ms",
        "p99_spike_naive_ms",
        "p95_overhead_ms",
        "fs_p99_ms",
        "fs_python_p99_ms",
        "hpa_reaction_s",
        "canary_rollback_s",
        "canary_failed_frac",
        "onnx_max_abs_diff",
        "auth_reject_rate",
    ):
        assert key in report["metrics"]
    header = (root / "results" / "loadtest_podkill.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "row_id" in header.split(",")
    assert (root / "results" / "onnx_parity.csv").exists()
    assert (root / "results" / "k8s_events_podkill.csv").exists()
