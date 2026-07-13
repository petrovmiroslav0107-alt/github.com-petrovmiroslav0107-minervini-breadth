"""Entry point.

Daily run:      python -m breadth.run --region europe
                python -m breadth.run --region us
Both regions:   python -m breadth.run --region both
Backfill:       python -m breadth.run --region both --backfill-days 252
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .data import get_provider
from .outputs import write_outputs
from .screen import run_screen
from .universe import build_us_universe, load_europe_universe

log = logging.getLogger(__name__)


def run_region(region: str, cfg: dict, eval_days: int) -> bool:
    provider = get_provider(cfg)
    universe = (build_us_universe(cfg) if region == "us"
                else load_europe_universe(cfg))
    result = run_screen(region, universe, provider, cfg, eval_days=eval_days)
    write_outputs(result, cfg)
    latest = result.breadth.iloc[-1]
    log.info("%s %s: %s/%s passing (%.2f%%)", region, latest["date"],
             latest["pass_count"], latest["universe_size"],
             float(latest["pass_percentage"]))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=["us", "europe", "both"], required=True)
    parser.add_argument("--backfill-days", type=int, default=1, metavar="N",
                        help="evaluate the last N trading days (default 1; "
                             "use ~252 to reconstruct 12 months)")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)

    regions = ["europe", "us"] if args.region == "both" else [args.region]
    failures = 0
    for region in regions:
        try:
            run_region(region, cfg, args.backfill_days)
        except Exception:  # noqa: BLE001 - one region must not block the other
            log.exception("Region %s failed", region)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
