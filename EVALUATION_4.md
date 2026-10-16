# EVALUATION 4 — ml-serving-platform

**Overall: FAIL** · timings: {'install_s': 113, 'tests_s': 140, 'run_s': 6}

Evaluated independently: clean clone of the committed repo, fresh virtualenv, tests, full `python run.py`, metrics recomputed from raw artifacts.

## Engineering

| Check | Result | Detail |
|---|---|---|
| git repository present | PASS |  |
| all source changes committed | PASS |  |
| meaningful commit history (>= 3 commits) | PASS | 6 commits |

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
| `python run.py` completes from scratch | FAIL | 0.1 min;  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 311, in run_kind     cluster.up()   File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 64, in up     self.stream(   File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 41, in stream     subprocess.run(args, cwd=self.root, chec |
| results/metrics.json produced | FAIL |  |

## Tests

| Check | Result | Detail |
|---|---|---|
| pytest passes | PASS | 19 passed, 0 failed |
| at least 8 tests | PASS | 19 tests |
| coverage >= 75% | PASS | 94.9% |

## Required fixes

1. **`python run.py` completes from scratch** — 0.1 min;  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 311, in run_kind
    cluster.up()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 64, in up
    self.stream(
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 41, in stream
    subprocess.run(args, cwd=self.root, check=True, timeout=timeout)
  File "C:\Users\Sudhir\AppData\Local\Programs\Python\Python312\Lib\subproc
2. **results/metrics.json produced** — 

## Optional improvements

None.

## run.py output (tail)

```
 Inc.","Version":"v1.25.0","ShortDescription":"Docker Scout","Name":"scout","Path":"C:\\Program Files\\Docker\\cli-plugins\\docker-scout.exe"}],"Warnings":null}}
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
Deleting cluster "mlserve" ...
ERROR: failed to delete cluster "mlserve": error listing nodes: failed to list nodes: command "docker ps -a --filter label=io.x-k8s.kind.cluster=mlserve --format '{{.Names}}'" failed with error: exit status 1

Command Output: failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
Traceback (most recent call last):
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\run.py", line 940, in <module>
    main()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\run.py", line 872, in main
    run_kind(ROOT, rows_path, overhead_path, feature_events(ranking["aggregates"]), prof)
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 311, in run_kind
    cluster.up()
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 64, in up
    self.stream(
  File "C:\Users\Sudhir\job-pipeline\data\eval\ml-serving-platform\4\kind_runner.py", line 41, in stream
    subprocess.run(args, cwd=self.root, check=True, timeout=timeout)
  File "C:\Users\Sudhir\AppData\Local\Programs\Python\Python312\Lib\subprocess.py", line 571, in run
    raise CalledProcessError(retcode, process.args,
subprocess.CalledProcessError: Command '['kind', 'create', 'cluster', '--name', 'mlserve', '--config', 'C:\\Users\\Sudhir\\job-pipeline\\data\\eval\\ml-serving-platform\\4\\k8s\\kind-config.yaml']' returned non-zero exit status 1.

```
