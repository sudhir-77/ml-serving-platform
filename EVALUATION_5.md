# EVALUATION 5 — ml-serving-platform

**Overall: FAIL** · timings: {'install_s': 153, 'tests_s': 158, 'run_s': 57}

Evaluated independently: clean clone of the committed repo, fresh virtualenv, tests, full `python run.py`, metrics recomputed from raw artifacts.

## Engineering

| Check | Result | Detail |
|---|---|---|
| git repository present | PASS |  |
| all source changes committed | WARN | ?? EVALUATION_4.md; ?? EVALUATION_5.md |
| meaningful commit history (>= 3 commits) | PASS | 7 commits |

## Deliverables

| Check | Result | Detail |
|---|---|---|
| README.md present | PASS |  |
| requirements.txt present | PASS |  |
| run.py present | PASS |  |
| RESUME_NOTES.md present | PASS |  |

## Reproduction

| Check | Result | Detail |
|---|---|---|
| dependencies install from requirements.txt | PASS |  |
| `python run.py` completes from scratch | FAIL | 0.9 min; ile "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 462, in run_kind     cluster.up()   File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 215, in up     self.stream(   File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 191, in stream     subprocess.run(args, cwd=self.root, chec |
| results/metrics.json produced | FAIL |  |

## Tests

| Check | Result | Detail |
|---|---|---|
| pytest passes | PASS | 19 passed, 0 failed |
| at least 8 tests | PASS | 19 tests |
| coverage >= 75% | PASS | 94.6% |

## Required fixes

1. **`python run.py` completes from scratch** — 0.9 min; ile "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 462, in run_kind
    cluster.up()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 215, in up
    self.stream(
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 191, in stream
    subprocess.run(args, cwd=self.root, check=True, timeout=timeout)
  File "C:\Users\Sudhir\AppData\Local\Programs\Python\Python312\Lib\subproc
2. **results/metrics.json produced** — 

## Optional improvements

- all source changes committed — ?? EVALUATION_4.md; ?? EVALUATION_5.md

## run.py output (tail)

```
, '--config', 'C:\\Users\\Sudhir\\job-pipeline\\data\\eval\\ml-serving-platform\\5\\k8s\\kind-config.yaml']' returned non-zero exit status 1.
Creating cluster "mlserve" ...
 • Ensuring node image (kindest/node:v1.37.0) 🖼️  ...
 ✓ Ensuring node image (kindest/node:v1.37.0) 🖼️
 • Preparing nodes 📦   ...
 ✓ Preparing nodes 📦 
 • Writing configuration 📜  ...
 ✓ Writing configuration 📜
 • Starting control-plane 🕹️  ...
 ✗ Starting control-plane 🕹️
Deleted nodes: ["mlserve-control-plane"]
ERROR: failed to create cluster: failed to remove control plane taint: command "docker exec --privileged mlserve-control-plane kubectl --kubeconfig=/etc/kubernetes/admin.conf taint nodes --all node-role.kubernetes.io/control-plane-" failed with error: exit status 1

Command Output: error: taint "node-role.kubernetes.io/control-plane" not found
Deleting cluster "mlserve" ...
Traceback (most recent call last):
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\run.py", line 940, in <module>
    main()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\run.py", line 872, in main
    run_kind(ROOT, rows_path, overhead_path, feature_events(ranking["aggregates"]), prof)
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 462, in run_kind
    cluster.up()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 215, in up
    self.stream(
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\5\kind_runner.py", line 191, in stream
    subprocess.run(args, cwd=self.root, check=True, timeout=timeout)
  File "C:\Users\Sudhir\AppData\Local\Programs\Python\Python312\Lib\subprocess.py", line 571, in run
    raise CalledProcessError(retcode, process.args,
subprocess.CalledProcessError: Command '['kind', 'create', 'cluster', '--name', 'mlserve', '--config', 'C:\\Users\\Sudhir\\job-pipeline\\data\\eval\\ml-serving-platform\\5\\k8s\\kind-config.yaml']' returned non-zero exit status 1.

```
