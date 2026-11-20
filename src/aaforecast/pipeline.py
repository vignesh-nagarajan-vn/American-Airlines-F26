"""End-to-end steps used by the command line."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd

from . import config, data, external, validate
from .backtest import run_backtest
from .config import RunConfig
from .eda import run_eda
from .features import FeatureBuilder
from .forecast import run_forecast
from .geo import market_attributes
from .report import backtest_report, forecast_report

log = logging.getLogger(__name__)


@dataclass
class Inputs:
    panel: data.Panel
    airports: pd.DataFrame
    builder: FeatureBuilder


def load_inputs(cfg: RunConfig) -> Inputs:
    df = data.load_pax()
    airports = data.load_airports()
    if cfg.fast:
        top = df.groupby(["org", "des"])["od_pax"].sum().nlargest(cfg.fast_top_n).index
        df = df.set_index(["org", "des"]).loc[top].reset_index()
        log.info("Fast mode: %d largest markets", cfg.fast_top_n)
    panel = data.build_panel(df)
    attrs = market_attributes(panel.markets, airports)
    ext = external.load() if cfg.use_external else None
    builder = FeatureBuilder(panel, attrs, ext, cfg, airports)
    return Inputs(panel, airports, builder)


def _out(*parts):
    d = config.OUTPUT_DIR.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def step_validate() -> str:
    raw = data.read_pax_raw()
    clean = data.load_pax(use_cache=False)
    airports = data.load_airports()
    checks = validate.check_raw(raw) + validate.check_clean(clean, airports)
    text = validate.to_markdown(checks)
    d = _out("validation")
    (d / "data_quality.md").write_text(text, encoding="utf-8")
    validate.to_frame(checks).to_csv(d / "data_quality.csv", index=False)
    if any(c.status == "FAIL" for c in checks):
        log.error("Data validation has failures; see outputs/validation/data_quality.md")
    return text


def step_fetch(force: bool = False) -> dict:
    airports = data.load_airports()
    used = set(data.load_pax()[["org", "des"]].to_numpy().ravel())
    status = external.fetch_all(airports.loc[sorted(used)], force=force)
    (_out("validation") / "external_status.json").write_text(json.dumps(status, indent=2))
    return status


def step_eda() -> str:
    df = data.load_pax()
    return run_eda(data.build_panel(df), data.load_airports(), _out("eda"))


def _tag(cfg: RunConfig) -> str:
    return cfg.target + ("_fast" if cfg.fast else "")


def step_backtest(cfg: RunConfig) -> str:
    inp = load_inputs(cfg)
    res = run_backtest(inp.builder, cfg)
    d = _out("backtest", _tag(cfg))
    for name, frame in res.items():
        if name == "predictions":
            frame.to_csv(d / "predictions.csv.gz", index=False)
        else:
            frame.to_csv(d / f"{name}.csv", index=False)
    return backtest_report(res, d)


def step_forecast(cfg: RunConfig, shrink_override: float | None = None) -> str:
    inp = load_inputs(cfg)
    bt = config.OUTPUT_DIR / "backtest" / _tag(cfg)
    table = pd.read_csv(bt / "interval_table.csv") if (bt / "interval_table.csv").exists() else None
    agg_path = bt / "aggregate_interval_table.csv"
    agg = pd.read_csv(agg_path) if agg_path.exists() else None
    if shrink_override is not None:
        cfg.shrink = shrink_override
    elif (bt / "calibration.csv").exists():
        cal = pd.read_csv(bt / "calibration.csv")
        cfg.shrink = float(cal.loc[cal["selected_for_forecast"], "shrink"].iloc[0])
    res = run_forecast(inp.builder, cfg, table, agg)
    d = _out("forecast", _tag(cfg))
    for name, frame in res.items():
        frame.to_csv(d / f"{name}.csv", index=False)
    label = "passengers" if cfg.target == "pax" else "revenue (USD)"
    return forecast_report(res, d, label)
