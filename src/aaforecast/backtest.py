"""Rolling-origin backtest that mirrors how the forecast is used.

For each test origin T (for example 2024-07), the model is trained only on rows
whose target month is on or before T, then asked for months T+12 to T+18. This
is the same position a planner is in when building a schedule a year out.

The weight on the learned market mix is chosen without peeking: each fold uses
the weight that worked best on the folds before it.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import metrics
from .config import AA_HUBS, RunConfig
from .features import FeatureBuilder, training_origins
from .model import AnchorAdjustModel, baselines, combine

log = logging.getLogger(__name__)
METHODS = ["anchor_adjust", "seasonal_anchor", "same_month_last_year", "same_month_x_trend"]
SIZE_LABELS = ["<1k", "1k-10k", "10k-50k", "50k-200k", "200k+"]


def run_backtest(builder: FeatureBuilder, cfg: RunConfig) -> dict[str, pd.DataFrame]:
    months = builder.p.months
    n_months = len(months)
    origins = training_origins(n_months, cfg.origin_step)
    log.info("Building training rows for %d origins", len(origins))
    full = builder.build(origins)

    folds = []
    for label in cfg.backtest_origins:
        T = builder.p.month_index(label)
        if T + max(cfg.horizons) >= n_months:
            log.warning("Skipping origin %s: not enough actuals after it", label)
            continue
        model = AnchorAdjustModel(cfg).fit(full[full["target_idx"] <= T])
        test = builder.origin_rows(T, min_ttm=0.0)
        test = test[test["y"].notna()].reset_index(drop=True)
        test["raw_mix"] = model.raw_mix(test)
        for name, values in baselines(test).items():
            test[name] = values
        test["fold"] = label
        folds.append(test)
        log.info("Fold %s trained on %d rows", label, int((full["target_idx"] <= T).sum()))

    # Score every shrink weight on every fold, then pick each fold's weight from earlier folds only.
    grid = {}
    for test in folds:
        for s in cfg.shrink_grid:
            grid[(test["fold"].iloc[0], s)] = metrics.wape(test["y"], combine(test, test["raw_mix"], s, cfg.network_growth))
    calib = pd.Series(grid).rename_axis(["fold", "shrink"]).rename("wape").reset_index()
    preds = []
    for i, test in enumerate(folds):
        earlier = calib[calib["fold"].isin([f["fold"].iloc[0] for f in folds[:i]])]
        s = cfg.shrink if earlier.empty else float(earlier.groupby("shrink")["wape"].mean().idxmin())
        test["anchor_adjust"] = combine(test, test["raw_mix"], s, cfg.network_growth)
        test["shrink_used"] = s
        log.info("Fold %s: shrink %.2f, WAPE %.3f vs seasonal anchor %.3f vs last year %.3f",
                 test["fold"].iloc[0], s, metrics.wape(test["y"], test["anchor_adjust"]),
                 metrics.wape(test["y"], test["seasonal_anchor"]), metrics.wape(test["y"], test["same_month_last_year"]))
        keep = ["fold", "shrink_used", "market_id", "org", "des", "horizon", "origin", "target_month", "ttm", "y",
                "mkt_domestic", *METHODS]
        preds.append(test[keep])
    pred = pd.concat(preds, ignore_index=True)
    pred["segment"] = np.where(pred["mkt_domestic"] == 1, "Domestic", "International")
    pred["size_band"] = _size_bucket(pred["ttm"])

    chosen = float(calib.groupby("shrink")["wape"].mean().idxmin())
    out = {"predictions": pred}
    out["calibration"] = calib.assign(selected_for_forecast=lambda d: d["shrink"] == chosen)
    out["overall"] = metrics.summarize(pred, METHODS)
    out["by_horizon"] = metrics.summarize(pred, METHODS, ["horizon"])
    out["by_fold"] = metrics.summarize(pred, METHODS, ["fold"])
    out["by_segment"] = metrics.summarize(pred, METHODS, ["segment"])
    out["by_size"] = metrics.summarize(pred, METHODS, ["size_band"])

    # Aggregation levels planners use: origin airport, AA hub touch, whole network.
    airport = pred.groupby(["fold", "org", "target_month"], as_index=False)[["y", *METHODS]].sum()
    out["airport_level"] = metrics.summarize(airport, METHODS)
    hub_rows, hub_frames = [], []
    for hub in sorted(AA_HUBS):
        sel = pred[(pred["org"] == hub) | (pred["des"] == hub)]
        g = sel.groupby(["fold", "target_month"], as_index=False)[["y", *METHODS]].sum()
        hub_frames.append(g)
        s = metrics.summarize(g, METHODS)
        s.insert(0, "hub", hub)
        hub_rows.append(s)
    out["hub_level"] = pd.concat(hub_rows, ignore_index=True)
    network = pred.groupby(["fold", "target_month"], as_index=False)[["y", *METHODS]].sum()
    out["network_level"] = metrics.summarize(network, METHODS)
    out["aggregate_interval_table"] = aggregate_intervals(
        {"airport": airport, "hub": pd.concat(hub_frames, ignore_index=True), "network": network}, cfg.interval)

    out["significance"] = pd.DataFrame([metrics.paired_test(pred, "anchor_adjust", ref) for ref in METHODS[1:]])

    table, cov = interval_table(pred, cfg.interval)
    out["interval_table"] = table
    out["interval_coverage"] = cov
    return out


def _size_bucket(ttm: pd.Series) -> pd.Series:
    edges = [0, 1_000, 10_000, 50_000, 200_000, np.inf]
    return pd.cut(ttm, edges, labels=SIZE_LABELS, right=False)


def _quantiles(p: pd.DataFrame, a: float, min_n: int = 50) -> pd.DataFrame:
    """Error quantiles per (horizon, size); thin cells fall back to the whole horizon, then to all rows."""
    def q(g):
        return pd.Series({"q_low": g.quantile(a), "q_high": g.quantile(1 - a), "n": len(g)})

    cell = p.groupby(["horizon", "size"], observed=True)["err"].apply(q).unstack().reset_index()
    by_h = p.groupby("horizon")["err"].apply(q).unstack()
    overall = q(p["err"])
    grid = pd.MultiIndex.from_product([sorted(p["horizon"].unique()), SIZE_LABELS], names=["horizon", "size"]).to_frame(index=False)
    cell["size"] = cell["size"].astype(str)
    t = grid.merge(cell, on=["horizon", "size"], how="left")
    thin = t["n"].isna() | (t["n"] < min_n)
    for col in ("q_low", "q_high"):
        fallback = t["horizon"].map(by_h[col]).where(t["horizon"].map(by_h["n"]) >= min_n, overall[col])
        t[col] = t[col].where(~thin, fallback)
    return t[["horizon", "size", "q_low", "q_high"]]


def interval_table(pred: pd.DataFrame, level: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Empirical log-error quantiles by horizon and market size.

    Coverage is checked honestly: each fold's intervals come from the other folds.
    """
    a = (1 - level) / 2
    p = pred.assign(err=np.log1p(pred["y"]) - np.log1p(pred["anchor_adjust"]), size=_size_bucket(pred["ttm"]))
    cov_rows = []
    for fold in p["fold"].unique():
        other = p[p["fold"] != fold]
        if other.empty:
            continue
        cur = apply_intervals(p[p["fold"] == fold], _quantiles(other, a), "anchor_adjust")
        inside = (cur["y"] >= cur["lower"]) & (cur["y"] <= cur["upper"])
        cov_rows.append({"fold": fold, "target": level, "coverage": inside.mean(),
                         "volume_weighted_coverage": (inside * cur["y"]).sum() / cur["y"].sum()})
    return _quantiles(p, a), pd.DataFrame(cov_rows)


def aggregate_intervals(levels: dict[str, pd.DataFrame], level: float) -> pd.DataFrame:
    """Interval quantiles for totals, from backtest errors measured at that total.

    Summing market bounds would assume every market misses in the same
    direction at once, which makes airport and network bands far too wide.
    """
    a = (1 - level) / 2
    rows = []
    for name, df in levels.items():
        err = np.log1p(df["y"]) - np.log1p(df["anchor_adjust"])
        rows.append({"level": name, "q_low": err.quantile(a), "q_high": err.quantile(1 - a), "n": len(err)})
    return pd.DataFrame(rows)


def apply_aggregate_interval(df: pd.DataFrame, table: pd.DataFrame | None, level: str, forecast_col: str = "forecast") -> pd.DataFrame:
    d = df.copy()
    if table is None or level not in set(table["level"]):
        d["lower"] = np.nan
        d["upper"] = np.nan
        return d
    q = table.set_index("level").loc[level]
    d["lower"] = np.clip(d[forecast_col] * np.exp(q["q_low"]), 0, None)
    d["upper"] = d[forecast_col] * np.exp(q["q_high"])
    return d


def apply_intervals(df: pd.DataFrame, table: pd.DataFrame, forecast_col: str = "forecast") -> pd.DataFrame:
    t = table.copy()
    t["size"] = pd.Categorical(t["size"].astype(str), categories=SIZE_LABELS)
    d = df.drop(columns=["size"], errors="ignore").assign(size=_size_bucket(df["ttm"]))
    d = d.merge(t, on=["horizon", "size"], how="left")
    base = np.log1p(d[forecast_col])
    d["lower"] = np.clip(np.expm1(base + d["q_low"]), 0, None)
    d["upper"] = np.expm1(base + d["q_high"])
    return d.drop(columns=["size", "q_low", "q_high"])
