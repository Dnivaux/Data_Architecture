"""
Shared utilities: retry logic, structured logging, Parquet persistence.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
LOG_DATE_FMT = "%Y-%m-%dT%H:%M:%S"


def get_logger(name: str, log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT)

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# HTTP session with automatic retries
# ---------------------------------------------------------------------------

def build_session(
    retries: int = 5,
    backoff_factor: float = 1.0,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
    timeout: int = 30,
) -> requests.Session:
    """Return a requests.Session pre-configured with retry + backoff."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.request = _timeout_wrapper(session.request, timeout)  # type: ignore[method-assign]
    return session


def _timeout_wrapper(original_request: Callable, timeout: int) -> Callable:
    """Inject a default timeout into every session.request() call."""
    def wrapper(*args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", timeout)
        return original_request(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Parquet storage helpers
# ---------------------------------------------------------------------------

BRONZE_ROOT = Path(__file__).parents[2] / "data" / "bronze"


def save_parquet(
    df: pd.DataFrame,
    source: str,
    partition_col: str | None = None,
    partition_value: str | None = None,
    filename: str = "part-0.parquet",
) -> Path:
    """
    Persist *df* under  data/bronze/<source>/[<partition_col>=<partition_value>/]<filename>

    Returns the path written.
    """
    if partition_col and partition_value:
        out_dir = BRONZE_ROOT / source / f"{partition_col}={partition_value}"
    else:
        out_dir = BRONZE_ROOT / source

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    df.to_parquet(out_path, index=False, engine="pyarrow", compression="snappy")
    return out_path


def read_parquet(source: str, **filters: str) -> pd.DataFrame:
    """
    Read all parquet files under data/bronze/<source>, optionally filtering by
    partition directories whose name matches *filters* (e.g. date='2024-01-01').
    """
    root = BRONZE_ROOT / source
    if not root.exists():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for path in root.rglob("*.parquet"):
        if filters:
            parts = {p.split("=")[0]: p.split("=")[1] for p in path.parts if "=" in p}
            if not all(parts.get(k) == v for k, v in filters.items()):
                continue
        frames.append(pd.read_parquet(path, engine="pyarrow"))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
