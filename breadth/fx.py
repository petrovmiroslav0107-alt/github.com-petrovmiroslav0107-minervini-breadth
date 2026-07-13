"""Currency handling for the liquidity floor.

The 8 Trend Template criteria are price-relative, so currency never matters
for them. It matters ONLY for the liquidity floor (price > EUR 5 equivalent,
50-day average traded value > EUR 3M for Europe / USD floors for the US).

LSE special case: Yahoo quotes most .L tickers in pence (GBX). Prices and
traded value must be divided by 100 to get GBP before converting to EUR.
The universe seed file carries a `currency` column (GBX for LSE) so this is
explicit per ticker, not guessed.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Rough static fallbacks (units of EUR per 1 unit of currency), used only if
# the live FX fetch fails. Liquidity floors are coarse filters, so a few
# percent of FX drift is acceptable; a run never dies because FX was down.
FALLBACK_EUR_RATES = {
    "EUR": 1.0,
    "USD": 0.92,
    "GBP": 1.17,
    "CHF": 1.06,
    "SEK": 0.088,
    "DKK": 0.134,
    "NOK": 0.086,
}


def normalise_currency(currency: str) -> tuple[str, float]:
    """Map a quote currency to (ISO currency, price multiplier).

    GBX (pence) -> ("GBP", 0.01). Everything else is a pass-through.
    """
    cur = (currency or "").strip().upper()
    if cur in ("GBX", "GBP.", "GBP_PENCE", "PENCE"):
        return "GBP", 0.01
    return cur, 1.0


def eur_rates(currencies: set[str], provider=None) -> dict[str, float]:
    """Return EUR-per-unit rates for the given ISO currencies.

    Tries the data provider's live FX first, falls back to the static table.
    """
    rates: dict[str, float] = {}
    live: dict[str, float] = {}
    if provider is not None:
        try:
            live = provider.get_fx_to_eur(sorted(currencies))
        except Exception as exc:  # noqa: BLE001 - FX must never kill a run
            log.warning("Live FX fetch failed (%s); using static fallbacks", exc)
    for cur in currencies:
        if cur in live and live[cur] > 0:
            rates[cur] = live[cur]
        elif cur in FALLBACK_EUR_RATES:
            rates[cur] = FALLBACK_EUR_RATES[cur]
            log.warning("Using static fallback FX rate for %s", cur)
        else:
            raise ValueError(f"No FX rate available for currency {cur!r}; "
                             f"add it to FALLBACK_EUR_RATES in breadth/fx.py")
    return rates
