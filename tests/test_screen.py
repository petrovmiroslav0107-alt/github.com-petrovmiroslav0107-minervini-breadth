"""End-to-end screen test with a fake data provider (no network)."""
from datetime import date

import numpy as np
import pandas as pd

from breadth.data.base import DataProvider, FetchResult
from breadth.screen import run_screen

END = date(2025, 6, 30)
N_DAYS = 420

CFG = {
    "criteria": {
        "sma_fast": 50, "sma_mid": 150, "sma_slow": 200,
        "sma_slow_trend_days": 21, "week52_days": 252,
        "low_52w_multiple": 1.30, "high_52w_multiple": 0.75,
        "rs_percentile_min": 70,
        "rs_lookbacks": {"m6": 126, "m9": 189, "m12": 252},
        "rs_weights": {"m6": 2.0, "m9": 1.0, "m12": 1.0},
    },
    "liquidity": {
        "adv_window": 50,
        "us": {"min_price": 5.0, "min_adv": 5_000_000, "currency": "USD"},
    },
    "data": {"lookback_calendar_days": 480, "ffill_limit": 5},
}


def _history(closes: np.ndarray, volume: float = 1_000_000.0) -> pd.DataFrame:
    idx = pd.bdate_range(end=END, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": volume}, index=idx)


class FakeProvider(DataProvider):
    def __init__(self, histories):
        self._h = histories

    def fetch_history(self, symbols, start, end):
        return FetchResult(histories={s: self._h[s] for s in symbols
                                      if s in self._h})

    def get_fx_to_eur(self, currencies):
        return {c: 1.0 for c in currencies}


def build_fixture():
    up = np.linspace(100, 200, N_DAYS)
    down = np.linspace(200, 100, N_DAYS)
    cheap = np.linspace(2, 3, N_DAYS)          # below the $5 price floor
    histories = {
        "UP1": _history(up),
        "UP2": _history(up),                    # identical twin: tied RS rank
        "DOWN": _history(down),
        "CHEAP": _history(cheap),
    }
    universe = pd.DataFrame({
        "symbol": ["UP1", "UP2", "DOWN", "CHEAP"],
        "exchange": ["NASDAQ", "NASDAQ", "NYSE", "NYSE"],
        "currency": ["USD"] * 4,
    })
    return universe, FakeProvider(histories)


def test_breadth_counts_and_liquidity_floor():
    universe, provider = build_fixture()
    result = run_screen("us", universe, provider, CFG, eval_days=3, end=END)

    latest = result.breadth.iloc[-1]
    # CHEAP is excluded by the price floor -> universe of 3
    assert latest["universe_size"] == 3
    # both uptrends tie at RS percentile (2+3)/2/3 = 83.3 >= 70 -> both pass
    assert latest["pass_count"] == 2
    assert latest["pass_percentage"] == 66.67
    assert len(result.breadth) == 3  # eval_days rows


def test_snapshot_lists_failed_criteria():
    universe, provider = build_fixture()
    result = run_screen("us", universe, provider, CFG, eval_days=1, end=END)

    snap = result.snapshot.set_index("symbol")
    assert "CHEAP" not in snap.index          # ineligible, not in snapshot
    assert bool(snap.loc["UP1", "passed"]) is True
    assert snap.loc["UP1", "failed_criteria"] == ""
    down_fails = set(snap.loc["DOWN", "failed_criteria"].split("|"))
    assert {"c1", "c2", "c4", "c5", "c8"} <= down_fails


def test_backfill_produces_a_row_per_trading_day():
    universe, provider = build_fixture()
    result = run_screen("us", universe, provider, CFG, eval_days=20, end=END)
    assert len(result.breadth) == 20
    assert result.breadth["date"].is_unique
    # breadth is stable across the window for these synthetic series
    assert (result.breadth["pass_count"] == 2).all()
