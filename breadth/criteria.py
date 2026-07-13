"""The 8 Minervini Trend Template criteria, computed as boolean time series.

Everything here is per-stock and price-relative, so it is currency-agnostic.
Criteria 1-7 are pure functions of one stock's close series. Criterion 8
(RS percentile) is cross-sectional and therefore lives in screen.py — this
module only produces the raw RS score.

52-week high/low use closing prices (rolling 252 trading days), the most
common implementation; switch to High/Low columns in trend_template_frame
if you prefer intraday extremes.
"""
from __future__ import annotations

import pandas as pd

CRITERIA_LABELS = {
    "c1": "close>SMA150&SMA200",
    "c2": "SMA150>SMA200",
    "c3": "SMA200 trending up",
    "c4": "SMA50>SMA150&SMA200",
    "c5": "close>SMA50",
    "c6": "close>=1.30x 52w low",
    "c7": "close>=0.75x 52w high",
    "c8": "RS percentile>=70",
}


def trend_template_frame(close: pd.Series, p: dict) -> pd.DataFrame:
    """Compute criteria 1-7, the raw RS score, and data-sufficiency flags
    for every date in `close`'s index.

    Rolling windows use min_periods == window, so all values are NaN (and
    all criteria False) until enough history exists; `has_history` marks
    dates where every input is defined.
    """
    sma_fast = close.rolling(p["sma_fast"]).mean()
    sma_mid = close.rolling(p["sma_mid"]).mean()
    sma_slow = close.rolling(p["sma_slow"]).mean()
    sma_slow_prev = sma_slow.shift(p["sma_slow_trend_days"])

    w52 = int(p["week52_days"])
    low_52w = close.rolling(w52).min()
    high_52w = close.rolling(w52).max()

    lb, wt = p["rs_lookbacks"], p["rs_weights"]
    r6 = close / close.shift(int(lb["m6"])) - 1.0
    r9 = close / close.shift(int(lb["m9"])) - 1.0
    r12 = close / close.shift(int(lb["m12"])) - 1.0
    rs_raw = wt["m6"] * r6 + wt["m9"] * r9 + wt["m12"] * r12

    out = pd.DataFrame(index=close.index)
    out["c1"] = (close > sma_mid) & (close > sma_slow)
    out["c2"] = sma_mid > sma_slow
    out["c3"] = sma_slow > sma_slow_prev
    out["c4"] = (sma_fast > sma_mid) & (sma_fast > sma_slow)
    out["c5"] = close > sma_fast
    out["c6"] = close >= p["low_52w_multiple"] * low_52w
    out["c7"] = close >= p["high_52w_multiple"] * high_52w
    out["rs_raw"] = rs_raw
    out["has_history"] = (sma_slow_prev.notna() & low_52w.notna()
                          & rs_raw.notna())
    return out


def rs_percentile(rs_raw_panel: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    """Criterion 8's ranking step: percentile (0-100] of the raw RS score,
    per date, WITHIN the eligible universe passed in. Regions must be ranked
    separately — the caller enforces that by passing one region's panel.

    rs_raw_panel / eligible: DataFrames indexed by date, one column per symbol.
    """
    masked = rs_raw_panel.where(eligible)
    return masked.rank(axis=1, pct=True, method="average") * 100.0
