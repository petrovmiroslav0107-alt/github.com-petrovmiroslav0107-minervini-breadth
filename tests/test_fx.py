import pytest

from breadth.fx import FALLBACK_EUR_RATES, eur_rates, normalise_currency


def test_gbx_pence_normalisation():
    iso, mult = normalise_currency("GBX")
    assert iso == "GBP"
    assert mult == 0.01


def test_regular_currency_passthrough():
    assert normalise_currency("SEK") == ("SEK", 1.0)
    assert normalise_currency("eur") == ("EUR", 1.0)


def test_fallback_rates_used_without_provider():
    rates = eur_rates({"GBP", "EUR"}, provider=None)
    assert rates["EUR"] == 1.0
    assert rates["GBP"] == FALLBACK_EUR_RATES["GBP"]


def test_unknown_currency_raises():
    with pytest.raises(ValueError):
        eur_rates({"XYZ"}, provider=None)


def test_lse_liquidity_floor_example():
    # EUR 3M floor for a GBX-quoted stock: floor in pence-units of traded value
    rates = eur_rates({"GBP"}, provider=None)
    iso, mult = normalise_currency("GBX")
    floor_local = 3_000_000 / (rates[iso] * mult)
    # 3M EUR ~= 2.56M GBP ~= 256M pence-units at the fallback rate
    assert floor_local == pytest.approx(3_000_000 / (1.17 * 0.01))
