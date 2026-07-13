"""US universe: common stocks on NYSE + NASDAQ, from NASDAQ's public
symbol directory (updated nightly, free, no key):

  https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt   (NASDAQ)
  https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt    (NYSE et al.)

Filters applied here (structural): ETF flag, test issues, NextShares,
preferreds/warrants/units/rights. The price and dollar-volume floors are
applied later from actual OHLCV data in screen.py.
"""
from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests

log = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# NYSE-family suffix conventions in the ACT/CQS symbol (otherlisted.txt):
# $ = preferred, .W/.WS = warrant, .U = unit, .R = rights, class shares use "."
_NON_COMMON = re.compile(r"[\$]|\.(W|WS|U|R|RT|PR[A-Z]?)$")


def _fetch(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    text = r.text
    # last line is a "File Creation Time" footer
    body = "\n".join(line for line in text.splitlines()
                     if not line.startswith("File Creation Time"))
    return pd.read_csv(io.StringIO(body), sep="|")


def _to_yahoo(symbol: str) -> str:
    # Yahoo uses "-" where NASDAQ/CQS use "." for share classes (BRK.B -> BRK-B)
    return symbol.strip().replace(".", "-")


def build_us_universe(cfg: dict) -> pd.DataFrame:
    """Returns DataFrame with columns: symbol, exchange, currency."""
    rows: list[dict] = []

    nasdaq = _fetch(NASDAQ_LISTED_URL)
    nasdaq = nasdaq[(nasdaq["Test Issue"] == "N") & (nasdaq["ETF"] == "N")]
    if "NextShares" in nasdaq.columns:
        nasdaq = nasdaq[nasdaq["NextShares"] == "N"]
    for _, row in nasdaq.iterrows():
        sym = str(row["Symbol"]).strip()
        name = str(row.get("Security Name", ""))
        if _looks_non_common(sym, name):
            continue
        rows.append({"symbol": _to_yahoo(sym), "exchange": "NASDAQ", "currency": "USD"})

    wanted = set(cfg["universe"]["us"]["other_listed_exchanges"])
    other = _fetch(OTHER_LISTED_URL)
    other = other[(other["Test Issue"] == "N") & (other["ETF"] == "N")]
    other = other[other["Exchange"].isin(wanted)]
    for _, row in other.iterrows():
        sym = str(row["ACT Symbol"]).strip()
        name = str(row.get("Security Name", ""))
        if _NON_COMMON.search(sym) or _looks_non_common(sym, name):
            continue
        rows.append({"symbol": _to_yahoo(sym), "exchange": "NYSE", "currency": "USD"})

    df = pd.DataFrame(rows).drop_duplicates(subset="symbol").reset_index(drop=True)
    log.info("US universe: %d symbols (pre-liquidity-filter)", len(df))
    return df


_NAME_EXCLUDES = (
    "preferred", "warrant", " right", "rights", " unit", "units",
    "%", "notes", "due 20", "depositary", "etn", "trust preferred",
)


def _looks_non_common(symbol: str, name: str) -> bool:
    if len(symbol) > 5:  # 5th letter suffixes like W (warrant), U (unit) etc.
        return True
    lower = name.lower()
    return any(tag in lower for tag in _NAME_EXCLUDES)
