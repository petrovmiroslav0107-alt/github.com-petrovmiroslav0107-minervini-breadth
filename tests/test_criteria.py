"""Hand-built fixtures for each of the 8 criteria."""
import numpy as np
import pandas as pd
import pytest

from breadth.criteria import rs_percentile, trend_template_frame

PARAMS = {
    "sma_fast": 50, "sma_mid": 150, "sma_slow": 200,
    "sma_slow_trend_days": 21, "week52_days": 252,
    "low_52w_multiple": 1.30, "high_52w_multiple": 0.75,
    "rs_percentile_min": 70,
    "rs_lookbacks": {"m6": 126, "m9": 189, "m12": 252},
    "rs_weights": {"m6": 2.0, "m9": 1.0, "m12": 1.0},
}


def make_series(values) -> pd.Series:
    idx = pd.bdate_range("2024-01-01", periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def last_row(values):
    return trend_template_frame(make_series(values), PARAMS).iloc[-1]


def linear(start, stop, n=400):
    return np.linspace(start, stop, n)


# ------------------------------------------------------------------ c1-c7 -- #
def test_steady_uptrend_passes_criteria_1_to_7():
    row = last_row(linear(10, 20))
    assert row["has_history"]
    for c in ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]:
        assert row[c], f"{c} should pass in a steady uptrend"


def test_steady_downtrend_fails_every_trend_criterion():
    row = last_row(linear(20, 10))
    assert row["has_history"]
    for c in ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]:
        assert not row[c], f"{c} should fail in a steady downtrend"


def test_c1_c5_fail_when_price_crashes_below_moving_averages():
    values = linear(100, 120)
    values[-1] = 60  # crash on the last day, MAs still reflect the uptrend
    row = last_row(values)
    assert not row["c1"]  # close < SMA150/SMA200
    assert not row["c5"]  # close < SMA50
    assert row["c2"] and row["c3"] and row["c4"]  # MA stack itself intact


def test_c2_c3_fail_on_flat_series():
    # constant price: SMA150 == SMA200 and SMA200 == its value 21 days ago;
    # both criteria are strict inequalities and must fail
    row = last_row(np.full(400, 100.0))
    assert not row["c2"]
    assert not row["c3"]
    assert row["c7"]  # close == 52w high, trivially within 25%


def test_c4_fails_when_short_ma_below_long_ma():
    # long uptrend that rolls over for the final ~60 days: SMA50 dips below
    # SMA150 while the 150/200 relationship still reflects the old uptrend
    values = np.concatenate([linear(100, 200, 340), linear(200, 120, 60)])
    row = last_row(values)
    assert not row["c4"]


def test_c5_is_the_only_failure_on_a_pullback_below_sma50():
    values = linear(100, 200, 400)
    values[-1] = 185.0  # below SMA50 (~194) but above SMA150 (~181)
    row = last_row(values)
    assert not row["c5"]
    for c in ["c1", "c2", "c3", "c4", "c6", "c7"]:
        assert row[c], f"{c} should still pass on a shallow pullback"


def test_c6_fails_when_less_than_30pct_above_52w_low():
    # shallow uptrend: +10% over the whole window can't be 30% off the low
    row = last_row(linear(100, 110))
    assert not row["c6"]
    for c in ["c1", "c2", "c3", "c4", "c5", "c7"]:
        assert row[c]


def test_c6_boundary_is_inclusive():
    # close exactly 1.30x the 52-week low passes (criterion is >=)
    values = np.full(400, 130.0)
    values[-200] = 100.0  # a single dip inside the 52w window defines the low
    row = last_row(values)
    assert row["c6"]


def test_c7_fails_more_than_25pct_off_high():
    # run to 200, then base at 145 (27.5% off the high)
    values = np.concatenate([linear(100, 200, 380), np.full(20, 145.0)])
    row = last_row(values)
    assert not row["c7"]


def test_c7_boundary_is_inclusive():
    values = np.full(400, 75.0)
    values[-100] = 100.0  # 52w high 100; close 75 == 0.75 * 100 passes
    row = last_row(values)
    assert row["c7"]


def test_has_history_false_without_a_full_year_of_data():
    row = last_row(linear(10, 20, 200))
    assert not row["has_history"]


# ------------------------------------------------------------------ RS ----- #
def test_rs_raw_weights_6_9_12_month_returns():
    # 400 flat days then engineered: use a series with known returns
    n = 400
    values = np.full(n, 100.0)
    values[-1] = 120.0  # +20% vs every lookback point
    row = last_row(values)
    r = 0.20
    assert row["rs_raw"] == pytest.approx(2 * r + r + r)


def test_rs_percentile_ranks_within_eligible_universe_only():
    dates = pd.bdate_range("2025-01-01", periods=2)
    syms = [f"S{i}" for i in range(10)]
    rs = pd.DataFrame([np.arange(10.0)] * 2, index=dates, columns=syms)
    eligible = pd.DataFrame(True, index=dates, columns=syms)
    # best stock ineligible -> must be excluded from the ranking pool
    eligible["S9"] = False

    pct = rs_percentile(rs, eligible)
    assert np.isnan(pct.loc[dates[0], "S9"])
    assert pct.loc[dates[0], "S8"] == pytest.approx(100.0)  # best of the 9
    assert pct.loc[dates[0], "S0"] == pytest.approx(100.0 / 9)
    n_passing = (pct.loc[dates[0]] >= 70).sum()
    assert n_passing == 3  # ranks 7,8,9 of 9 -> 77.8, 88.9, 100


def test_rs_percentile_regions_are_independent():
    dates = pd.bdate_range("2025-01-01", periods=1)
    us = pd.DataFrame([[5.0, 1.0]], index=dates, columns=["A", "B"])
    eu = pd.DataFrame([[0.5, 0.1]], index=dates, columns=["X", "Y"])
    all_true = lambda df: pd.DataFrame(True, index=df.index, columns=df.columns)
    # a weak absolute score still ranks top of its own region
    assert rs_percentile(eu, all_true(eu)).loc[dates[0], "X"] == pytest.approx(100.0)
    assert rs_percentile(us, all_true(us)).loc[dates[0], "B"] == pytest.approx(50.0)
