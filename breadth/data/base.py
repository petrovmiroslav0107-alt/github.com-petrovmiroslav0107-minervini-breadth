"""Data-provider interface.

Screening logic depends ONLY on this interface. To move from yfinance to
EODHD/Polygon, implement these three methods and change `provider:` in
config.yaml — nothing in breadth/criteria.py or breadth/screen.py changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class FetchResult:
    """OHLCV histories plus per-run data-quality bookkeeping."""
    # symbol -> DataFrame indexed by DatetimeIndex with columns
    # Open, High, Low, Close, Volume (Close = split/dividend-adjusted close)
    histories: dict[str, pd.DataFrame] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)       # download errored
    empty: list[str] = field(default_factory=list)        # no rows returned (delisted?)
    stale: list[str] = field(default_factory=list)        # last bar older than stale_days


class DataProvider(ABC):
    @abstractmethod
    def fetch_history(self, symbols: list[str], start: date, end: date) -> FetchResult:
        """Daily OHLCV for `symbols` between start and end (inclusive)."""

    @abstractmethod
    def get_fx_to_eur(self, currencies: list[str]) -> dict[str, float]:
        """Latest EUR-per-unit rate for each ISO currency code."""
