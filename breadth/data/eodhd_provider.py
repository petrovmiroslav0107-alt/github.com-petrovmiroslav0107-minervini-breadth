"""EODHD implementation of DataProvider (upgrade path, ~EUR 20/month).

Functional but UNTESTED against a live key — verify with a handful of tickers
before relying on it. Symbol mapping: EODHD uses SYMBOL.EXCHANGE codes
(e.g. AZN.LSE, SAP.XETRA) rather than Yahoo suffixes; add an `eodhd_symbol`
column to the universe seed file when you switch, or extend SUFFIX_MAP below.

Enable with:  provider: eodhd  in config.yaml and EODHD_API_KEY in the env.
"""
from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd
import requests

from .base import DataProvider, FetchResult
from .cache import OHLCVCache
from ..config import resolve

log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"

# Yahoo suffix -> EODHD exchange code
SUFFIX_MAP = {
    ".L": "LSE", ".DE": "XETRA", ".PA": "PA", ".AS": "AS", ".BR": "BR",
    ".LS": "LS", ".MI": "MI", ".MC": "MC", ".ST": "ST", ".CO": "CO",
    ".OL": "OL", ".HE": "HE", ".SW": "SW",
}


def yahoo_to_eodhd(symbol: str) -> str:
    for suffix, exch in SUFFIX_MAP.items():
        if symbol.endswith(suffix):
            return symbol[:-len(suffix)] + "." + exch
    return symbol + ".US"  # US tickers carry no Yahoo suffix


class EODHDProvider(DataProvider):
    def __init__(self, cfg: dict):
        key_env = cfg.get("eodhd_api_key_env", "EODHD_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        if not self.api_key:
            raise RuntimeError(f"EODHD provider selected but {key_env} is not set")
        d = cfg["data"]
        self.stale_days = int(d["stale_days"])
        self.cache = OHLCVCache(resolve(cfg, d["cache_dir"]))

    def fetch_history(self, symbols: list[str], start: date, end: date) -> FetchResult:
        result = FetchResult()
        for sym in symbols:
            cached = self.cache.get(sym, start, end)
            if cached is not None and not cached.empty:
                result.histories[sym] = cached
                continue
            try:
                r = requests.get(
                    f"{BASE_URL}/eod/{yahoo_to_eodhd(sym)}",
                    params={"api_token": self.api_key, "fmt": "json",
                            "from": start.isoformat(), "to": end.isoformat()},
                    timeout=30)
                r.raise_for_status()
                rows = r.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("EODHD download failed for %s: %s", sym, exc)
                result.failed.append(sym)
                continue
            if not rows:
                result.empty.append(sym)
                continue
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            # use adjusted close; scale OHLC by the same factor for consistency
            factor = df["adjusted_close"] / df["close"]
            out = pd.DataFrame({
                "Open": df["open"] * factor, "High": df["high"] * factor,
                "Low": df["low"] * factor, "Close": df["adjusted_close"],
                "Volume": df["volume"],
            })
            self.cache.put(sym, out, start, end)
            result.histories[sym] = out

        stale_cutoff = pd.Timestamp(end) - pd.Timedelta(days=self.stale_days)
        result.stale = [s for s, df in result.histories.items()
                        if df.index[-1] < stale_cutoff]
        return result

    def get_fx_to_eur(self, currencies: list[str]) -> dict[str, float]:
        rates = {"EUR": 1.0} if "EUR" in currencies else {}
        for cur in currencies:
            if cur == "EUR":
                continue
            try:
                r = requests.get(f"{BASE_URL}/real-time/{cur}EUR.FOREX",
                                 params={"api_token": self.api_key, "fmt": "json"},
                                 timeout=15)
                r.raise_for_status()
                rates[cur] = float(r.json()["close"])
            except Exception as exc:  # noqa: BLE001
                log.warning("EODHD FX failed for %s: %s", cur, exc)
        return rates
