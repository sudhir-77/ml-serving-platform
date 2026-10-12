"""Reproduce the local serving benchmarks.

Usage:
    python run.py
    python run.py --quick
    python run.py --skip-build
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import requests

from mlserving.configgen import make_gateway_config, make_workload
from mlserving.data import ensure_dataset, load_movies, load_ratings
from mlserving.features import feature_events, vector_for
from mlserving.hardware import PeakTracker, hardware_snapshot
from mlserving.autoscale import desired_replicas
from mlserving.metrics import assemble_from_results, build_report, paired_overhead_p95, write_report
from mlserving.plots import write_plots
from mlserving.resume import write_resume
from mlserving.train import train_movielens, write_parity_csv

GATEWAY_PORT = 18180
NAIVE_PORT = 18181
RUST_PORT = 18200
PY_PORT = 18201
RANK_A = 18301
RANK_B = 18302
RANK_C = 18303
RANK_V2 = 18304
TEXT_PORT = 18310


def profile(quick: bool) -> dict:
    if quick:
        return {
            "podkill_n": 120,
            "podkill_rps": 60,
            "kill_every": 4.0,
            "downtime": 0.4,
            "spike_n": 80,
            "spike_rps": 50,
            "overhead_n": 50,
            "overhead_warmup": 20,
            "feature_n": 400,
            "feature_rps": 400,
            "feature_concurrency": 8,
            "hpa_n": 160,
            "hpa_rps": 80,
            "canary_n": 160,
            "canary_rps": 80,
            "concurrency": 4,
        }
    return {
        "podkill_n": 20000,
        "podkill_rps": 250,
        "kill_every": 30.0,
        "downtime": 1.5,
        "spike_n": 4000,
        "spike_rps": 200,
        "overhead_n": 600,
        "overhead_warmup": 200,
        "feature_n": 8000,
        "feature_rps": 2000,
        "feature_concurrency": 32,
        "hpa_n": 2000,
        "hpa_rps": 200,
        "canary_n": 4000,
        "canary_rps": 200,
        "concurrency": 12,
    }


class Supervisor:
    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []
        self.logs: list[object] = []

    def spawn(self, args: list[str], log_path: Path, env: dict) -> subprocess.Popen:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "w", encoding="utf-8")
        self.logs.append(handle)
        proc = subprocess.Popen(args, stdout=handle, stderr=subprocess.STDOUT, env=env)
        self.procs.append(proc)
        return proc

    def stop(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.procs = [item for item in self.procs if item is not proc]

    def stop_all(self) -> None:
        for proc in list(self.procs):
            self.stop(proc)
        for handle in self.logs:
            handle.close()
        self.logs.clear()


def log(message: str) -> None:
    print(message, flush=True)


def base_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUNBUFFERED"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    return env


def wait_health(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last = "no response"
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=0.5)
            if response.status_code == 200:
                return
            last = f"status {response.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(0.1)
    raise TimeoutError(f"{url} not healthy: {last}")


def wait_down(url: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(url, timeout=0.2)
        except requests.RequestException:
            return
        time.sleep(0.05)


def tail(path: Path, n: int = 30) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def run_checked(args: list[str], cwd: Path, env: dict | None = None) -> None:
    log(" ".join(str(part) for part in args))
    subprocess.run(args, cwd=cwd, env=env, check=True)


def build_binaries(skip: bool) -> tuple[Path, Path, Path]:
    bindir = ROOT / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    gateway = bindir / "gateway.exe"
    loadgen = bindir / "loadgen.exe"
    feature = ROOT / "featureserver" / "target" / "release" / "featureserver.exe"
    env = os.environ.copy()
    env["GOPROXY"] = "off"
    env["CARGO_BUILD_JOBS"] = "1"
    env["CARGO_TERM_COLOR"] = "never"
    if not (skip and gateway.exists()):
        run_checked(["go", "build", "-o", str(gateway), "./cmd/gateway"], ROOT / "gateway", env)
    if not (skip and loadgen.exists()):
        run_checked(["go", "build", "-o", str(loadgen), "."], ROOT / "loadgen", env)
    if not (skip and feature.exists()):
        run_checked(["cargo", "build", "--release"], ROOT / "featureserver", env)
    return gateway, loadgen, feature


def start_model(sup: Supervisor, port: int, kind: str, model_path: Path, error_rate: float = 0.0) -> subprocess.Popen:
    env = base_env()
    env["PORT"] = str(port)
    env["HOST"] = "127.0.0.1"
    env["MODEL_KIND"] = kind
    env["MODEL_PATH"] = str(model_path)
    env["TEXT_DIM"] = "32"
    env["FAULT_ERROR_RATE"] = str(error_rate)
    env["FAULT_SEED"] = "0"
    log_path = ROOT / "logs" / f"{kind}-{port}.log"
    proc = sup.spawn([sys.executable, str(ROOT / "models" / "server.py")], log_path, env)
    try:
        wait_health(f"http://127.0.0.1:{port}/health", timeout=40)
    except TimeoutError:
        log(tail(log_path))
        raise
    return proc


def start_rust(sup: Supervisor, binary: Path) -> subprocess.Popen:
    env = base_env()
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(RUST_PORT)
    env["TOKIO_WORKER_THREADS"] = "4"
    proc = sup.spawn([str(binary)], ROOT / "logs" / "featureserver.log", env)
    try:
        wait_health(f"http://127.0.0.1:{RUST_PORT}/health", timeout=20)
    except TimeoutError:
        log(tail(ROOT / "logs" / "featureserver.log"))
        raise
    return proc


def start_python_features(sup: Supervisor) -> subprocess.Popen:
    env = base_env()
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(PY_PORT)
    proc = sup.spawn([sys.executable, str(ROOT / "models" / "feature_baseline.py")], ROOT / "logs" / "pyfeatures.log", env)
    try:
        wait_health(f"http://127.0.0.1:{PY_PORT}/health", timeout=20)
    except TimeoutError:
        log(tail(ROOT / "logs" / "pyfeatures.log"))
        raise
    return proc


def start_gateway(sup: Supervisor, binary: Path, cfg: dict, name: str) -> subprocess.Popen:
    path = ROOT / "logs" / f"{name}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    proc = sup.spawn([str(binary), str(path)], ROOT / "logs" / f"{name}.log", base_env())
    host, port = cfg["listen"].split(":")
    try:
        wait_health(f"http://{host}:{port}/health", timeout=15)
    except TimeoutError:
        log(tail(ROOT / "logs" / f"{name}.log"))
        raise
    return proc


def set_fault(port: int, delay_ms: float, error_rate: float) -> None:
    requests.post(
        f"http://127.0.0.1:{port}/admin/fault",
        json={"delay_ms": delay_ms, "error_rate": error_rate},
        timeout=2,
    ).raise_for_status()


def clear_faults() -> None:
    for port in (RANK_A, RANK_B, RANK_C):
        try:
            set_fault(port, 0, 0)
        except requests.RequestException:
            pass


def loadgen(
    binary: Path,
    *,
    url: str,
    scenario: str,
    mode: str,
    target: str,
    rows: Path,
    out: Path,
    n: int,
    rps: int,
    concurrency: int,
    direct: str = "",
    warmup: int = 0,
    api_key: str = "both-key",
) -> None:
    args = [
        str(binary),
        "-url", url,
        "-scenario", scenario,
        "-mode", mode,
        "-target", target,
        "-rows", str(rows),
        "-out", str(out),
        "-n", str(n),
        "-rps", str(rps),
        "-concurrency", str(concurrency),
        "-api-key", api_key,
        "-model", "ranking",
        "-warmup", str(warmup),
    ]
    if direct:
        args.extend(["-direct", direct])
    timeout = max(90, int(n / max(rps, 1)) + 180)
    log(f"loadgen {scenario} {mode} n={n} rps={rps}")
    subprocess.run(args, check=True, timeout=timeout)


def concat_csv(parts: list[Path], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as handle:
        wrote = False
        for part in parts:
            lines = part.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            if not wrote:
                handle.write(lines[0] + "\n")
                wrote = True
            for line in lines[1:]:
                if line.strip():
                    handle.write(line + "\n")


class EventLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8", newline="")
        self.handle.write("ts,object,event\n")

    def add(self, obj: str, event: str) -> None:
        self.handle.write(f"{time.time():.3f},{obj},{event}\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def push_features(url: str, events: list[dict]) -> None:
    response = requests.post(f"{url}/v1/events", json={"events": events}, timeout=60)
    response.raise_for_status()


def check_vectors(aggregates: dict, users, items, test_idx) -> None:
    for offset in range(0, min(20, len(test_idx))):
        idx = int(test_idx[offset])
        user_id = int(users[idx])
        item_id = int(items[idx])
        expected = vector_for(aggregates, user_id, item_id)
        for port in (RUST_PORT, PY_PORT):
            got = requests.post(
                f"http://127.0.0.1:{port}/v1/vector",
                json={"user_id": user_id, "item_id": item_id},
                timeout=2,
            ).json()["features"]
            if any(abs(a - b) > 1e-8 for a, b in zip(expected, got)):
                raise RuntimeError(f"feature mismatch on port {port} for user {user_id} item {item_id}")


def backend(name: str, port: int, canary: bool = False) -> dict:
    return {"name": name, "url": f"http://127.0.0.1:{port}", "canary": canary}


def gateway_cfg(listen_port: int, mode: str, ranking: list[dict], hedge_ms: int, canary_weight: float = 0.0, breaker: int = 4) -> dict:
    cfg = make_gateway_config(
        f"127.0.0.1:{listen_port}",
        mode,
        f"http://127.0.0.1:{RUST_PORT}",
        ranking,
        [backend("text-a", TEXT_PORT)],
        hedge_after_ms=hedge_ms,
        fetch_features=(mode == "gateway"),
        canary_weight=canary_weight,
    )
    cfg["breaker_failures"] = breaker
    return cfg


def write_auth_matrix(port: int, dest: Path) -> None:
    cases = [
        ("missing", None, 1),
        ("unknown", "not-a-key", 1),
        ("out-of-scope", "text-key", 1),
        ("allowed", "both-key", 0),
    ]
    lines = ["case,status,expected_reject"]
    for name, key, expect in cases:
        headers = {}
        if key:
            headers["X-API-Key"] = key
        response = requests.post(
            f"http://127.0.0.1:{port}/v1/predict",
            headers=headers,
            json={"model": "ranking", "user_id": 1, "item_id": 1, "features": [0, 0, 0, 0, 0, 0]},
            timeout=3,
        )
        lines.append(f"{name},{response.status_code},{expect}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stream_check(port: int) -> None:
    response = requests.get(
        f"http://127.0.0.1:{port}/v1/stream",
        params={"model": "text", "text": "toy story"},
        headers={"X-API-Key": "text-key"},
        timeout=3,
    )
    response.raise_for_status()
    if "data:" not in response.text:
        raise RuntimeError("text stream did not return events")


def run_load_pair(binary: Path, scenario: str, rows: Path, gateway_url: str, naive_url: str, n: int, rps: int, concurrency: int, results: Path) -> None:
    gw_part = results / f"_part_{scenario}_gateway.csv"
    naive_part = results / f"_part_{scenario}_naive.csv"
    loadgen(binary, url=gateway_url, scenario=scenario, mode="gateway", target="gateway", rows=rows, out=gw_part, n=n, rps=rps, concurrency=concurrency)
    loadgen(binary, url=naive_url, scenario=scenario, mode="naive", target="gateway", rows=rows, out=naive_part, n=n, rps=rps, concurrency=concurrency)
    concat_csv([gw_part, naive_part], results / f"loadtest_{scenario}.csv")


def pod_killer(sup: Supervisor, stop: threading.Event, holder: dict, events: EventLog, every: float, downtime: float, model_path: Path) -> None:
    next_kill = time.time() + every
    down = False
    while not stop.is_set():
        if time.time() >= next_kill:
            events.add("rank-a", "pod_kill")
            sup.stop(holder.get("proc"))
            down = True
            wait_down(f"http://127.0.0.1:{RANK_A}/health")
            time.sleep(downtime)
            holder["proc"] = start_model(sup, RANK_A, "ranking", model_path, 0.0)
            down = False
            events.add("rank-a", "pod_ready")
            next_kill = time.time() + every
        stop.wait(0.05)
    if down:
        holder["proc"] = start_model(sup, RANK_A, "ranking", model_path, 0.0)
        events.add("rank-a", "pod_ready")


def run_podkill(sup, binary, load_bin, rows, prof, results, model_path) -> None:
    events = EventLog(results / "k8s_events_podkill.csv")
    events.add("rank-a", "scenario_start")
    ranking = [backend("rank-a", RANK_A), backend("rank-b", RANK_B)]
    gw = start_gateway(sup, binary, gateway_cfg(GATEWAY_PORT, "gateway", ranking, hedge_ms=5), "gateway-podkill")
    naive = start_gateway(sup, binary, gateway_cfg(NAIVE_PORT, "naive", [backend("rank-a", RANK_A)], hedge_ms=0), "naive-podkill")
    holder = {"proc": sup.procs_by_port.get(RANK_A)}
    stop = threading.Event()
    thread = threading.Thread(
        target=pod_killer,
        args=(sup, stop, holder, events, prof["kill_every"], prof["downtime"], model_path),
        daemon=True,
    )
    thread.start()
    try:
        run_load_pair(
            load_bin,
            "podkill",
            rows,
            f"http://127.0.0.1:{GATEWAY_PORT}",
            f"http://127.0.0.1:{NAIVE_PORT}",
            prof["podkill_n"],
            prof["podkill_rps"],
            prof["concurrency"],
            results,
        )
    finally:
        stop.set()
        thread.join(timeout=30)
        sup.stop(gw)
        sup.stop(naive)
        events.close()


def run_spike(sup, binary, load_bin, rows, prof, results) -> None:
    events = EventLog(results / "k8s_events_spike.csv")
    events.add("rank-a", "latency_spike_5x")
    set_fault(RANK_A, 75, 0)
    set_fault(RANK_B, 15, 0)
    ranking = [backend("rank-a", RANK_A), backend("rank-b", RANK_B)]
    gw = start_gateway(sup, binary, gateway_cfg(GATEWAY_PORT, "gateway", ranking, hedge_ms=8), "gateway-spike")
    naive = start_gateway(sup, binary, gateway_cfg(NAIVE_PORT, "naive", [backend("rank-a", RANK_A)], hedge_ms=0), "naive-spike")
    try:
        run_load_pair(
            load_bin,
            "spike",
            rows,
            f"http://127.0.0.1:{GATEWAY_PORT}",
            f"http://127.0.0.1:{NAIVE_PORT}",
            prof["spike_n"],
            prof["spike_rps"],
            min(8, prof["concurrency"]),
            results,
        )
    finally:
        clear_faults()
        sup.stop(gw)
        sup.stop(naive)
        events.close()


def run_overhead(sup, binary, load_bin, rows, prof, results) -> None:
    events = EventLog(results / "k8s_events_overhead.csv")
    events.add("gateway", "scenario_start")
    clear_faults()
    gw = start_gateway(
        sup,
        binary,
        gateway_cfg(GATEWAY_PORT, "gateway", [backend("rank-b", RANK_B)], hedge_ms=0),
        "gateway-overhead",
    )
    try:
        write_auth_matrix(GATEWAY_PORT, results / "auth_matrix.csv")
        stream_check(GATEWAY_PORT)
        part = results / "_part_overhead.csv"
        loadgen(
            load_bin,
            url=f"http://127.0.0.1:{GATEWAY_PORT}",
            scenario="overhead",
            mode="gateway",
            target="gateway",
            rows=rows,
            out=part,
            n=prof["overhead_n"],
            rps=0,
            concurrency=1,
            direct=f"http://127.0.0.1:{RANK_B}",
            warmup=prof["overhead_warmup"],
        )
        part.replace(results / "loadtest_overhead.csv")
        log(f"overhead p95 so far {paired_overhead_p95(read_rows(results / 'loadtest_overhead.csv')):.3f} ms")
    finally:
        sup.stop(gw)
        events.close()


def read_rows(path: Path):
    import csv

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def run_features(load_bin, rows, prof, results) -> None:
    events = EventLog(results / "k8s_events_features.csv")
    events.add("featureserver", "scenario_start")
    rust_part = results / "_part_features_rust.csv"
    py_part = results / "_part_features_py.csv"
    try:
        loadgen(
            load_bin,
            url=f"http://127.0.0.1:{RUST_PORT}",
            scenario="features",
            mode="gateway",
            target="features",
            rows=rows,
            out=rust_part,
            n=prof["feature_n"],
            rps=prof["feature_rps"],
            concurrency=prof["feature_concurrency"],
            api_key="",
            warmup=400,
        )
        loadgen(
            load_bin,
            url=f"http://127.0.0.1:{PY_PORT}",
            scenario="features",
            mode="naive",
            target="features",
            rows=rows,
            out=py_part,
            n=prof["feature_n"],
            rps=prof["feature_rps"],
            concurrency=prof["feature_concurrency"],
            api_key="",
            warmup=100,
        )
        concat_csv([rust_part, py_part], results / "loadtest_features.csv")
    finally:
        events.close()


def gateway_rps(port: int, window: float = 0.4) -> float:
    first = requests.get(f"http://127.0.0.1:{port}/metrics", timeout=1).json()["requests"]
    time.sleep(window)
    second = requests.get(f"http://127.0.0.1:{port}/metrics", timeout=1).json()["requests"]
    return max(0.0, (second - first) / window)


def run_hpa(sup, binary, load_bin, rows, prof, results, model_path) -> None:
    events = EventLog(results / "k8s_events_hpa.csv")
    events.add("ranking", "scenario_start")
    gw = start_gateway(
        sup,
        binary,
        gateway_cfg(GATEWAY_PORT, "gateway", [backend("rank-b", RANK_B)], hedge_ms=0),
        "gateway-hpa",
    )
    part = results / "_part_hpa.csv"
    proc = subprocess.Popen(
        [
            str(load_bin), "-url", f"http://127.0.0.1:{GATEWAY_PORT}", "-scenario", "hpa", "-mode", "gateway",
            "-target", "gateway", "-rows", str(rows), "-out", str(part), "-n", str(prof["hpa_n"]),
            "-rps", str(prof["hpa_rps"]), "-concurrency", str(min(8, prof["concurrency"])),
            "-api-key", "both-key", "-model", "ranking",
        ]
    )
    reaction = None
    started = time.time()
    extra = None
    try:
        deadline = started + 25
        while time.time() < deadline and proc.poll() is None:
            rps = gateway_rps(GATEWAY_PORT, 0.3)
            if desired_replicas(rps, 40, 2) > 1 and extra is None:
                log(f"hpa observed {rps:.1f} rps, starting a replica")
                events.add("rank-c", "scale_out_begin")
                extra = start_model(sup, RANK_C, "ranking", model_path, 0.0)
                requests.post(
                    f"http://127.0.0.1:{GATEWAY_PORT}/admin/backends",
                    json={"model": "ranking", "name": "rank-c", "url": f"http://127.0.0.1:{RANK_C}"},
                    timeout=2,
                ).raise_for_status()
                reaction = time.time() - started
                events.add("rank-c", "scale_out_ready")
                break
            time.sleep(0.05)
        proc.wait(timeout=180)
        if proc.returncode not in (0, None):
            raise RuntimeError(f"hpa loadgen exited {proc.returncode}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        sup.stop(gw)
        events.close()
    if reaction is None:
        reaction = 999.0
        events_path = results / "k8s_events_hpa.csv"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f},rank-c,scale_out_timeout\n")
    part.replace(results / "loadtest_hpa.csv")
    (results / "hpa_meta.json").write_text(json.dumps({"hpa_reaction_s": reaction}), encoding="utf-8")


def run_canary(sup, binary, load_bin, rows, prof, results) -> None:
    events = EventLog(results / "k8s_events_canary.csv")
    ranking = [backend("rank-b", RANK_B), backend("rank-v2", RANK_V2, canary=True)]
    cfg = gateway_cfg(GATEWAY_PORT, "gateway", ranking, hedge_ms=5, canary_weight=0.0, breaker=100)
    gw = start_gateway(sup, binary, cfg, "gateway-canary")
    part = results / "_part_canary.csv"
    proc = subprocess.Popen(
        [
            str(load_bin), "-url", f"http://127.0.0.1:{GATEWAY_PORT}", "-scenario", "canary", "-mode", "gateway",
            "-target", "gateway", "-rows", str(rows), "-out", str(part), "-n", str(prof["canary_n"]),
            "-rps", str(prof["canary_rps"]), "-concurrency", str(min(8, prof["concurrency"])),
            "-api-key", "both-key", "-model", "ranking",
        ]
    )
    rollback_s = 999.0
    try:
        armed = requests.post(
            f"http://127.0.0.1:{GATEWAY_PORT}/admin/canary",
            json={"model": "ranking", "weight": 0.15},
            timeout=2,
        ).json()
        events.add("rank-v2", "canary_armed")
        deadline = time.time() + 30
        while time.time() < deadline:
            metrics = requests.get(f"http://127.0.0.1:{GATEWAY_PORT}/metrics", timeout=1).json()
            if metrics.get("canary_rolled_back"):
                rollback_s = (metrics["rollback_unix_ms"] - armed["canary_armed_unix_ms"]) / 1000.0
                events.add("rank-v2", "canary_rollback")
                break
            time.sleep(0.05)
        proc.wait(timeout=180)
        if proc.returncode not in (0, None):
            raise RuntimeError(f"canary loadgen exited {proc.returncode}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        sup.stop(gw)
        events.close()
    part.replace(results / "loadtest_canary.csv")
    (results / "canary_meta.json").write_text(json.dumps({"canary_rollback_s": rollback_s}), encoding="utf-8")


def parse_go_coverage() -> float:
    env = os.environ.copy()
    env["GOPROXY"] = "off"
    profile = ROOT / "gateway" / "coverage.out"
    subprocess.run(["go", "test", "-coverprofile", str(profile), "./..."], cwd=ROOT / "gateway", env=env, check=True)
    output = subprocess.check_output(["go", "tool", "cover", "-func", str(profile)], cwd=ROOT / "gateway", text=True)
    for line in output.splitlines():
        if line.startswith("total:"):
            percent = line.split()[-1].rstrip("%")
            return float(percent) / 100.0
    raise RuntimeError("go coverage total missing")


def parse_python_coverage() -> float:
    dest = ROOT / "results" / "py_cov.json"
    subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_library.py", "-q", "--cov=mlserving", f"--cov-report=json:{dest}"],
        cwd=ROOT,
        check=True,
    )
    data = json.loads(dest.read_text(encoding="utf-8"))
    return float(data["totals"]["percent_covered"]) / 100.0


def rust_tests_ok() -> bool:
    env = os.environ.copy()
    env["CARGO_BUILD_JOBS"] = "1"
    result = subprocess.run(["cargo", "test", "--release"], cwd=ROOT / "featureserver", env=env)
    return result.returncode == 0


def update_readme(report: dict) -> None:
    path = ROOT / "README.md"
    if not path.exists():
        return
    lines = [
        "| Criterion | Value | Op | Threshold | Passed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["success_criteria"]:
        high = item["threshold_high"]
        threshold = item["threshold"] if high is None else f"{item['threshold']} to {high}"
        value = item["value"]
        if isinstance(value, float):
            rendered = f"{value:.6g}"
        else:
            rendered = str(value)
        lines.append(f"| {item['name']} | {rendered} | {item['op']} | {threshold} | {item['passed']} |")
    metrics = report["metrics"]
    lines.extend(
        [
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| success_rate_podkill | {metrics['success_rate_podkill']:.6g} |",
            f"| success_rate_podkill_naive | {metrics['success_rate_podkill_naive']:.6g} |",
            f"| p99_spike_ms | {metrics['p99_spike_ms']:.6g} |",
            f"| p99_spike_naive_ms | {metrics['p99_spike_naive_ms']:.6g} |",
            f"| p95_overhead_ms | {metrics['p95_overhead_ms']:.6g} |",
            f"| fs_p99_ms | {metrics['fs_p99_ms']:.6g} |",
            f"| fs_python_p99_ms | {metrics['fs_python_p99_ms']:.6g} |",
            f"| hpa_reaction_s | {metrics['hpa_reaction_s']:.6g} |",
            f"| canary_rollback_s | {metrics['canary_rollback_s']:.6g} |",
            f"| canary_failed_frac | {metrics['canary_failed_frac']:.6g} |",
            f"| onnx_max_abs_diff | {metrics['onnx_max_abs_diff']:.6g} |",
            f"| auth_reject_rate | {metrics['auth_reject_rate']:.6g} |",
        ]
    )
    section = "\n".join(lines)
    text = path.read_text(encoding="utf-8")
    start, end = "<!-- RESULTS_START -->", "<!-- RESULTS_END -->"
    if start in text and end in text:
        pre, rest = text.split(start, 1)
        _, post = rest.split(end, 1)
        path.write_text(pre + start + "\n" + section + "\n" + end + post, encoding="utf-8")


def scenario_enabled(only: str, name: str) -> bool:
    return not only or only == name


def main() -> None:
    parser = argparse.ArgumentParser(description="Train models and run local serving scenarios")
    parser.add_argument("--quick", action="store_true", help="small subset, under a few minutes once binaries exist")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--only", default="", help="run one scenario: overhead, features, spike, podkill, hpa, canary")
    args = parser.parse_args()
    os.chdir(ROOT)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    started = time.time()
    tracker = PeakTracker()
    tracker.start()
    sup = Supervisor()
    sup.procs_by_port = {}
    try:
        log("training on MovieLens 100K")
        trained = train_movielens(ROOT)
        ranking = trained["ranking"]
        raw = ensure_dataset(ROOT)
        users, items, _ratings, _timestamps = load_ratings(raw / "u.data")
        _ids, titles, _flags, _drama = load_movies(raw / "u.item", raw / "u.genre")
        results = ROOT / "results"
        write_parity_csv(
            results / "onnx_parity.csv",
            ranking["test_row_ids"],
            ranking["y_test"],
            ranking["y_framework"],
            ranking["y_onnx"],
        )
        (results / "train_meta.json").write_text(
            json.dumps(
                {
                    "test_rmse": ranking["test_rmse"],
                    "baseline_rmse": ranking["baseline_rmse"],
                    "execution_mode": "local_processes",
                    "splits": {"movielens_100k": ranking["split_sizes"]},
                }
            ),
            encoding="utf-8",
        )
        prof = profile(args.quick)
        test_idx = ranking["split"]["test"]
        workload = make_workload(
            users[test_idx],
            items[test_idx],
            test_idx,
            ranking["aggregates"],
            n=max(prof["podkill_n"], prof["spike_n"], prof["feature_n"], prof["hpa_n"], prof["canary_n"]),
            seed=0,
            texts=titles[:50],
        )
        rows_path = ROOT / "data" / "models" / "workload.json"
        rows_path.write_text(json.dumps(workload), encoding="utf-8")
        overhead_idx = test_idx[:32]
        overhead_rows = make_workload(
            users[overhead_idx],
            items[overhead_idx],
            overhead_idx,
            ranking["aggregates"],
            n=max(32, prof["overhead_n"]),
            seed=1,
            texts=titles[:10],
        )
        overhead_path = ROOT / "data" / "models" / "workload_overhead.json"
        overhead_path.write_text(json.dumps(overhead_rows), encoding="utf-8")

        gateway_bin, load_bin, feature_bin = build_binaries(args.skip_build)
        model_path = ROOT / "data" / "models" / "ranking.onnx"
        text_path = ROOT / "data" / "models" / "text.onnx"
        start_rust(sup, feature_bin)
        start_python_features(sup)
        rank_a = start_model(sup, RANK_A, "ranking", model_path, 0.0)
        sup.procs_by_port[RANK_A] = rank_a
        start_model(sup, RANK_B, "ranking", model_path, 0.0)
        start_model(sup, RANK_V2, "ranking", model_path, 0.9)
        start_model(sup, TEXT_PORT, "text", text_path, 0.0)
        events = feature_events(ranking["aggregates"])
        push_features(f"http://127.0.0.1:{RUST_PORT}", events)
        push_features(f"http://127.0.0.1:{PY_PORT}", events)
        check_vectors(ranking["aggregates"], users, items, test_idx)

        if scenario_enabled(args.only, "overhead"):
            run_overhead(sup, gateway_bin, load_bin, overhead_path, prof, results)
        if scenario_enabled(args.only, "features"):
            run_features(load_bin, rows_path, prof, results)
        if scenario_enabled(args.only, "spike"):
            run_spike(sup, gateway_bin, load_bin, rows_path, prof, results)
        if scenario_enabled(args.only, "canary"):
            run_canary(sup, gateway_bin, load_bin, rows_path, prof, results)
        if scenario_enabled(args.only, "hpa"):
            run_hpa(sup, gateway_bin, load_bin, rows_path, prof, results, model_path)
        if scenario_enabled(args.only, "podkill"):
            run_podkill(sup, gateway_bin, load_bin, rows_path, prof, results, model_path)

        if args.only:
            log(f"finished scenario {args.only}")
            return

        sup.stop_all()
        if args.quick:
            cover = {"go_coverage": 0.0, "python_coverage": 0.0, "rust_tests_passed": False}
        else:
            log("measuring coverage")
            cover = {
                "go_coverage": parse_go_coverage(),
                "python_coverage": parse_python_coverage(),
                "rust_tests_passed": rust_tests_ok(),
            }
        (results / "coverage_meta.json").write_text(json.dumps(cover), encoding="utf-8")
        metrics = assemble_from_results(results)
        hardware = hardware_snapshot()
        hardware["peak_rss_gb"] = round(tracker.peak_bytes / (1024**3), 4)
        report = build_report(
            metrics,
            {"movielens_100k": ranking["split_sizes"]},
            [0],
            hardware,
            time.time() - started,
        )
        # Peak is still moving; the final patch happens in finally after children exit.
        report["_pending_runtime"] = True
        write_report(results / "metrics.json", report)
        write_plots(results, metrics)
        write_resume(ROOT / "RESUME_NOTES.md", report)
        update_readme(report)
        for item in report["success_criteria"]:
            log(f"{'PASS' if item['passed'] else 'FAIL'} {item['name']}: {item['value']}")
    finally:
        sup.stop_all()
        peak = tracker.stop()
        metrics_path = ROOT / "results" / "metrics.json"
        if metrics_path.exists() and not args.only:
            report = json.loads(metrics_path.read_text(encoding="utf-8"))
            report["hardware"]["peak_rss_gb"] = round(peak, 4)
            report["runtime_seconds"] = round(time.time() - started, 3)
            report.pop("_pending_runtime", None)
            metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log(f"peak rss {peak:.3f} GB, runtime {time.time() - started:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"run failed: {exc}")
        raise
