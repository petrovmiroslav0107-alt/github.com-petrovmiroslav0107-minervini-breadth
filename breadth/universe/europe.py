"""European universe: loaded from a versioned seed CSV.

Design decision (documented in README): instead of scraping 11 exchanges'
official instrument files on every run, the universe is a curated CSV of
index-seeded constituents committed to the repo. Rationale:

* the EUR 3M/day traded-value floor means broad-index constituents already
  cover ~90-95%+ of stocks that would qualify;
* a stable universe produces a cleaner breadth time series than one whose
  composition jumps whenever a scraper breaks;
* refreshing is an explicit, reviewable event (run refresh_europe.py, eyeball
  the diff, commit) instead of a silent runtime dependency.

Seed columns: symbol (Yahoo, with suffix), name, exchange, currency.
Currency GBX marks LSE pence quotes. One line per company — primary listing
only, so a company never counts twice in the breadth numbers.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..config import resolve

log = logging.getLogger(__name__)

REQUIRED_COLS = {"symbol", "exchange", "currency"}

# Yahoo suffix -> default currency, used to sanity-check the seed file
SUFFIX_CURRENCY = {
    ".L": "GBX", ".DE": "EUR", ".PA": "EUR", ".AS": "EUR", ".BR": "EUR",
    ".LS": "EUR", ".MI": "EUR", ".MC": "EUR", ".ST": "SEK", ".CO": "DKK",
    ".OL": "NOK", ".HE": "EUR", ".SW": "CHF",
}


def load_europe_universe(cfg: dict) -> pd.DataFrame:
    """Returns DataFrame with columns: symbol, exchange, currency (+name)."""
    path = resolve(cfg, cfg["universe"]["europe"]["seed_file"])
    df = pd.read_csv(path, comment="#")
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Europe seed file {path} missing columns: {missing}")

    df["symbol"] = df["symbol"].str.strip()
    before = len(df)
    df = df.drop_duplicates(subset="symbol").reset_index(drop=True)
    if len(df) != before:
        log.warning("Europe seed contained %d duplicate symbols", before - len(df))

    # sanity-check currencies against suffix defaults
    for _, row in df.iterrows():
        for suffix, cur in SUFFIX_CURRENCY.items():
            if row["symbol"].endswith(suffix) and row["currency"] != cur:
                log.warning("Seed: %s has currency %s (suffix default %s) — "
                            "make sure this is intentional",
                            row["symbol"], row["currency"], cur)
                break
    log.info("Europe universe: %d symbols (pre-liquidity-filter)", len(df))
    return df[["symbol", "exchange", "currency"]]
