"""CSV writers. Appends are idempotent: re-running a day replaces that
day's rows instead of duplicating them."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .config import resolve
from .screen import ScreenResult

log = logging.getLogger(__name__)


def _upsert_csv(path: Path, new_rows: pd.DataFrame, key_cols: list[str]) -> None:
    if new_rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_csv(path, dtype=str)
        new_keys = set(map(tuple, new_rows[key_cols].astype(str).values))
        keep = ~existing[key_cols].astype(str).apply(tuple, axis=1).isin(new_keys)
        combined = pd.concat([existing[keep], new_rows.astype(str)])
    else:
        combined = new_rows.astype(str)
    combined = combined.sort_values(key_cols)
    combined.to_csv(path, index=False)


def write_outputs(result: ScreenResult, cfg: dict) -> None:
    out = cfg["output"]
    out_dir = resolve(cfg, out["dir"])

    _upsert_csv(out_dir / out["breadth_log"], result.breadth, ["date", "region"])

    if result.region == "europe" and not result.by_exchange.empty:
        _upsert_csv(out_dir / out["europe_exchange_log"], result.by_exchange,
                    ["date", "exchange"])

    snap_dir = out_dir / out["snapshot_dir"]
    snap_dir.mkdir(parents=True, exist_ok=True)
    latest = result.snapshot["date"].iloc[0] if not result.snapshot.empty else "empty"
    snap_path = snap_dir / f"{latest}_{result.region}.csv"
    result.snapshot.to_csv(snap_path, index=False)

    qual_dir = out_dir / out["quality_dir"]
    qual_dir.mkdir(parents=True, exist_ok=True)
    qual_path = qual_dir / "data_quality_log.csv"
    _upsert_csv(qual_path, pd.DataFrame([result.quality]), ["date", "region"])

    log.info("%s: wrote breadth log, snapshot (%s), quality log",
             result.region, snap_path.name)
