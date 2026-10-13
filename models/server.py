"""FastAPI ONNX model process. One process is one replica."""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mlserving.textfeat import hash_text  # noqa: E402

app = FastAPI()
_lock = threading.Lock()
_rng = random.Random(int(os.environ.get("FAULT_SEED", "0")))
_state = {
    "delay_ms": float(os.environ.get("FAULT_DELAY_MS", "0")),
    "error_rate": float(os.environ.get("FAULT_ERROR_RATE", "0")),
    "requests": 0,
}
_session: ort.InferenceSession | None = None
_input_name = ""
_dtype = np.float32
_kind = os.environ.get("MODEL_KIND", "ranking")
_text_dim = int(os.environ.get("TEXT_DIM", "32"))
_burn_ms = float(os.environ.get("CPU_BURN_MS", "0"))


class PredictIn(BaseModel):
    features: list[float] | None = None
    text: str | None = None


class FaultIn(BaseModel):
    delay_ms: float | None = None
    error_rate: float | None = None


@app.on_event("startup")
def _load() -> None:
    global _session, _input_name, _dtype
    path = os.environ["MODEL_PATH"]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    _session = ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
    _input_name = _session.get_inputs()[0].name
    if "double" in _session.get_inputs()[0].type:
        _dtype = np.float64


@app.get("/health")
def health() -> dict:
    if _session is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict:
    return {"requests": _state["requests"]}


@app.post("/admin/fault")
def fault(body: FaultIn) -> dict:
    with _lock:
        if body.delay_ms is not None:
            _state["delay_ms"] = float(body.delay_ms)
        if body.error_rate is not None:
            _state["error_rate"] = float(body.error_rate)
    return {"ok": True, "delay_ms": _state["delay_ms"], "error_rate": _state["error_rate"]}


@app.post("/predict")
def predict(body: PredictIn):
    with _lock:
        delay = float(_state["delay_ms"])
        err_rate = float(_state["error_rate"])
        _state["requests"] += 1
        fail = err_rate > 0 and _rng.random() < err_rate
    if delay > 0:
        time.sleep(delay / 1000.0)
    if _burn_ms > 0:
        _busy_wait(_burn_ms)
    if fail:
        return JSONResponse({"error": "injected"}, status_code=500)
    if _session is None:
        return JSONResponse({"error": "loading"}, status_code=503)
    if _kind == "text":
        feats = hash_text(body.text or "", _text_dim)
    else:
        feats = list(body.features or [])
    array = np.asarray([feats], dtype=_dtype)
    output = _session.run(None, {_input_name: array})[0]
    score = float(np.asarray(output).reshape(-1)[0])
    return {"score": score}


def _busy_wait(ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    value = 0
    while time.perf_counter() < end:
        value = (value + 1) & 0xFFFF
    if value < 0:
        raise RuntimeError("busy wait failed")


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ["PORT"])
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
