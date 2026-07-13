# Minervini Trend Template Breadth Logger

Daily market-breadth logger based on Mark Minervini's 8-point Trend Template,
tracking the **US** (NYSE + NASDAQ) and **Europe** (11 major exchanges) as
separate universes. A stock counts toward breadth only if it passes **all 8**
criteria; the log records what fraction of each region's liquid universe
qualifies each day.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest tests/            # 22 tests, no network needed

python -m breadth.run --region europe        # daily run, one region
python -m breadth.run --region us
python -m breadth.run --region both
python -m breadth.run --region both --backfill-days 252   # ~12-month backfill
```

For GitHub Actions: push this repo, and `.github/workflows/breadth.yml` runs
weekdays at **17:00 UTC (Europe)** and **22:30 UTC (US)**, committing updated
CSVs back to the repo. Manual runs (any region, any backfill depth) via the
*Run workflow* button. The default branch must allow the workflow to push
(Settings → Actions → General → Workflow permissions → read & write).

## The 8 criteria

A stock must pass **all** of these (computed on adjusted closes):

| # | Test | Config key |
|---|------|-----------|
| 1 | Close > 150-day SMA and Close > 200-day SMA | `sma_mid`, `sma_slow` |
| 2 | 150-day SMA > 200-day SMA | |
| 3 | 200-day SMA above its value 21 trading days ago | `sma_slow_trend_days` |
| 4 | 50-day SMA > 150-day SMA and > 200-day SMA | `sma_fast` |
| 5 | Close > 50-day SMA | |
| 6 | Close ≥ 1.30 × 52-week low | `low_52w_multiple` |
| 7 | Close ≥ 0.75 × 52-week high | `high_52w_multiple` |
| 8 | RS percentile ≥ 70 within its own region | `rs_percentile_min` |

**Criterion 8 (IBD-style RS):** raw score = 2 × (6-month return) + (9-month
return) + (12-month return), using 126/189/252 trading-day lookbacks, ranked
to a percentile **within each region separately** — the US universe is ranked
only against the US, Europe only against Europe. Never across regions.
Weights and lookbacks are configurable (`rs_weights`, `rs_lookbacks`).

52-week high/low use closing prices (rolling 252 trading days). All
thresholds live in `config.yaml` — change them there, never in code.

## Universes

**US** — built programmatically each run from NASDAQ's public symbol
directory (`nasdaqlisted.txt` + `otherlisted.txt`): common stocks on NYSE and
NASDAQ, ETFs/test issues/preferreds/warrants/units excluded, then filtered to
price > $5 and 50-day average dollar volume > $5M.

**Europe** — LSE, XETRA, Euronext (Paris/Amsterdam/Brussels/Lisbon), Borsa
Italiana, BME Madrid, Nasdaq OMX (Stockholm/Copenhagen/Helsinki), Oslo and
SIX. The universe is a **versioned seed CSV** (`data/universe/europe_seed.csv`)
of index-seeded constituents, one row per company (primary listing only, so
nothing double-counts). Liquidity floor: price > €5-equivalent and 50-day
average traded value > €3M, converted per ticker at current FX (with static
fallback rates so an FX outage can't kill a run).

Why a seed CSV instead of scraping 11 exchanges nightly: the €3M floor means
index constituents already cover ~90–95% of qualifying names, a stable
universe gives a cleaner breadth time series, and refreshing becomes an
explicit reviewed event instead of a silent runtime dependency. Refresh with:

```bash
python -m breadth.universe.refresh_europe          # dry run, prints the diff
python -m breadth.universe.refresh_europe --write  # append new symbols
```

or the manual `refresh-europe-universe` GitHub workflow (opens a PR).
Do this ~quarterly, after index reviews. The script never deletes rows —
remove delisted names by hand when the diff flags them.

## Outputs (`output/`)

- `breadth_log.csv` — one row per region per day:
  `date, region, universe_size, pass_count, pass_percentage`.
  `universe_size` is the count of stocks passing the liquidity floor with
  sufficient history that day (the denominator).
- `europe_by_exchange.csv` — per-exchange breakdown:
  `date, exchange, universe_size, pass_count` — shows whether European
  strength is broad or concentrated in one country.
- `snapshots/YYYY-MM-DD_<region>.csv` — every eligible stock that day:
  passed flag, which criteria it failed (`c1|c5|c8`…), close, RS percentile.
- `quality/data_quality_log.csv` — per run: symbols requested, downloaded,
  failed, empty (delisted?), stale, insufficient history.

Re-running a day replaces that day's rows (idempotent) — a manual rerun never
duplicates data.

## Swapping the data provider

Screening depends only on `breadth/data/base.py::DataProvider`
(`fetch_history` + `get_fx_to_eur`). yfinance is the default; an EODHD
implementation (`breadth/data/eodhd_provider.py`, ~€20/month, materially more
reliable for Europe) is included but untested against a live key. Switch via
`provider: eodhd` in `config.yaml` plus an `EODHD_API_KEY` env var / Actions
secret. A Polygon adapter would follow the same pattern.

## Known data-quality caveats (Europe especially)

- **LSE quotes in pence (GBX).** Criteria are unaffected (price-relative);
  the liquidity floor divides by 100 via the seed's `currency=GBX` column.
  A handful of LSE lines quote in USD/EUR — fix the `currency` cell for those
  if you add any.
- **yfinance volume on European venues is exchange-reported only** (no
  off-exchange/dark volume), so traded value understates true liquidity —
  the €3M floor is deliberately conservative.
- **Local holidays**: panels forward-fill up to 5 trading days
  (`data.ffill_limit`), so a stock keeps yesterday's status when its exchange
  is closed but others are open. Stocks with no bar for longer drop out of
  the denominator (this also naturally handles delistings).
- **Survivorship bias in backfill**: the backfill uses *today's* universe, so
  breadth 12 months ago is reconstructed from current constituents. Fine for
  regime context; not a research-grade historical series. (EODHD's delisted
  data is the fix if this ever matters.)
- **Adjusted-close traded value**: dollar volume uses adjusted closes, which
  slightly misstates pre-split traded value. Irrelevant for a floor filter.
- yfinance throttles occasionally; downloads are chunked with retries and
  failures are logged per run in the quality log rather than aborting.

## Changing thresholds

Everything is in `config.yaml`: liquidity floors per region, SMA windows,
52-week multiples, RS weights/lookbacks/threshold, chunk sizes, cache and
staleness settings. The unit tests pin the *default* semantics; if you change
thresholds, tests still pass because they construct their own parameter sets.

## Layout

```
breadth/
  criteria.py        # the 8 criteria as time series (pure, per stock)
  screen.py          # panels, RS ranking, liquidity filter, aggregation
  run.py             # CLI: --region us|europe|both, --backfill-days N
  outputs.py         # idempotent CSV writers
  fx.py              # GBX + FX handling for the liquidity floor only
  config.py
  data/              # DataProvider interface, yfinance, EODHD, parquet cache
  universe/          # US symbol directory, Europe seed loader + refresher
data/universe/europe_seed.csv
output/              # committed results
tests/               # 22 offline tests incl. per-criterion fixtures
```
