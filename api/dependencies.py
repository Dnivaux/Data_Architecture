"""
Shared dependencies for the API (caching, data loading, etc.).
"""
from functools import lru_cache
from pathlib import Path

import pandas as pd


class DataCache:
    """In-memory cache for frequently accessed tables."""

    def __init__(self):
        self._cache = {}

    def get(self, key: str) -> pd.DataFrame | None:
        return self._cache.get(key)

    def set(self, key: str, value: pd.DataFrame) -> None:
        self._cache[key] = value

    def clear(self) -> None:
        self._cache.clear()


@lru_cache(maxsize=1)
def get_data_cache() -> DataCache:
    """Singleton instance of DataCache."""
    return DataCache()


def load_gold_table(filename: str) -> pd.DataFrame:
    """Load a Gold layer table, with caching."""
    cache = get_data_cache()

    # Check cache first
    if (cached := cache.get(filename)) is not None:
        return cached

    # Load from disk
    gold_path = Path(__file__).parent.parent / "data" / "gold" / filename
    if not gold_path.exists():
        return pd.DataFrame()

    df = pd.read_parquet(gold_path, engine="pyarrow")
    cache.set(filename, df)
    return df
