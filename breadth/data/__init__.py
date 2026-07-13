from .base import DataProvider, FetchResult


def get_provider(cfg: dict) -> DataProvider:
    """Provider imports are lazy so the screening engine (and its tests)
    never depend on a provider library that isn't in use."""
    name = cfg.get("provider", "yfinance")
    if name == "yfinance":
        from .yfinance_provider import YFinanceProvider
        return YFinanceProvider(cfg)
    if name == "eodhd":
        from .eodhd_provider import EODHDProvider
        return EODHDProvider(cfg)
    raise ValueError(f"Unknown data provider: {name!r}")
