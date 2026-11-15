"""Command line entry point: python -m aaforecast <command>."""

from __future__ import annotations

import argparse
import logging
import sys
import warnings

from .config import RunConfig

COMMANDS = ["validate", "fetch", "eda", "backtest", "forecast", "all"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aaforecast", description="12 to 18 month O&D demand forecasting")
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--target", choices=["pax", "rev"], default="pax", help="forecast passengers or revenue")
    ap.add_argument("--fast", action="store_true", help="largest markets only, for quick iteration")
    ap.add_argument("--no-external", action="store_true", help="skip public datasets")
    ap.add_argument("--origin-step", type=int, default=3, help="months between training origins")
    ap.add_argument("--force", action="store_true", help="re-download external data")
    ap.add_argument("--network-growth", type=float, default=0.0,
                    help="annual network growth assumption, e.g. 0.03 for +3%% (default flat)")
    ap.add_argument("--shrink", type=float, default=None,
                    help="weight on the learned market mix; default is the backtest's choice")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    cfg = RunConfig(target=args.target, fast=args.fast, use_external=not args.no_external,
                    origin_step=args.origin_step, network_growth=args.network_growth)

    from . import pipeline

    steps = COMMANDS[:-1] if args.command == "all" else [args.command]
    for step in steps:
        logging.info("Running %s", step)
        if step == "validate":
            print(pipeline.step_validate())
        elif step == "fetch":
            print(pipeline.step_fetch(force=args.force))
        elif step == "eda":
            print(pipeline.step_eda())
        elif step == "backtest":
            print(pipeline.step_backtest(cfg))
        elif step == "forecast":
            print(pipeline.step_forecast(cfg, args.shrink))
    return 0


if __name__ == "__main__":
    sys.exit(main())
