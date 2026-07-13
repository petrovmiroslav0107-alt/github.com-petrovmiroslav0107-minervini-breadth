"""Refresh the European seed universe from index constituent lists (Wikipedia).

Usage:
    python -m breadth.universe.refresh_europe [--write]

Without --write it prints a diff (additions / disappearances) against the
current seed and exits — review before committing. With --write it appends
NEW symbols to the seed CSV; it never deletes rows automatically (a scraper
hiccup must not silently shrink the universe). Remove delisted names by hand
when flagged.

Scraping Wikipedia is the fragile part of this design — it is deliberately
quarantined here, run manually (or via the refresh-universe workflow) a few
times a year, never in the daily breadth job.
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "universe" / "europe_seed.csv"

# index page -> (yahoo suffix, exchange label, currency)
# Column-name candidates for the ticker column differ per page; we search for them.
SOURCES = [
    ("https://en.wikipedia.org/wiki/FTSE_100_Index", ".L", "LSE", "GBX"),
    ("https://en.wikipedia.org/wiki/FTSE_250_Index", ".L", "LSE", "GBX"),
    ("https://en.wikipedia.org/wiki/DAX", ".DE", "XETRA", "EUR"),
    ("https://en.wikipedia.org/wiki/MDAX", ".DE", "XETRA", "EUR"),
    ("https://en.wikipedia.org/wiki/SDAX", ".DE", "XETRA", "EUR"),
    ("https://en.wikipedia.org/wiki/CAC_40", ".PA", "EURONEXT_PARIS", "EUR"),
    ("https://en.wikipedia.org/wiki/CAC_Next_20", ".PA", "EURONEXT_PARIS", "EUR"),
    ("https://en.wikipedia.org/wiki/AEX_index", ".AS", "EURONEXT_AMSTERDAM", "EUR"),
    ("https://en.wikipedia.org/wiki/AMX_index", ".AS", "EURONEXT_AMSTERDAM", "EUR"),
    ("https://en.wikipedia.org/wiki/BEL_20", ".BR", "EURONEXT_BRUSSELS", "EUR"),
    ("https://en.wikipedia.org/wiki/PSI-20", ".LS", "EURONEXT_LISBON", "EUR"),
    ("https://en.wikipedia.org/wiki/FTSE_MIB", ".MI", "BORSA_ITALIANA", "EUR"),
    ("https://en.wikipedia.org/wiki/IBEX_35", ".MC", "BME_MADRID", "EUR"),
    ("https://en.wikipedia.org/wiki/OMX_Stockholm_30", ".ST", "OMX_STOCKHOLM", "SEK"),
    ("https://en.wikipedia.org/wiki/OMX_Copenhagen_25", ".CO", "OMX_COPENHAGEN", "DKK"),
    ("https://en.wikipedia.org/wiki/OBX_Index", ".OL", "OSLO", "NOK"),
    ("https://en.wikipedia.org/wiki/OMX_Helsinki_25", ".HE", "OMX_HELSINKI", "EUR"),
    ("https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW", "SIX", "CHF"),
]

TICKER_COLUMNS = ["ticker", "ticker symbol", "symbol", "epic", "code"]
NAME_COLUMNS = ["company", "name", "constituent"]
# obvious non-common-stock names to skip
EXCLUDE_NAME = re.compile(r"investment trust|\bETF\b|\bREIT\b$", re.IGNORECASE)


def _clean_symbol(raw: str, suffix: str) -> str | None:
    s = str(raw).strip().upper()
    if not s or s == "NAN" or len(s) > 12:
        return None
    # strip an exchange prefix like "BIT:" or existing suffix
    s = s.split(":")[-1].strip()
    for suf in (".L", ".DE", ".PA", ".AS", ".BR", ".LS", ".MI", ".MC",
                ".ST", ".CO", ".OL", ".HE", ".SW"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    # Yahoo notation: class separators and LSE trailing dots use "-" / nothing
    if suffix == ".L":
        s = s.rstrip(".").replace(".", "-")   # BT.A -> BT-A
    else:
        s = s.replace(" ", "-").replace(".", "-")
    return s + suffix if s else None


def scrape_source(url: str, suffix: str, exchange: str, currency: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=60, headers={"User-Agent": "breadth-logger/1.0"})
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    for table in tables:
        cols = {str(c).strip().lower(): c for c in table.columns}
        tick_col = next((cols[c] for c in TICKER_COLUMNS if c in cols), None)
        name_col = next((cols[c] for c in NAME_COLUMNS if c in cols), None)
        if tick_col is None or len(table) < 10:
            continue
        rows = []
        for _, r in table.iterrows():
            name = str(r[name_col]).strip() if name_col is not None else ""
            if EXCLUDE_NAME.search(name):
                continue
            sym = _clean_symbol(r[tick_col], suffix)
            if sym:
                rows.append({"symbol": sym, "name": name,
                             "exchange": exchange, "currency": currency})
        if rows:
            return pd.DataFrame(rows)
    raise ValueError(f"No constituent table recognised at {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="append new symbols to the seed CSV")
    args = parser.parse_args()

    seed = pd.read_csv(SEED_PATH, comment="#")
    scraped_frames, failures = [], []
    for url, suffix, exchange, currency in SOURCES:
        try:
            df = scrape_source(url, suffix, exchange, currency)
            log.info("%-60s %3d constituents", url.split("/wiki/")[-1], len(df))
            scraped_frames.append(df)
        except Exception as exc:  # noqa: BLE001 - one broken page must not stop the rest
            failures.append(url)
            log.error("FAILED %s: %s", url, exc)

    scraped = (pd.concat(scraped_frames).drop_duplicates(subset="symbol")
               if scraped_frames else pd.DataFrame(columns=seed.columns))
    new = scraped[~scraped["symbol"].isin(seed["symbol"])]
    scraped_exchanges = set(scraped["exchange"])
    gone = seed[seed["exchange"].isin(scraped_exchanges)
                & ~seed["symbol"].isin(scraped["symbol"])]

    print(f"\nSeed size: {len(seed)}   scraped: {len(scraped)}   "
          f"new: {len(new)}   in-seed-but-not-scraped: {len(gone)}   "
          f"failed sources: {len(failures)}")
    if len(new):
        print("\nNEW symbols (would be appended):")
        print(new.to_string(index=False))
    if len(gone):
        print("\nIn seed but not in any scraped index (verify & remove by hand "
              "if delisted; may simply be a non-index constituent you added):")
        print(gone[["symbol", "name"]].to_string(index=False))

    if args.write and len(new):
        combined = pd.concat([seed, new]).sort_values(["exchange", "symbol"])
        header = ("# Index-seeded European universe, one line per company "
                  "(primary listing).\n# Refresh with `python -m "
                  "breadth.universe.refresh_europe --write` and review the diff.\n")
        with open(SEED_PATH, "w", encoding="utf-8", newline="") as f:
            f.write(header)
            combined.to_csv(f, index=False)
        print(f"\nWrote {len(combined)} rows to {SEED_PATH}")
    return 1 if failures and not scraped_frames else 0


if __name__ == "__main__":
    sys.exit(main())
