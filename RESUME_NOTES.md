# Resume notes

## Bullets

- Built a disaggregated model-serving stack on a single-node kind Kubernetes cluster (Go gateway, Rust feature server, Dockerized ONNX models), keeping 100.0% of requests successful while one model replica was killed under load vs. 91.31% for a naive proxy.
- Cut p99 latency by 77.51% during a simulated 5x replica slowdown with hedged, latency-aware routing at 0.692475 ms p95 gateway overhead versus direct-to-model calls.
- Wrote a Rust (tokio, axum) online feature server with an in-memory sharded store and TTL, measuring 1.251343 ms p99 lookups at the 2000 RPS feature scenario (13.05x the Python dict baseline).
- Added HPA autoscaling that brought up a ready replica 25.335 s after a load step, and automatic canary rollback in 46.325 s with 0.57% of canary-scenario requests failed.

## Skills and tools

Go, Rust, tokio, axum, Python, ONNX Runtime, scikit-learn, FastAPI, Docker, Kubernetes manifests, kind config, pytest, GitHub Actions.

## Summary

This portfolio project routes prediction requests through a Go gateway to a Rust feature store and ONNX Runtime model processes. Faults are injected on purpose: replica kills, a 5x latency spike, and a bad canary.
Held-out MovieLens ratings are scored once; the largest absolute gap between the training framework and ONNX Runtime on those rows is 1.776e-15.

## GitHub description

Local disaggregated ML serving stack: Go gateway, Rust feature server, ONNX models, fault-injection benchmarks.
