# Methodology

## The Planning Question

Network and schedule planning commit capacity about a year before departure. The question is: given everything known today, how many passengers will fly each O&D market in each month 12 to 18 months from now, how confident are we, and why?

A useful answer has to do four things:

- Beat the simple rules planners already use, at the market level and when rolled up.
- Explain itself in planning terms rather than model internals.
- Let the planner set the network-wide assumption.
- Say how wide the range of outcomes is.

## Forecast Structure

For market `m`, forecast origin `t`, and horizon `h` from 12 to 18 months:

```
anchor_m    = (trailing 12-month demand / 12) x exp(seasonal shape for the target month)
mix_m       = shrink x f(features known at t)
forecast_m  = anchor_m x exp(mix_m) x scale
scale       = chosen so all markets sum to  sum(anchor) x (1 + network growth) ^ (h / 12)
```

**Seasonal shape** is the log ratio of the target calendar month to its trailing 12-month average, averaged over the two most recent years whose month and trailing window are clear of the pandemic. Averaging two years removes much of the noise in a single "same month last year" value. In backtests, the anchor alone beats that rule.

**Network growth** defaults to zero. Network-wide swings 12 to 18 months out tend to come from shocks, such as the pandemic and its recovery, and those are not learnable from seven years of data. Planners also usually hold a corporate growth view. The `--network-growth` flag plugs it in.

**Market mix** is a histogram gradient-boosted tree model (scikit-learn), trained on every market, origin, and horizon at once. Its target is each market's log growth versus its anchor minus the volume-weighted average for that origin and horizon. So it learns who outgrows whom, which carries over from one year to the next far better than absolute growth. Rows are weighted by the square root of trailing volume. Pandemic-era target months (2020-03 to 2021-12) are excluded.

**Shrink** scales the learned mix between 0 (anchor only) and 1. Each backtest fold uses the value that scored best on earlier folds only. The production forecast uses the value that scored best across all folds.

## Features

All features are computed as of the origin month. Names carry a driver-family prefix, and the driver bridge reports results by family.

| Family | Examples |
| --- | --- |
| Market momentum | Last 12 months, trailing-12 growth, 3 and 6 month growth, volatility, recovery versus 2019 |
| Seasonality and calendar | Horizon, seasonal shape, Easter and weekend shifts between the anchor and target month |
| Airport and network trend | Origin and destination airport growth, market share of the origin airport, network growth |
| Market profile | Distance, domestic or international, AA hub endpoints, coordinates, runway length |
| Demographics and migration | Catchment and metro population growth, FAA enplanement growth, movers between catchments |
| Economy and fuel | Jet fuel, Brent, unemployment, consumer sentiment, airfare CPI, CPI, real disposable income |
| Exchange rates | 12-month change in the local currency per US dollar at the foreign endpoint |
| Fares | Average fare, fare growth, fare per kilometer |

### Catchments

Every US county is assigned to its nearest network airport within 160 km, using the county internal points from the county boundaries dataset. Airports with the same metro code pool their counties, so JFK, LGA, and EWR share a New York catchment. County population and county-to-county migration flows are then summed by catchment.

### Publication Lags

| Source | Assumed available |
| --- | --- |
| FRED monthly series | One month after the reference month |
| Census population, vintage Y | April of Y+1 |
| FAA enplanements, calendar year Y | October of Y+1 |
| Migration flows, 2016 to 2020 | Treated as a fixed market attribute |

## Explaining A Forecast

The driver bridge splits each market's `log(forecast / anchor)` into exact group Shapley values over the eight driver families. A family that is switched off takes its training median, so each effect is measured against a typical market. The pieces add up exactly:

```
log(forecast / anchor) = typical market + sum of family effects + network outlook and rescaling
```

Hub and network bridges are volume-weighted averages of market bridges.

## Uncertainty

Intervals come from the backtest itself. For each horizon and market-size band, the 10th and 90th percentiles of `log(actual / forecast)` set an 80% band. Thin cells fall back to the whole horizon. Coverage is checked on held-out folds, so the reported coverage is out of sample.

## Scenarios

A scenario shifts the economy, fuel, or FX features at the origin and re-scores every market. The network total stays at the outlook, so a scenario shows exposure: which hubs and segments gain or lose share. These are sensitivities learned from 2019 onward, not causal effects.

## Evaluation

| Check | Detail |
| --- | --- |
| Rolling origins | 2024-01, 2024-04, 2024-07, 2024-10, 2025-01, each scored on months 12 to 18 ahead |
| Metric | WAPE (total absolute error over total actual) and bias, at market, airport, hub, and network level |
| Baselines | Seasonal anchor alone, same month last year, last year times trailing growth |
| Significance | Wilcoxon signed-rank on per-market error differences and a market bootstrap for the WAPE gap |
| Diagnostics | Chow test for the 2020 break, ADF and KPSS stationarity, STL trend and seasonal strength |

## Known Limits

- Network-level turns are not predicted. They come in through the network outlook.
- Seven years of history, with two of them disrupted, give little room to learn how demand responds to macro conditions.
- The data does not include capacity or schedule plans. Adding planned seats per market would likely be the single biggest gain.
