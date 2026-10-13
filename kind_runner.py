"""Create a kind cluster, run the serving scenarios, and delete the cluster."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from mlserving.configgen import make_gateway_config
from mlserving.events import EventLog, reaction_seconds
from mlserving.metrics import read_csv

NS = "ml-serving"
CLUSTER = "mlserve"
CTX = "kind-mlserve"
TAGS = (
    "ml-ranking:local",
    "ml-gateway:local",
    "ml-featureserver:local",
    "ml-loadgen:local",
)


def log(message: str) -> None:
    print(message, flush=True)


def _docker_probe() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}} {{.OSType}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = (proc.stdout or "").strip()
    if proc.returncode == 0 and text.split()[-1:] and text.split()[-1].lower() in {"linux", "windows"}:
        return True, text
    detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    return False, detail[-400:]


def _docker_desktop_exe() -> Path | None:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LocalAppData", "")) / "Programs",
    ]
    for root in roots:
        candidate = root / "Docker" / "Docker" / "Docker Desktop.exe"
        if candidate.exists():
            return candidate
    return None


def _launch_docker() -> None:
    if os.name != "nt":
        for args in (["systemctl", "start", "docker"], ["open", "-a", "Docker"]):
            try:
                subprocess.run(args, check=False, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                continue
        return
    exe = _docker_desktop_exe()
    if exe is not None:
        log(f"starting {exe}")
        os.startfile(exe)
    else:
        log("Docker Desktop.exe was not found; trying the Windows service")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Start-Service -Name com.docker.service -ErrorAction SilentlyContinue",
        ],
        check=False,
        timeout=20,
    )


def _switch_linux_engine() -> None:
    if os.name != "nt":
        return
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    cli = program_files / "Docker" / "Docker" / "DockerCli.exe"
    if not cli.exists():
        return
    log("switching Docker to the Linux engine")
    subprocess.run([str(cli), "-SwitchLinuxEngine"], check=False, timeout=90)


def _engine_kind(detail: str) -> str:
    parts = detail.split()
    if not parts:
        return ""
    return parts[-1].lower()


def _wsl_shutdown(timeout: float = 20.0) -> bool:
    if os.name != "nt":
        return False
    log("running wsl --shutdown so Docker can start its engine")
    try:
        proc = subprocess.run(
            ["wsl.exe", "--shutdown"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log("wsl --shutdown timed out")
        return False
    except OSError as exc:
        log(f"wsl --shutdown failed: {exc}")
        return False
    log(f"wsl --shutdown exit {proc.returncode}")
    return proc.returncode == 0


def _engine_error(detail: str) -> bool:
    text = detail.lower()
    return any(token in text for token in ("wsl", "unable to start", "500 internal server error", "http 503"))


def ensure_docker(timeout: float = 300.0) -> None:
    """Start Docker Desktop when the engine is installed but stopped.

    kind talks to the Linux engine pipe. A stopped daemon makes `kind create`
    fail before any scenario can write results. If Docker reports that WSL is
    stuck, shut WSL down once and start the engine again.
    """
    ok, detail = _docker_probe()
    if ok and _engine_kind(detail) == "linux":
        log(f"docker daemon is ready ({detail})")
        return
    if ok and _engine_kind(detail) == "windows":
        log(f"docker is in Windows container mode ({detail})")
        _switch_linux_engine()
    else:
        log("docker daemon is not running; starting Docker Desktop")
        _launch_docker()
    deadline = time.time() + timeout
    last = detail
    switched = False
    recovered_wsl = False
    while time.time() < deadline:
        time.sleep(3)
        ok, last = _docker_probe()
        if ok and _engine_kind(last) == "linux":
            log(f"docker daemon is ready ({last})")
            return
        if ok and _engine_kind(last) == "windows" and not switched:
            _switch_linux_engine()
            switched = True
            continue
        if _engine_error(last) and not recovered_wsl:
            recovered_wsl = True
            if not _wsl_shutdown():
                break
            _launch_docker()
    raise RuntimeError(
        "Docker's Linux engine did not become ready. If WSL is unresponsive, "
        "restart it from an elevated PowerShell with: Restart-Service WSLService. "
        f"Last docker error: {last}"
    )


class Cluster:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.results = root / "results"
        self.logs = root / "logs"

    def stream(self, args: list[str], timeout: int) -> None:
        log(" ".join(args))
        subprocess.run(args, cwd=self.root, check=True, timeout=timeout)

    def kubectl(self, args: list[str], timeout: int = 180, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = ["kubectl", "--context", CTX, *args]
        proc = subprocess.run(
            cmd,
            cwd=self.root,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"{' '.join(cmd)} failed: {detail[-1500:]}")
        return proc

    def up(self) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        ensure_docker()
        self._delete_cluster()
        self.stream(
            ["kind", "create", "cluster", "--name", CLUSTER, "--config", str(self.root / "k8s" / "kind-config.yaml")],
            timeout=420,
        )
        self.kubectl(
            ["taint", "nodes", "--all", "node-role.kubernetes.io/control-plane-", "node-role.kubernetes.io/master-"],
            check=False,
            timeout=30,
        )
        self._build_and_load()
        self._put_config("gateway-config", self._config("gateway", self._pair(), hedge=8))
        self._put_config("naive-config", self._config("naive", [self._backend("ranking-a", "ranking-a")], hedge=0))
        self.kubectl(["apply", "-f", str(self.root / "k8s" / "metrics-server.yaml")], timeout=120)
        self.kubectl(["apply", "-f", str(self.root / "k8s" / "stack.yaml")], timeout=120)
        for name in ("featureserver", "pyfeatures", "ranking-a", "ranking-b", "ranking-v2", "text-model", "gateway", "naive"):
            self.rollout(name)
        self.kubectl(["-n", NS, "wait", "pod/loadgen", "--for=condition=Ready", "--timeout=180s"], timeout=200)
        self.kubectl(["-n", "kube-system", "rollout", "status", "deployment/metrics-server", "--timeout=180s"], timeout=200)
        for url in (
            "http://127.0.0.1:30080/health",
            "http://127.0.0.1:30081/health",
            "http://127.0.0.1:30082/health",
            "http://127.0.0.1:30083/health",
            "http://127.0.0.1:30084/health",
            "http://127.0.0.1:30085/health",
        ):
            wait_http(url)
        self._release_redpanda_if_unready()

    def down(self, remove_images: bool) -> None:
        subprocess.run(["kind", "delete", "cluster", "--name", CLUSTER], check=False, timeout=180)
        if remove_images:
            for tag in TAGS:
                subprocess.run(["docker", "rmi", tag], check=False, timeout=60)

    def rollout(self, name: str, timeout: int = 180) -> None:
        self.kubectl(["-n", NS, "rollout", "status", f"deployment/{name}", f"--timeout={timeout}s"], timeout=timeout + 30)

    def _delete_cluster(self) -> None:
        listed = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True, timeout=30, check=False)
        if CLUSTER in (listed.stdout or "").split():
            log(f"deleting leftover cluster {CLUSTER}")
            subprocess.run(["kind", "delete", "cluster", "--name", CLUSTER], check=False, timeout=180)

    def _build_and_load(self) -> None:
        builds = (
            ("models/Dockerfile.kind", "ml-ranking:local", "."),
            ("gateway/Dockerfile", "ml-gateway:local", "gateway"),
            ("featureserver/Dockerfile", "ml-featureserver:local", "featureserver"),
            ("loadgen/Dockerfile", "ml-loadgen:local", "loadgen"),
        )
        for dockerfile, tag, context in builds:
            self.stream(["docker", "build", "-f", dockerfile, "-t", tag, context], timeout=1800)
            self.stream(["kind", "load", "docker-image", tag, "--name", CLUSTER], timeout=420)

    def _release_redpanda_if_unready(self) -> None:
        proc = self.kubectl(
            ["-n", NS, "rollout", "status", "deployment/redpanda", "--timeout=45s"],
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            log("redpanda did not become ready; deleting it so the node keeps memory for the model pods")
            self.kubectl(["-n", NS, "delete", "deployment", "redpanda", "--wait=false"], check=False)

    def _put_config(self, name: str, cfg: dict) -> None:
        path = self.logs / f"{name}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        rendered = self.kubectl(
            ["-n", NS, "create", "configmap", name, f"--from-file=gateway.json={path}", "--dry-run=client", "-o", "yaml"],
            timeout=30,
        )
        self.kubectl(["apply", "-f", "-"], input_text=rendered.stdout, timeout=30)

    def use_gateway(self, cfg: dict, name: str = "gateway") -> None:
        self._put_config(f"{name}-config", cfg)
        self.kubectl(["-n", NS, "rollout", "restart", f"deployment/{name}"], timeout=30)
        self.rollout(name)
        port = 30080 if name == "gateway" else 30081
        wait_http(f"http://127.0.0.1:{port}/health")

    def _config(self, mode: str, backends: list[dict], hedge: int, **kwargs: object) -> dict:
        return make_gateway_config(
            "0.0.0.0:8080",
            mode,
            "http://featureserver:8080",
            backends,
            [{"name": "text-a", "url": "http://text-model:8000", "canary": False}],
            hedge_after_ms=hedge,
            fetch_features=True,
            canary_weight=float(kwargs.get("canary_weight", 0.0)),
            canary_min_samples=int(kwargs.get("canary_min_samples", 20)),
            canary_window_ms=int(kwargs.get("canary_window_ms", 2000)),
            canary_error_threshold=float(kwargs.get("canary_error_threshold", 0.4)),
            breaker_failures=int(kwargs.get("breaker_failures", 8)),
        )

    @staticmethod
    def _backend(name: str, service: str, canary: bool = False) -> dict:
        return {"name": name, "url": f"http://{service}:8000", "canary": canary}

    def _pair(self) -> list[dict]:
        return [self._backend("ranking-a", "ranking-a"), self._backend("ranking-b", "ranking-b")]

    def _rel(self, local: Path) -> str:
        # kubectl cp treats a drive-letter colon as a remote separator.
        return local.resolve().relative_to(self.root.resolve()).as_posix()

    def copy_in(self, local: Path, remote: str) -> None:
        self.kubectl(["-n", NS, "cp", self._rel(local), f"loadgen:{remote}"], timeout=120)

    def copy_out(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.kubectl(["-n", NS, "cp", f"loadgen:{remote}", self._rel(local)], timeout=120)

    def exec_loadgen(self, args: list[str], timeout: int) -> None:
        self.kubectl(["-n", NS, "exec", "loadgen", "--", *args], timeout=timeout)

    def loadgen(
        self,
        *,
        url: str,
        scenario: str,
        mode: str,
        target: str,
        rows_remote: str,
        out: Path,
        n: int,
        rps: int,
        concurrency: int,
        direct: str = "",
        warmup: int = 0,
        api_key: str = "both-key",
        background: bool = False,
    ) -> subprocess.Popen[str] | None:
        remote = f"/work/{out.name}"
        self.kubectl(["-n", NS, "exec", "loadgen", "--", "rm", "-f", remote], check=False, timeout=20)
        args = [
            "loadgen",
            "-url", url,
            "-scenario", scenario,
            "-mode", mode,
            "-target", target,
            "-rows", rows_remote,
            "-out", remote,
            "-n", str(n),
            "-rps", str(rps),
            "-concurrency", str(concurrency),
            "-api-key", api_key,
            "-model", "ranking",
            "-warmup", str(warmup),
        ]
        if direct:
            args.extend(["-direct", direct])
        timeout = max(180, int((n + warmup) / max(rps, 1)) + 180)
        log(f"loadgen {scenario} {mode} n={n} rps={rps}")
        if background:
            self.logs.mkdir(parents=True, exist_ok=True)
            handle = (self.logs / f"loadgen-{scenario}-{mode}.log").open("w", encoding="utf-8")
            proc = subprocess.Popen(
                ["kubectl", "--context", CTX, "-n", NS, "exec", "loadgen", "--", *args],
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            proc._log_handle = handle  # closed in finish_background
            return proc
        self.exec_loadgen(args, timeout)
        self.copy_out(remote, out)
        return None

    def ready_pods(self, selector: str) -> list[str]:
        proc = self.kubectl(["-n", NS, "get", "pods", "-l", selector, "-o", "json"], timeout=30)
        data = json.loads(proc.stdout or "{}")
        names = []
        for pod in data.get("items", []):
            conditions = pod.get("status", {}).get("conditions", [])
            ready = any(item.get("type") == "Ready" and item.get("status") == "True" for item in conditions)
            if ready:
                names.append(pod["metadata"]["name"])
        return names

    def append_api_events(self, events: EventLog) -> None:
        proc = self.kubectl(["-n", NS, "get", "events", "-o", "json"], timeout=30, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return
        data = json.loads(proc.stdout)
        for item in data.get("items", []):
            obj = str(item.get("involvedObject", {}).get("name", "cluster"))
            reason = str(item.get("reason", "Event"))
            events.add(obj, reason, source="kubectl")

    def ready_transition(self, name: str) -> float | None:
        proc = self.kubectl(["-n", NS, "get", "pod", name, "-o", "json"], timeout=30, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return None
        pod = json.loads(proc.stdout)
        for cond in pod.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "True" and cond.get("lastTransitionTime"):
                raw = str(cond["lastTransitionTime"]).replace("Z", "+00:00")
                return datetime.fromisoformat(raw).timestamp()
        return None

    def one_pod(self, selector: str) -> str | None:
        names = self.ready_pods(selector)
        return names[0] if names else None


def wait_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = "no response"
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=1)
            if response.status_code == 200:
                return
            last = f"status {response.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(0.4)
    raise TimeoutError(f"{url} not healthy: {last}")


def finish_background(proc: subprocess.Popen[str] | None, timeout: int) -> None:
    if proc is None:
        return
    handle = getattr(proc, "_log_handle", None)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        raise RuntimeError("loadgen timed out") from None
    finally:
        if handle is not None:
            handle.close()
    if proc.returncode != 0:
        detail = ""
        if handle is not None:
            detail = Path(handle.name).read_text(encoding="utf-8", errors="replace")[-800:]
        raise RuntimeError(f"loadgen exited {proc.returncode}: {detail}")


def run_kind(root: Path, workload: Path, overhead: Path, feature_events_payload: list[dict], prof: dict) -> None:
    cluster = Cluster(root)
    remove_images = False
    try:
        cluster.up()
        _prepare_features(feature_events_payload)
        cluster.copy_in(workload, "/work/workload.json")
        cluster.copy_in(overhead, "/work/overhead.json")
        _overhead(cluster, prof)
        _features(cluster, prof)
        _spike(cluster, prof)
        _canary(cluster, prof)
        _hpa(cluster, prof)
        _podkill(cluster, prof)
        (cluster.results / "run_meta.json").write_text(
            json.dumps({"fs_offered_rps": prof["feature_rps"], "execution_mode": "kind"}),
            encoding="utf-8",
        )
        remove_images = True
    finally:
        cluster.down(remove_images)


def _prepare_features(payload: list[dict]) -> None:
    for port in (30082, 30083):
        wait_http(f"http://127.0.0.1:{port}/health")
        response = requests.post(f"http://127.0.0.1:{port}/v1/events", json={"events": payload}, timeout=120)
        response.raise_for_status()


def _overhead(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_overhead.csv")
    events.add("gateway", "scenario_start")
    try:
        cluster.use_gateway(cluster._config("gateway", [cluster._backend("ranking-b", "ranking-b")], hedge=0))
        _auth_matrix(cluster.results / "auth_matrix.csv")
        _stream_check()
        cluster.loadgen(
            url="http://gateway:8080",
            scenario="overhead",
            mode="gateway",
            target="gateway",
            rows_remote="/work/overhead.json",
            out=cluster.results / "loadtest_overhead.csv",
            n=prof["overhead_n"],
            rps=0,
            concurrency=1,
            direct="http://ranking-b:8000",
            warmup=prof["overhead_warmup"],
        )
        rows = read_csv(cluster.results / "loadtest_overhead.csv")
        paired = _paired_row_ids(rows)
        log(f"overhead paired row_ids {paired}")
        if paired < 500:
            raise RuntimeError(f"overhead produced {paired} paired row_ids")
        cluster.append_api_events(events)
    finally:
        events.close()


def _features(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_features.csv")
    events.add("featureserver", "scenario_start")
    try:
        rust = cluster.results / "_part_features_rust.csv"
        python = cluster.results / "_part_features_py.csv"
        cluster.loadgen(
            url="http://featureserver:8080",
            scenario="features",
            mode="rust",
            target="features",
            rows_remote="/work/workload.json",
            out=rust,
            n=prof["feature_n"],
            rps=prof["feature_rps"],
            concurrency=prof["feature_concurrency"],
            api_key="",
            warmup=300,
        )
        cluster.loadgen(
            url="http://pyfeatures:8080",
            scenario="features",
            mode="python",
            target="features",
            rows_remote="/work/workload.json",
            out=python,
            n=prof["feature_n"],
            rps=prof["feature_rps"],
            concurrency=prof["feature_concurrency"],
            api_key="",
            warmup=100,
        )
        _concat([rust, python], cluster.results / "loadtest_features.csv")
        modes = sorted({row["mode"] for row in read_csv(cluster.results / "loadtest_features.csv")})
        log(f"feature modes {modes}")
        cluster.append_api_events(events)
    finally:
        events.close()


def _spike(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_spike.csv")
    events.add("ranking-a", "latency_spike_5x")
    try:
        _fault(30085, 80, 0)
        _fault(30084, 16, 0)
        cluster.use_gateway(cluster._config("gateway", cluster._pair(), hedge=10))
        _pair_load(cluster, "spike", prof["spike_n"], prof["spike_rps"], prof["concurrency"])
        cluster.append_api_events(events)
    finally:
        _fault(30085, 0, 0)
        _fault(30084, 0, 0)
        events.close()


def _canary(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_canary.csv")
    cfg = cluster._config(
        "gateway",
        [cluster._backend("ranking-b", "ranking-b"), cluster._backend("ranking-v2", "ranking-v2", canary=True)],
        hedge=0,
        canary_weight=0.0,
        canary_min_samples=prof["canary_min_samples"],
        canary_window_ms=180000,
        canary_error_threshold=0.4,
        breaker_failures=10000,
    )
    cluster.use_gateway(cfg)
    out = cluster.results / "loadtest_canary.csv"
    proc = cluster.loadgen(
        url="http://gateway:8080",
        scenario="canary",
        mode="gateway",
        target="gateway",
        rows_remote="/work/workload.json",
        out=out,
        n=prof["canary_n"],
        rps=prof["canary_rps"],
        concurrency=prof["concurrency"],
        background=True,
    )
    rollback_s = 999.0
    try:
        armed = requests.post(
            "http://127.0.0.1:30080/admin/canary",
            json={"model": "ranking", "weight": 0.10},
            timeout=3,
        ).json()
        events.add("ranking-v2", "canary_armed")
        deadline = time.time() + 70
        while time.time() < deadline:
            metrics = requests.get("http://127.0.0.1:30080/metrics", timeout=2).json()
            if metrics.get("canary_rolled_back"):
                rollback_s = (metrics["rollback_unix_ms"] - armed["canary_armed_unix_ms"]) / 1000.0
                events.add("ranking-v2", "canary_rollback")
                break
            time.sleep(0.05)
        finish_background(proc, timeout=max(180, int(prof["canary_n"] / prof["canary_rps"]) + 180))
        proc = None
        cluster.copy_out("/work/loadtest_canary.csv", out)
    finally:
        finish_background(proc, timeout=20)
        cluster.append_api_events(events)
        events.close()
    rows = read_csv(out)
    v2 = [row for row in rows if "v2" in row["backend_final"]]
    log(f"canary v2 rows {len(v2)} rollback {rollback_s:.3f}s")
    if len(v2) < 100:
        raise RuntimeError(f"only {len(v2)} requests reached ranking-v2")
    if rollback_s > 60:
        raise RuntimeError(f"canary rollback took {rollback_s:.3f}s")
    (cluster.results / "canary_meta.json").write_text(
        json.dumps({"canary_rollback_s": rollback_s}),
        encoding="utf-8",
    )


def _hpa(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_hpa.csv")
    try:
        cluster.kubectl(["apply", "-f", str(cluster.root / "k8s" / "hpa.yaml")], timeout=60)
        cluster.rollout("ranking-hpa", timeout=180)
        _wait_metrics(cluster)
        before = set(cluster.ready_pods("app=ranking-hpa"))
        if len(before) < 1:
            raise RuntimeError("ranking-hpa has no ready pod")
        cluster.use_gateway(
            cluster._config("gateway", [cluster._backend("ranking-hpa", "ranking-hpa")], hedge=0),
        )
        load_step = time.time()
        events.add("ranking-hpa", "load_step", ts=load_step)
        out = cluster.results / "loadtest_hpa.csv"
        proc = cluster.loadgen(
            url="http://gateway:8080",
            scenario="hpa",
            mode="gateway",
            target="gateway",
            rows_remote="/work/workload.json",
            out=out,
            n=prof["hpa_n"],
            rps=prof["hpa_rps"],
            concurrency=4,
            background=True,
        )
        added = ""
        deadline = time.time() + 100
        try:
            while time.time() < deadline:
                current = cluster.ready_pods("app=ranking-hpa")
                extra = [name for name in current if name not in before]
                if extra:
                    added = extra[0]
                    stamp = cluster.ready_transition(added)
                    if stamp is None or stamp < load_step:
                        stamp = time.time()
                    events.add(added, "scale_out_ready", source="kubectl", ts=stamp)
                    break
                if proc is not None and proc.poll() is not None:
                    break
                time.sleep(0.25)
            finish_background(proc, timeout=max(60, int(prof["hpa_n"] / max(prof["hpa_rps"], 1)) + 60))
            proc = None
            cluster.copy_out("/work/loadtest_hpa.csv", out)
        finally:
            finish_background(proc, timeout=20)
        if not added:
            described = cluster.kubectl(["-n", NS, "describe", "hpa", "ranking-hpa"], check=False, timeout=30)
            raise RuntimeError(f"HPA did not add a ready replica\n{(described.stdout or '')[-1500:]}")
        cluster.append_api_events(events)
    finally:
        events.close()
        cluster.kubectl(["-n", NS, "delete", "hpa", "ranking-hpa", "--wait=false"], check=False)
        cluster.kubectl(["-n", NS, "delete", "deployment", "ranking-hpa", "--wait=false"], check=False)
    reaction = round(reaction_seconds(read_csv(cluster.results / "k8s_events_hpa.csv")), 3)
    log(f"hpa reaction {reaction}s")
    (cluster.results / "hpa_meta.json").write_text(json.dumps({"hpa_reaction_s": reaction}), encoding="utf-8")


def _podkill(cluster: Cluster, prof: dict) -> None:
    events = EventLog(cluster.results / "k8s_events_podkill.csv")
    events.add("ranking-a", "scenario_start")
    stop = threading.Event()
    killer = threading.Thread(target=_kill_loop, args=(cluster, stop, events, prof["kill_every"]), daemon=True)
    try:
        _fault(30085, 0, 0)
        _fault(30084, 0, 0)
        cluster.use_gateway(cluster._config("gateway", cluster._pair(), hedge=8))
        killer.start()
        _pair_load(cluster, "podkill", prof["podkill_n"], prof["podkill_rps"], prof["concurrency"])
        cluster.append_api_events(events)
    finally:
        stop.set()
        killer.join(timeout=30)
        events.close()


def _kill_loop(cluster: Cluster, stop: threading.Event, events: EventLog, every: float) -> None:
    next_kill = time.time() + every
    while not stop.is_set():
        if time.time() >= next_kill:
            name = cluster.one_pod("app=ranking-a")
            if name:
                events.add(name, "pod_kill")
                cluster.kubectl(["-n", NS, "delete", "pod", name, "--wait=false"], check=False, timeout=20)
                log(f"deleted pod {name}")
            next_kill = time.time() + every
        stop.wait(0.2)


def _pair_load(cluster: Cluster, scenario: str, n: int, rps: int, concurrency: int) -> None:
    gateway = cluster.results / f"_part_{scenario}_gateway.csv"
    naive = cluster.results / f"_part_{scenario}_naive.csv"
    cluster.loadgen(
        url="http://gateway:8080",
        scenario=scenario,
        mode="gateway",
        target="gateway",
        rows_remote="/work/workload.json",
        out=gateway,
        n=n,
        rps=rps,
        concurrency=concurrency,
    )
    cluster.loadgen(
        url="http://naive:8080",
        scenario=scenario,
        mode="naive",
        target="gateway",
        rows_remote="/work/workload.json",
        out=naive,
        n=n,
        rps=rps,
        concurrency=concurrency,
    )
    _concat([gateway, naive], cluster.results / f"loadtest_{scenario}.csv")


def _wait_metrics(cluster: Cluster) -> None:
    deadline = time.time() + 150
    while time.time() < deadline:
        proc = cluster.kubectl(["top", "pods", "-n", NS], check=False, timeout=20)
        if proc.returncode == 0 and "ranking-hpa" in (proc.stdout or ""):
            log("metrics-server is reporting pod CPU")
            return
        time.sleep(3)
    raise TimeoutError("metrics-server did not report ranking-hpa CPU")


def _fault(port: int, delay_ms: float, error_rate: float) -> None:
    response = requests.post(
        f"http://127.0.0.1:{port}/admin/fault",
        json={"delay_ms": delay_ms, "error_rate": error_rate},
        timeout=3,
    )
    response.raise_for_status()


def _auth_matrix(dest: Path) -> None:
    cases = [("missing", None, 1), ("unknown", "not-a-key", 1), ("out-of-scope", "text-key", 1), ("allowed", "both-key", 0)]
    lines = ["case,status,expected_reject"]
    for name, key, expect in cases:
        headers = {}
        if key:
            headers["X-API-Key"] = key
        response = requests.post(
            "http://127.0.0.1:30080/v1/predict",
            headers=headers,
            json={"model": "ranking", "user_id": 1, "item_id": 1, "features": [0, 0, 0, 0, 0, 0]},
            timeout=5,
        )
        lines.append(f"{name},{response.status_code},{expect}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stream_check() -> None:
    response = requests.get(
        "http://127.0.0.1:30080/v1/stream",
        params={"model": "text", "text": "toy story"},
        headers={"X-API-Key": "text-key"},
        timeout=5,
    )
    response.raise_for_status()
    if "data:" not in response.text:
        raise RuntimeError("text stream did not return events")


def _concat(parts: list[Path], dest: Path) -> None:
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


def _paired_row_ids(rows: list[dict[str, str]]) -> int:
    gateway = {row["row_id"] for row in rows if row["mode"] == "gateway"}
    direct = {row["row_id"] for row in rows if row["mode"] == "direct"}
    return len(gateway & direct)


def kind_profile() -> dict:
    return {
        "podkill_n": 11000,
        "podkill_rps": 50,
        "kill_every": 30.0,
        "spike_n": 2000,
        "spike_rps": 40,
        "overhead_n": 600,
        "overhead_warmup": 80,
        "feature_n": 6000,
        "feature_rps": 2000,
        "feature_concurrency": 16,
        "hpa_n": 2500,
        "hpa_rps": 40,
        "canary_n": 20000,
        "canary_rps": 80,
        "canary_min_samples": 240,
        "concurrency": 6,
    }
