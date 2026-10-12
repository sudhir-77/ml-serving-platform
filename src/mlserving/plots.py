"""Plots drawn from the load-test CSVs."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from mlserving.metrics import filter_rows, read_csv


def _latencies(path: Path, scenario: str, mode: str) -> list[float]:
    rows = filter_rows(read_csv(path), scenario, mode)
    return [float(row["latency_ms"]) for row in rows if int(float(row["status"])) == 200]


def plot_latency_compare(csv_path: Path, scenario: str, dest: Path, title: str) -> None:
    gateway = _latencies(csv_path, scenario, "gateway")
    naive = _latencies(csv_path, scenario, "naive")
    fig, ax = plt.subplots(figsize=(7, 4))
    if gateway:
        ax.hist(gateway, bins=40, alpha=0.7, label="gateway")
    if naive:
        ax.hist(naive, bins=40, alpha=0.7, label="naive")
    ax.set_xlabel("latency ms")
    ax.set_ylabel("requests")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=100)
    plt.close(fig)


def plot_success_bars(metrics: dict, dest: Path) -> None:
    labels = ["pod kill gateway", "pod kill naive"]
    vals = [
        float(metrics["success_rate_podkill"]) * 100.0,
        float(metrics["success_rate_podkill_naive"]) * 100.0,
    ]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(labels, vals, color=["#2c7fb8", "#f03b20"])
    ax.set_ylim(0, 100)
    ax.set_ylabel("success %")
    ax.set_title("Success rate while one replica is killed")
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=100)
    plt.close(fig)


def write_plots(results: Path, metrics: dict) -> list[Path]:
    paths = [
        results / "latency_podkill.png",
        results / "latency_spike.png",
        results / "success_podkill.png",
    ]
    plot_latency_compare(
        results / "loadtest_podkill.csv",
        "podkill",
        paths[0],
        "Latency during pod kills",
    )
    plot_latency_compare(
        results / "loadtest_spike.csv",
        "spike",
        paths[1],
        "Latency during a 5x replica spike",
    )
    plot_success_bars(metrics, paths[2])
    return paths
