"""yfinance implementation of DataProvider.

Downloads in chunks with retries; failed chunks are retried per-symbol so one
bad ticker can't sink 149 good ones. All prices are split/dividend-adjusted
(auto_adjust=True) — required so SMAs and returns are comparable across time.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from .base import DataProvider, FetchResult
from .cache import OHLCVCache
from ..config import resolve

log = logging.getLogger(__name__)

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


class YFinanceProvider(DataProvider):
    def __init__(self, cfg: dict):
        d = cfg["data"]
        self.chunk_size = int(d["chunk_size"])
        self.max_retries = int(d["max_retries"])
        self.retry_sleep = float(d["retry_sleep_seconds"])
        self.stale_days = int(d["stale_days"])
        self.cache = OHLCVCache(resolve(cfg, d["cache_dir"]))

    # ------------------------------------------------------------------ #
    def fetch_history(self, symbols: list[str], start: date, end: date) -> FetchResult:
        result = FetchResult()
        to_fetch: list[str] = []
        for sym in symbols:
            cached = self.cache.get(sym, start, end)
            if cached is not None and not cached.empty:
                result.histories[sym] = cached
            else:
                to_fetch.append(sym)
        log.info("history: %d cached, %d to download", len(result.histories), len(to_fetch))

        for i in range(0, len(to_fetch), self.chunk_size):
            chunk = to_fetch[i:i + self.chunk_size]
            frames = self._download_chunk(chunk, start, end)
            for sym in chunk:
                df = frames.get(sym)
                if df is None:
                    result.failed.append(sym)
                elif df.empty:
                    result.empty.append(sym)
                else:
                    self.cache.put(sym, df, start, end)
                    result.histories[sym] = df
            if i + self.chunk_size < len(to_fetch):
                time.sleep(1.0)  # be polite between chunks

        stale_cutoff = pd.Timestamp(end) - pd.Timedelta(days=self.stale_days)
        for sym, df in result.histories.items():
            if df.index[-1] < stale_cutoff:
                result.stale.append(sym)
        return result

    def _download_chunk(self, chunk: list[str], start: date, end: date
                        ) -> dict[str, pd.DataFrame | None]:
        """Returns symbol -> DataFrame (may be empty), or None on failure."""
        end_excl = end + timedelta(days=1)  # yfinance end is exclusive
        raw = None
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = yf.download(chunk, start=start, end=end_excl, interval="1d",
                                  auto_adjust=True, group_by="ticker",
                                  threads=True, progress=False)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("chunk download attempt %d/%d failed: %s",
                            attempt, self.max_retries, exc)
                time.sleep(self.retry_sleep * attempt)
        if raw is None:
            # whole chunk failed repeatedly -> try symbols one by one
            return {sym: self._download_single(sym, start, end_excl) for sym in chunk}

        out: dict[str, pd.DataFrame | None] = {}
        for sym in chunk:
            try:
                df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df.reindex(columns=OHLCV_COLS).dropna(subset=["Close"])
                out[sym] = df
            except (KeyError, TypeError):
                out[sym] = self._download_single(sym, start, end_excl)
        return out

    def _download_single(self, sym: str, start: date, end_excl: date
                         ) -> pd.DataFrame | None:
        try:
            df = yf.download(sym, start=start, end=end_excl, interval="1d",
                             auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df.reindex(columns=OHLCV_COLS).dropna(subset=["Close"])
        except Exception as exc:  # noqa: BLE001
            log.warning("single download failed for %s: %s", sym, exc)
            return None

    # ------------------------------------------------------------------ #
    def get_fx_to_eur(self, currencies: list[str]) -> dict[str, float]:
        rates: dict[str, float] = {}
        pairs = {c: f"{c}EUR=X" for c in currencies if c != "EUR"}
        if "EUR" in currencies:
            rates["EUR"] = 1.0
        if not pairs:
            return rates
        raw = yf.download(list(pairs.values()), period="5d", interval="1d",
                          group_by="ticker", progress=False, threads=True)
        for cur, pair in pairs.items():
            try:
                series = (raw[pair]["Close"] if isinstance(raw.columns, pd.MultiIndex)
                          else raw["Close"])
                last = series.dropna().iloc[-1]
                rates[cur] = float(last)
            except Exception:  # noqa: BLE001 - caller falls back to static rates
                pass
        return rates
