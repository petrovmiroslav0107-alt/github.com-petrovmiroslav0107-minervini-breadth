"""Screening engine: universe -> data -> criteria panels -> breadth counts.

Works identically for a daily run (eval_days=1) and a backfill (eval_days=N):
criteria are computed as time series, so a backfill is just "evaluate more
rows of the same panels". RS percentiles are recomputed cross-sectionally on
every date, always within one region.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from . import criteria as crit
from .fx import eur_rates, normalise_currency

log = logging.getLogger(__name__)

# panel columns produced per ticker
_TICKER_COLS = ["c1", "c2", "c3", "c4", "c5", "c6", "c7",
                "rs_raw", "has_history", "close", "adv"]


@dataclass
class ScreenResult:
    region: str
    breadth: pd.DataFrame          # date, region, universe_size, pass_count, pass_percentage
    by_exchange: pd.DataFrame      # date, exchange, universe_size, pass_count
    snapshot: pd.DataFrame         # per-symbol detail for the latest eval date
    quality: dict = field(default_factory=dict)


def required_start(end: date, cfg: dict, eval_days: int) -> date:
    """History window: ~15 months of lookback plus the backfill span."""
    lookback = int(cfg["data"]["lookback_calendar_days"])
    backfill_cal = int(eval_days * 1.5) + 10  # trading -> calendar days
    return end - timedelta(days=lookback + backfill_cal)


def run_screen(region: str, universe: pd.DataFrame, provider, cfg: dict,
               eval_days: int = 1, end: date | None = None) -> ScreenResult:
    """`universe`: DataFrame with columns symbol, exchange, currency."""
    end = end or date.today()
    start = required_start(end, cfg, eval_days)
    p = cfg["criteria"]
    liq = cfg["liquidity"][region]
    adv_window = int(cfg["liquidity"]["adv_window"])
    ffill_limit = int(cfg["data"]["ffill_limit"])

    symbols = universe["symbol"].tolist()
    fetch = provider.fetch_history(symbols, start, end)

    # --- per-ticker liquidity floors in local quote units ----------------- #
    # Floors are defined in the region currency (USD for US, EUR for Europe).
    # Convert once per ticker: floor_local = floor_region / (eur_per_unit ...)
    floor_factor = _floor_factors(universe, region, liq, provider)

    # --- per-ticker criteria frames --------------------------------------- #
    frames: dict[str, pd.DataFrame] = {}
    insufficient: list[str] = []
    min_bars = int(p["rs_lookbacks"]["m12"]) + 1
    for sym, hist in fetch.histories.items():
        if len(hist) < min_bars:
            insufficient.append(sym)
            continue
        close = hist["Close"]
        f = crit.trend_template_frame(close, p)
        f["close"] = close
        f["adv"] = (close * hist["Volume"]).rolling(adv_window).mean()
        frames[sym] = f[_TICKER_COLS]

    if not frames:
        raise RuntimeError(f"{region}: no usable histories — aborting run")

    # --- assemble panels (date x symbol), bridge local holidays ----------- #
    panels = {col: pd.DataFrame({s: f[col] for s, f in frames.items()})
              for col in _TICKER_COLS}
    bool_cols = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "has_history"]
    for col in _TICKER_COLS:
        panels[col] = panels[col].ffill(limit=ffill_limit)
        if col in bool_cols:
            panels[col] = panels[col].fillna(False).astype(bool)

    # --- eligibility: enough history + price & traded-value floors -------- #
    factors = pd.Series({s: floor_factor[s] for s in panels["close"].columns})
    price_ok = panels["close"].gt(float(liq["min_price"]) * factors, axis=1)
    adv_ok = panels["adv"].gt(float(liq["min_adv"]) * factors, axis=1)
    eligible = panels["has_history"] & price_ok & adv_ok

    # --- criterion 8: RS percentile within the region's eligible set ------ #
    rs_pct = crit.rs_percentile(panels["rs_raw"], eligible)
    c8 = (rs_pct >= float(p["rs_percentile_min"])).fillna(False)

    pass_all = eligible.copy()
    for c in ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]:
        pass_all &= panels[c]
    pass_all &= c8

    # --- aggregate --------------------------------------------------------- #
    uni_size = eligible.sum(axis=1)
    eval_dates = uni_size[uni_size > 0].index[-eval_days:]
    breadth = pd.DataFrame({
        "date": [d.date().isoformat() for d in eval_dates],
        "region": region,
        "universe_size": uni_size.loc[eval_dates].astype(int).values,
        "pass_count": pass_all.loc[eval_dates].sum(axis=1).astype(int).values,
    })
    breadth["pass_percentage"] = (100.0 * breadth["pass_count"]
                                  / breadth["universe_size"]).round(2)

    by_exchange = _exchange_breakdown(eligible, pass_all, universe, eval_dates)
    snapshot = _snapshot(panels, eligible, rs_pct, c8, pass_all,
                         universe, eval_dates[-1])
    quality = {
        "region": region, "date": eval_dates[-1].date().isoformat(),
        "symbols_requested": len(symbols),
        "downloaded": len(fetch.histories),
        "failed_download": len(fetch.failed),
        "empty_history": len(fetch.empty),
        "stale_last_bar": len(fetch.stale),
        "insufficient_history": len(insufficient),
        "eligible_latest": int(uni_size.loc[eval_dates[-1]]),
        "failed_symbols": ";".join(sorted(fetch.failed)[:50]),
        "stale_symbols": ";".join(sorted(fetch.stale)[:50]),
    }
    log.info("%s: %s", region, {k: v for k, v in quality.items()
                                if not k.endswith("symbols")})
    return ScreenResult(region, breadth, by_exchange, snapshot, quality)


# --------------------------------------------------------------------------- #
def _floor_factors(universe: pd.DataFrame, region: str, liq: dict,
                   provider) -> dict[str, float]:
    """symbol -> multiplier converting region-currency floors into the
    ticker's local quote units (handles GBX pence and all FX)."""
    if region == "us":
        return {s: 1.0 for s in universe["symbol"]}

    iso_needed = set()
    parsed: dict[str, tuple[str, float]] = {}
    for _, row in universe.iterrows():
        iso, mult = normalise_currency(row["currency"])
        parsed[row["symbol"]] = (iso, mult)
        iso_needed.add(iso)
    rates = eur_rates(iso_needed, provider)  # EUR per 1 unit of ISO currency
    # floor_local = floor_eur / (eur_per_quote_unit); quote unit = mult of ISO
    return {sym: 1.0 / (rates[iso] * mult) for sym, (iso, mult) in parsed.items()}


def _exchange_breakdown(eligible: pd.DataFrame, pass_all: pd.DataFrame,
                        universe: pd.DataFrame, eval_dates) -> pd.DataFrame:
    exch = universe.set_index("symbol")["exchange"]
    rows = []
    for d in eval_dates:
        elig_row, pass_row = eligible.loc[d], pass_all.loc[d]
        for exchange, syms in exch.groupby(exch).groups.items():
            cols = [s for s in syms if s in elig_row.index]
            n_elig = int(elig_row[cols].sum())
            if n_elig == 0:
                continue
            rows.append({"date": d.date().isoformat(), "exchange": exchange,
                         "universe_size": n_elig,
                         "pass_count": int(pass_row[cols].sum())})
    return pd.DataFrame(rows)


def _snapshot(panels, eligible, rs_pct, c8, pass_all, universe,
              eval_date) -> pd.DataFrame:
    exch = universe.set_index("symbol")["exchange"]
    crit_cols = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    rows = []
    for sym in eligible.columns:
        if not eligible.loc[eval_date, sym]:
            continue
        fails = [c for c in crit_cols if not panels[c].loc[eval_date, sym]]
        if not c8.loc[eval_date, sym]:
            fails.append("c8")
        rows.append({
            "date": eval_date.date().isoformat(),
            "symbol": sym,
            "exchange": exch.get(sym, ""),
            "passed": pass_all.loc[eval_date, sym],
            "failed_criteria": "|".join(fails),
            "close": round(float(panels["close"].loc[eval_date, sym]), 4),
            "rs_percentile": round(float(rs_pct.loc[eval_date, sym]), 1),
        })
    df = pd.DataFrame(rows)
    return df.sort_values(["passed", "symbol"], ascending=[False, True])
