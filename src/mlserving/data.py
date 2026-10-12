"""MovieLens 100K download, checksum, and the time-ordered split."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import numpy as np
import requests

DATASET_URL = "https://files.grouplens.org/datasets/movielens/ml-100k.zip"
EXPECTED_MD5 = "0e33842e24a9c977be4e0107933c0723"
EXPECTED_ROWS = 100_000
SEED = 0


def md5_file(path: Path, chunk: int = 1 << 16) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_dataset(dest: Path, url: str = DATASET_URL, timeout: int = 120) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        import certifi

        verify: str | bool = certifi.where()
    except ImportError:
        verify = True
    with requests.get(url, stream=True, timeout=timeout, verify=verify) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for block in response.iter_content(chunk_size=1 << 16):
                if block:
                    handle.write(block)
    tmp.replace(dest)


def ensure_dataset(root: Path) -> Path:
    """Return the extracted ml-100k directory, downloading the zip if needed."""
    data_dir = root / "data"
    raw = data_dir / "ml-100k"
    zip_path = data_dir / "ml-100k.zip"
    data_dir.mkdir(parents=True, exist_ok=True)
    if not (raw / "u.data").exists():
        if not zip_path.exists() or md5_file(zip_path) != EXPECTED_MD5:
            download_dataset(zip_path)
        digest = md5_file(zip_path)
        if digest != EXPECTED_MD5:
            raise RuntimeError(f"ml-100k.zip md5 {digest} != {EXPECTED_MD5}")
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(data_dir)
    if not (raw / "u.data").exists():
        raise RuntimeError("u.data missing after extract")
    rows = count_ratings(raw / "u.data")
    if rows != EXPECTED_ROWS:
        raise RuntimeError(f"u.data has {rows} rows, expected {EXPECTED_ROWS}")
    return raw


def count_ratings(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def load_ratings(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raw = np.loadtxt(path, delimiter="\t", dtype=np.int64)
    if raw.ndim != 2 or raw.shape[1] < 4:
        raise RuntimeError(f"unexpected ratings shape {getattr(raw, 'shape', None)}")
    users = raw[:, 0]
    items = raw[:, 1]
    ratings = raw[:, 2].astype(np.float64)
    timestamps = raw[:, 3]
    return users, items, ratings, timestamps


def load_movies(item_path: Path, genre_path: Path) -> tuple[np.ndarray, list[str], np.ndarray, int]:
    drama_idx = None
    for line in genre_path.read_text(encoding="latin-1").splitlines():
        if not line.strip():
            continue
        name, idx = line.split("|")
        if name.strip().lower() == "drama":
            drama_idx = int(idx)
    if drama_idx is None:
        raise RuntimeError("Drama is not in u.genre")
    ids: list[int] = []
    titles: list[str] = []
    flags: list[list[int]] = []
    for line in item_path.read_text(encoding="latin-1").splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        ids.append(int(parts[0]))
        titles.append(parts[1])
        bits = [int(x) for x in parts[-19:]]
        if len(bits) != 19:
            raise RuntimeError(f"expected 19 genre flags, got {len(bits)}")
        flags.append(bits)
    return (
        np.asarray(ids, dtype=np.int64),
        titles,
        np.asarray(flags, dtype=np.int8),
        drama_idx,
    )


def time_split(
    timestamps: np.ndarray,
    train_frac: float = 0.70,
    val_frac: float = 0.10,
) -> dict[str, np.ndarray]:
    """Split by timestamp. Ties keep file order via a stable sort.

    The test block is the latest slice. It is saved by the caller and is not
    a tuning set.
    """
    if not 0 < train_frac < 1 or not 0 < val_frac < 1:
        raise ValueError("fractions must be in (0, 1)")
    if train_frac + val_frac >= 1:
        raise ValueError("train_frac + val_frac must leave a test slice")
    order = np.argsort(timestamps, kind="mergesort")
    n = int(order.shape[0])
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    if n_train < 1 or n_val < 1 or n_train + n_val >= n:
        raise ValueError("split is too small")
    return {
        "train": order[:n_train],
        "val": order[n_train : n_train + n_val],
        "test": order[n_train + n_val :],
    }


def assert_split_integrity(
    timestamps: np.ndarray,
    users: np.ndarray,
    items: np.ndarray,
    split: dict[str, np.ndarray],
) -> None:
    train, val, test = split["train"], split["val"], split["test"]
    sets = [set(train.tolist()), set(val.tolist()), set(test.tolist())]
    if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
        raise RuntimeError("split indices overlap")
    if len(sets[0]) + len(sets[1]) + len(sets[2]) != len(timestamps):
        raise RuntimeError("split does not cover every row")
    if timestamps[train].max() > timestamps[val].min():
        raise RuntimeError("training timestamps leak into validation")
    if timestamps[val].max() > timestamps[test].min():
        raise RuntimeError("validation timestamps leak into test")
    train_pairs = set(zip(users[train].tolist(), items[train].tolist()))
    test_pairs = set(zip(users[test].tolist(), items[test].tolist()))
    if train_pairs & test_pairs:
        raise RuntimeError("a user-item pair is in both train and test")
