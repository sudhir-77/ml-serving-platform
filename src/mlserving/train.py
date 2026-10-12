"""Fit the ranking MLP and the text linear model. The test split is scored once."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor

from mlserving.data import (
    SEED,
    assert_split_integrity,
    ensure_dataset,
    load_movies,
    load_ratings,
    time_split,
)
from mlserving.features import design_matrix, fit_aggregates
from mlserving.onnx_export import export_linear, export_mlp, max_abs_diff, mlp_forward, onnx_predict
from mlserving.textfeat import hash_text

TEXT_DIM = 32


def fit_ranking_mlp(features: np.ndarray, ratings: np.ndarray, seed: int = SEED) -> MLPRegressor:
    model = MLPRegressor(
        hidden_layer_sizes=(16,),
        activation="relu",
        solver="adam",
        batch_size=256,
        max_iter=40,
        random_state=seed,
        learning_rate_init=0.001,
        tol=1e-4,
        early_stopping=False,
    )
    model.fit(features, ratings)
    return model


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    err = np.asarray(y_true, dtype=np.float64) - np.asarray(y_pred, dtype=np.float64)
    return float(np.sqrt(np.mean(err * err)))


def train_ranking_arrays(
    user_ids: np.ndarray,
    item_ids: np.ndarray,
    ratings: np.ndarray,
    timestamps: np.ndarray,
    model_dir: Path,
    seed: int = SEED,
) -> dict:
    split = time_split(timestamps)
    assert_split_integrity(timestamps, user_ids, item_ids, split)
    train_idx = split["train"]
    aggregates = fit_aggregates(user_ids[train_idx], item_ids[train_idx], ratings[train_idx])
    x_train = design_matrix(aggregates, user_ids[train_idx], item_ids[train_idx])
    model = fit_ranking_mlp(x_train, ratings[train_idx], seed=seed)
    coefs = [np.asarray(c, dtype=np.float64) for c in model.coefs_]
    intercepts = [np.asarray(b, dtype=np.float64) for b in model.intercepts_]
    # Export uses the training fit only. Parity on the test rows is measured after.
    onnx_path = model_dir / "ranking.onnx"
    export_mlp(coefs, intercepts, x_train.shape[1], onnx_path)
    train_ref = model.predict(x_train)
    train_np = mlp_forward(coefs, intercepts, x_train)
    if max_abs_diff(train_ref, train_np) > 1e-8:
        raise RuntimeError("numpy MLP forward pass does not match sklearn")

    test_idx = split["test"]
    x_test = design_matrix(aggregates, user_ids[test_idx], item_ids[test_idx])
    y_test = ratings[test_idx]
    framework = np.asarray(model.predict(x_test), dtype=np.float64).reshape(-1)
    served = onnx_predict(onnx_path, x_test)
    diff = max_abs_diff(framework, served)
    baseline = np.full(y_test.shape, aggregates["global_mean"], dtype=np.float64)
    return {
        "aggregates": aggregates,
        "onnx_path": onnx_path,
        "split_sizes": {name: int(idx.shape[0]) for name, idx in split.items()},
        "split": split,
        "test_row_ids": test_idx.astype(np.int64),
        "y_test": y_test,
        "y_framework": framework,
        "y_onnx": served,
        "onnx_max_abs_diff": diff,
        "test_rmse": rmse(y_test, framework),
        "baseline_rmse": rmse(y_test, baseline),
        "global_mean": aggregates["global_mean"],
    }


def train_text_model(titles: list[str], drama: np.ndarray, model_dir: Path, seed: int = SEED) -> dict:
    del seed  # Ridge on this matrix is deterministic
    x = np.asarray([hash_text(title, TEXT_DIM) for title in titles], dtype=np.float64)
    y = np.asarray(drama, dtype=np.float64)
    model = Ridge(alpha=1.0)
    model.fit(x, y)
    path = model_dir / "text.onnx"
    export_linear(np.asarray(model.coef_, dtype=np.float64), float(model.intercept_), TEXT_DIM, path)
    served = onnx_predict(path, x)
    framework = np.asarray(model.predict(x), dtype=np.float64).reshape(-1)
    return {
        "onnx_path": path,
        "dim": TEXT_DIM,
        "max_abs_diff": max_abs_diff(framework, served),
        "n": int(len(titles)),
    }


def save_training_bundle(root: Path, ranking: dict, text: dict) -> Path:
    model_dir = root / "data" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    features_path = model_dir / "features.json"
    features_path.write_text(json.dumps(ranking["aggregates"]), encoding="utf-8")
    meta = {
        "text_dim": text["dim"],
        "feature_names": ranking["aggregates"]["feature_names"],
        "global_mean": ranking["global_mean"],
        "split_sizes": ranking["split_sizes"],
        "onnx_max_abs_diff": ranking["onnx_max_abs_diff"],
        "text_max_abs_diff": text["max_abs_diff"],
        "test_rmse": ranking["test_rmse"],
        "baseline_rmse": ranking["baseline_rmse"],
        "seeds": [SEED],
    }
    (model_dir / "train_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    split_path = model_dir / "split_indices.npz"
    np.savez(
        split_path,
        train=ranking["split"]["train"],
        val=ranking["split"]["val"],
        test=ranking["split"]["test"],
    )
    return model_dir


def write_parity_csv(path: Path, row_ids: np.ndarray, y_true, y_framework, y_onnx) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    diff = np.abs(np.asarray(y_framework, dtype=np.float64) - np.asarray(y_onnx, dtype=np.float64))
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("row_id,y_true,y_train_framework,y_onnx,abs_diff\n")
        for row_id, truth, framework, served, gap in zip(
            row_ids.tolist(),
            np.asarray(y_true).tolist(),
            np.asarray(y_framework).tolist(),
            np.asarray(y_onnx).tolist(),
            diff.tolist(),
        ):
            handle.write(f"{int(row_id)},{truth:.10f},{framework:.10f},{served:.10f},{gap:.12e}\n")
    return float(np.max(diff))


def train_movielens(root: Path, seed: int = SEED) -> dict:
    raw = ensure_dataset(root)
    users, items, ratings, timestamps = load_ratings(raw / "u.data")
    ranking = train_ranking_arrays(users, items, ratings, timestamps, root / "data" / "models", seed=seed)
    movie_ids, titles, flags, drama_idx = load_movies(raw / "u.item", raw / "u.genre")
    del movie_ids
    text = train_text_model(titles, flags[:, drama_idx], root / "data" / "models", seed=seed)
    save_training_bundle(root, ranking, text)
    return {"ranking": ranking, "text": text}
