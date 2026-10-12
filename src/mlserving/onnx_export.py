"""Export a fitted sklearn MLP or linear model to a double-precision ONNX graph."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper, numpy_helper


def mlp_forward(coefs: list[np.ndarray], intercepts: list[np.ndarray], x: np.ndarray) -> np.ndarray:
    """Hidden ReLU, identity output. Matches sklearn MLPRegressor."""
    hidden = np.asarray(x, dtype=np.float64)
    last = len(coefs) - 1
    for i, (weight, bias) in enumerate(zip(coefs, intercepts)):
        hidden = hidden @ weight + bias
        if i != last:
            hidden = np.maximum(hidden, 0.0)
    return np.asarray(hidden, dtype=np.float64).reshape(-1)


def _save(graph_name: str, nodes, inputs, outputs, inits, path: Path) -> None:
    graph = helper.make_graph(nodes, graph_name, inputs, outputs, inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, path)


def export_mlp(coefs: list[np.ndarray], intercepts: list[np.ndarray], n_features: int, path: Path) -> None:
    nodes = []
    inits = []
    current = "input"
    last = len(coefs) - 1
    for i, (weight, bias) in enumerate(zip(coefs, intercepts)):
        weight_name = f"W{i}"
        bias_name = f"B{i}"
        inits.append(numpy_helper.from_array(np.asarray(weight, dtype=np.float64), weight_name))
        bias_row = np.asarray(bias, dtype=np.float64).reshape(1, -1)
        inits.append(numpy_helper.from_array(bias_row, bias_name))
        gemm_out = f"gemm{i}"
        nodes.append(
            helper.make_node("Gemm", [current, weight_name, bias_name], [gemm_out], alpha=1.0, beta=1.0)
        )
        if i != last:
            relu_out = f"relu{i}"
            nodes.append(helper.make_node("Relu", [gemm_out], [relu_out]))
            current = relu_out
        else:
            current = gemm_out
    _save(
        "ranking_mlp",
        nodes,
        [helper.make_tensor_value_info("input", TensorProto.DOUBLE, [None, n_features])],
        [helper.make_tensor_value_info(current, TensorProto.DOUBLE, [None, 1])],
        inits,
        path,
    )


def export_linear(coef: np.ndarray, intercept: float, n_features: int, path: Path) -> None:
    weight = np.asarray(coef, dtype=np.float64).reshape(n_features, 1)
    bias = np.asarray([[intercept]], dtype=np.float64)
    inits = [
        numpy_helper.from_array(weight, "W"),
        numpy_helper.from_array(bias, "B"),
    ]
    nodes = [helper.make_node("Gemm", ["input", "W", "B"], ["score"], alpha=1.0, beta=1.0)]
    _save(
        "text_linear",
        nodes,
        [helper.make_tensor_value_info("input", TensorProto.DOUBLE, [None, n_features])],
        [helper.make_tensor_value_info("score", TensorProto.DOUBLE, [None, 1])],
        inits,
        path,
    )


def session_for(path: Path) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def onnx_predict(path: Path, features: np.ndarray) -> np.ndarray:
    session = session_for(path)
    input_name = session.get_inputs()[0].name
    kind = session.get_inputs()[0].type
    dtype = np.float64 if "double" in kind else np.float32
    array = np.asarray(features, dtype=dtype)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    output = session.run(None, {input_name: array})[0]
    return np.asarray(output, dtype=np.float64).reshape(-1)


def max_abs_diff(reference: np.ndarray, candidate: np.ndarray) -> float:
    ref = np.asarray(reference, dtype=np.float64).reshape(-1)
    cand = np.asarray(candidate, dtype=np.float64).reshape(-1)
    if ref.shape != cand.shape:
        raise ValueError(f"shape mismatch {ref.shape} vs {cand.shape}")
    if ref.size == 0:
        raise ValueError("empty prediction vector")
    return float(np.max(np.abs(ref - cand)))
