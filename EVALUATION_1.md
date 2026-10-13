# EVALUATION 1 — ml-serving-platform

**Overall: FAIL** · timings: {'install_s': 126, 'tests_s': 200, 'run_s': 312}

Evaluated independently: clean clone of the committed repo, fresh virtualenv, tests, full `python run.py`, metrics recomputed from raw artifacts.

## Engineering

| Check | Result | Detail |
|---|---|---|
| git repository present | PASS |  |
| all source changes committed | WARN |  M SPEC.md |
| meaningful commit history (>= 3 commits) | PASS | 5 commits |
| ruff lint (E,F,W,B) clean | WARN | 20 issues; run.py:14:8: F401 [*] `statistics` imported but unused run.py:29:1: E402 Module level import not at top of file run.py:31:1: E402 Module level import not at top of file run.py:32:1: E402 Module level import not at top of file run.py:33:1: E402 Module level import not at top of file run.py:34:1: E402 Module level import not at top of file run.py:35:1: E402 Module level import not at top  |
| no secrets in repo | PASS |  |
| no hard-coded absolute paths | PASS |  |
| requirements pinned with == | PASS |  |
| .gitignore covers .venv and data | PASS |  |

## Deliverables

| Check | Result | Detail |
|---|---|---|
| README.md present | PASS |  |
| requirements.txt present | PASS |  |
| run.py present | PASS |  |
| RESUME_NOTES.md present | PASS |  |
| metrics.json has the contract's top-level keys | PASS |  |
| README has mermaid diagram | PASS |  |
| README has setup instructions | PASS |  |
| README has run instructions | PASS |  |
| README has limitations section | PASS |  |
| README has dataset license | PASS |  |

## Reproduction

| Check | Result | Detail |
|---|---|---|
| dependencies install from requirements.txt | PASS |  |
| `python run.py` completes from scratch | PASS | 5.2 min |
| metrics reproduce within ±2% (48 values compared) | FAIL | success_criteria[4].value: claimed 7.53976 vs reproduced 8.73944; success_criteria[5].value: claimed 1.61555 vs reproduced 1.57732; metrics.p99_cut: claimed 0.756395 vs reproduced 0.71443; metrics.p99_cut_pct: claimed 75.64 vs reproduced 71.44; metrics.hpa_reaction_s: claimed 1.61555 vs reproduced 1.57732 |
| no claimed metric missing from reproduction | PASS |  |

## Tests

| Check | Result | Detail |
|---|---|---|
| pytest passes | PASS | 17 passed, 0 failed |
| at least 8 tests | PASS | 17 tests |
| coverage >= 75% | PASS | 94.3% |

## Criteria

| Check | Result | Detail |
|---|---|---|
| scenarios ran on a kind Kubernetes cluster (execution_mode == kind) | FAIL | local_processes |
| gateway success rate >= 99.5% with pods killed | PASS | {'gateway': 1.0, 'naive': 0.93875} |
| p99 under 5x spike >= 40% lower than naive | PASS | 23.54 vs 82.43 |
| p95 gateway overhead < 5 ms (paired) | PASS | 0.537 ms on 32 pairs |
| canary: rollback within 60 s and < 1% of requests failed | PASS | rollback 0.825 s, failed 0.0000 |
| ONNX parity max abs diff < 1e-5 | PASS | 1.7763568394e-15 |
| all unauthorized / out-of-scope requests rejected | PASS | 1.0 on 3 cases |
| self-reported: Success rate when one model pod is killed every 30 s under load | PASS | value 1.0 >= 0.995 |
| self-reported: p99 latency under a 5x latency spike on one replica is at least 40% lower than the naive proxy | PASS | value 0.7144300937912708 >= 0.4 |
| self-reported: p95 gateway overhead without faults | PASS | value 0.5368699999999997 < 5.0 |
| self-reported: Rust feature server p99 lookup latency at 2,000 RPS | PASS | value 1.0843 < 2.0 |
| self-reported: Rust feature server at least 3x faster than a Python feature-lookup baseline | PASS | value 8.739436502812877 >= 3.0 |
| self-reported: HPA scale-out reaction time from load step to added ready replica | PASS | value 1.5773162841796875 between 0.01 |
| self-reported: Canary rollback within 60 s | PASS | value 0.825 <= 60.0 |
| self-reported: Canary failed fraction | PASS | value 0.0 < 0.01 |
| self-reported: ONNX serving parity | PASS | value 1.7763568394e-15 < 1e-05 |
| self-reported: Unauthorized or out-of-scope requests rejected | PASS | value 1.0 >= 1.0 |
| self-reported: Go test coverage | PASS | value 0.846 >= 0.75 |
| self-reported: Python test coverage | PASS | value 0.8123028391167192 >= 0.8 |
| self-reported: Rust tests for every public function | PASS | value 1.0 >= 1.0 |

## Ml

| Check | Result | Detail |
|---|---|---|
| >= 10,000 pod-kill requests per mode | PASS | {'gateway': 20000, 'naive': 20000} |
| >= 500 paired gateway/direct requests | FAIL | 32 |
| features file labels modes rust and python | FAIL | ['gateway', 'naive'] |
| k8s event logs have a source column | FAIL | ['event', 'object', 'ts'] |
| HPA scale-out observed from the API server | FAIL | 0 |
| >= 100 requests reached the bad v2 before rollback | FAIL | 4 |
| go test ./... passes | PASS | ok  	mlserving/gateway/cmd/gateway	(cached)	coverage: 66.7% of statements ok  	mlserving/gateway/internal/auth	(cached)	coverage: 100.0% of statements ok  	mlserving/gateway/internal/canary	(cached)	coverage: 84.3% of statements ok  	mlserving/gateway/internal/circuit	(cached)	coverage: 88.2% of statements ok  	mlserving/gateway/internal/gw	(cached)	coverage: 81.9% of statements ok  	mlserving/gat |
| cargo test passes | PASS |  running 7 tests ....... test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s   running 0 tests  test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s   running 0 tests  test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s   |
| claimed p95_overhead_ms matches recomputation (within 10% or 0.2 ms) | PASS | 0.5368699999999997 vs 0.537 |

## Resume

| Check | Result | Detail |
|---|---|---|
| every number in RESUME_NOTES bullets is in the reproduced metrics | FAIL | 2,000 in: Wrote a Rust (tokio, axum) online feature server with an in-memory sharded store and TTL, measuring 1.084 ms p |

## Recomputed headline metrics

```json
{
  "execution_mode": "local_processes",
  "success_podkill": {
    "gateway": 1.0,
    "naive": 0.93875
  },
  "n_podkill": {
    "gateway": 20000,
    "naive": 20000
  },
  "p99_spike": {
    "gateway": 23.53884399999999,
    "naive": 82.427607
  },
  "p99_cut": 0.7144300937912708,
  "p95_overhead_ms": 0.5368699999999998,
  "canary_v2_requests": 4,
  "canary_failed_frac": 0.0,
  "canary_rollback_s": 0.825
}
```

## Required fixes

1. **metrics reproduce within ±2% (48 values compared)** — success_criteria[4].value: claimed 7.53976 vs reproduced 8.73944; success_criteria[5].value: claimed 1.61555 vs reproduced 1.57732; metrics.p99_cut: claimed 0.756395 vs reproduced 0.71443; metrics.p99_cut_pct: claimed 75.64 vs reproduced 71.44; metrics.hpa_reaction_s: claimed 1.61555 vs reproduced 1.57732
2. **scenarios ran on a kind Kubernetes cluster (execution_mode == kind)** — local_processes
3. **>= 500 paired gateway/direct requests** — 32
4. **features file labels modes rust and python** — ['gateway', 'naive']
5. **k8s event logs have a source column** — ['event', 'object', 'ts']
6. **HPA scale-out observed from the API server** — 0
7. **>= 100 requests reached the bad v2 before rollback** — 4
8. **every number in RESUME_NOTES bullets is in the reproduced metrics** — 2,000 in: Wrote a Rust (tokio, axum) online feature server with an in-memory sharded store and TTL, measuring 1.084 ms p

## Optional improvements

- all source changes committed —  M SPEC.md
- ruff lint (E,F,W,B) clean — 20 issues; run.py:14:8: F401 [*] `statistics` imported but unused
run.py:29:1: E402 Module level import not at top of file
run.py:31:1: E402 Module level import not at top of file
run.py:32:1: E402 Module level import not at top of file
run.py:33:1: E402 Module level import not at top of file
run.py

## run.py output (tail)

```
::http_roundtrip_reads_an_ingested_vector ... ok

test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

PASS Success rate when one model pod is killed every 30 s under load: 1.0
PASS p99 latency under a 5x latency spike on one replica is at least 40% lower than the naive proxy: 0.7144300937912708
PASS p95 gateway overhead without faults: 0.5368699999999997
PASS Rust feature server p99 lookup latency at 2,000 RPS: 1.0843
PASS Rust feature server at least 3x faster than a Python feature-lookup baseline: 8.739436502812877
PASS HPA scale-out reaction time from load step to added ready replica: 1.5773162841796875
PASS Canary rollback within 60 s: 0.825
PASS Canary failed fraction: 0.0
PASS ONNX serving parity: 1.7763568394e-15
PASS Unauthorized or out-of-scope requests rejected: 1.0
PASS Go test coverage: 0.846
PASS Python test coverage: 0.8123028391167192
PASS Rust tests for every public function: 1.0
peak rss 0.837 GB, runtime 309.0s
    Finished `release` profile [optimized] target(s) in 0.14s
C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\1\.venv\Lib\site-packages\coverage\inorout.py:574: CoverageWarning: Module mlserving was previously imported, but not measured (module-not-measured); see https://coverage.readthedocs.io/en/7.16.2/messages.html#warning-module-not-measured
  self.warn(msg, slug="module-not-measured")
   Compiling featureserver v0.1.0 (C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\1\featureserver)
    Finished `release` profile [optimized] target(s) in 11.93s
     Running unittests src\lib.rs (target\release\deps\featureserver-d946b8b8fa8b16d3.exe)
     Running unittests src\main.rs (target\release\deps\featureserver-5fbc488d9cff0677.exe)
   Doc-tests featureserver

```
