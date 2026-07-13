"""Local parquet cache for OHLCV history.

One parquet + one small meta JSON per symbol. A cache entry is reused when it
was fetched today and covers the requested window — so re-running the same
day (or a backfill after the daily run) costs zero downloads. In CI the cache
directory can be persisted with actions/cache to warm up subsequent runs.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def _safe_name(symbol: str) -> str:
    return symbol.replace("^", "_").replace("/", "-")


class OHLCVCache:
    def __init__(self, cache_dir: str | Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, symbol: str) -> tuple[Path, Path]:
        base = self.dir / _safe_name(symbol)
        return base.with_suffix(".parquet"), base.with_suffix(".json")

    def get(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        pq, meta_path = self._paths(symbol)
        if not pq.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            fresh = (meta.get("fetched") == date.today().isoformat()
                     and meta.get("start") <= start.isoformat()
                     and meta.get("end") >= end.isoformat())
            if not fresh:
                return None
            df = pd.read_parquet(pq)
            df.index = pd.to_datetime(df.index)
            return df.loc[str(start):str(end)]
        except Exception as exc:  # noqa: BLE001 - corrupt cache -> refetch
            log.warning("Cache read failed for %s (%s); refetching", symbol, exc)
            return None

    def put(self, symbol: str, df: pd.DataFrame, start: date, end: date) -> None:
        pq, meta_path = self._paths(symbol)
        try:
            df.to_parquet(pq)
            meta_path.write_text(json.dumps({
                "fetched": date.today().isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": int(len(df)),
            }))
        except Exception as exc:  # noqa: BLE001 - cache is best-effort
            log.warning("Cache write failed for %s: %s", symbol, exc)
