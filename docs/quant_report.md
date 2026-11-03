# Quant model report

_Generated 2026-11-03 12:11 UTC by the monthly retraining workflow._

## Honest summary

The model's signal is positive but too weak to be distinguished from luck (t-stat below 2). Treat its scores as a weak hint at most.

What it predicts: each stock's return minus SPY's over the next 20 trading days, from price history only. It is one input for the Analyst and never trades by itself.

## Walk-forward results

Tested 226 months (2008-01 to 2026-10). Each month the model was trained only on data whose outcome was known before that month (with a 20-day gap), then tested on the month.

| Measure | Model | Naive baseline |
|---|---|---|
| Rank correlation with outcome (IC), monthly average | +0.020 | 0 (no skill) |
| IC t-statistic (monthly) | +1.64 | – |
| Daily IC: model vs. simple 60-day momentum ranking | +0.022 | +0.012 |
| Months with positive IC | 58% | 50% |
| Top fifth minus bottom fifth, 20-day excess return | +0.80% | 0% |

IC is the rank correlation between predicted and actual excess returns across the ~30 stocks; 0.02-0.05 is typical for useful real-world signals, and with only 30 stocks the numbers are noisy.

## IC by year

| Year | IC |
|---|---|
| 2008 | -0.019 |
| 2009 | -0.126 |
| 2010 | +0.063 |
| 2011 | +0.067 |
| 2012 | +0.040 |
| 2013 | +0.001 |
| 2014 | +0.024 |
| 2015 | +0.052 |
| 2016 | +0.040 |
| 2017 | +0.028 |
| 2018 | +0.047 |
| 2019 | +0.009 |
| 2020 | +0.056 |
| 2021 | -0.027 |
| 2022 | -0.001 |
| 2023 | +0.064 |
| 2024 | +0.104 |
| 2025 | +0.011 |
| 2026 | -0.063 |

## Current model

Trained through 2026-10-05 on 182,307 samples (ridge, lambda 10.0).

| Feature | Weight |
|---|---|
| mom_5 | -0.00107 |
| mom_20 | +0.00082 |
| mom_60 | +0.00180 |
| mom_120 | -0.00402 |
| vol_20 | -0.00285 |
| vol_60 | +0.00580 |
| volume_chg | -0.00036 |
| drawdown_60 | -0.00117 |
| vs_ma20 | +0.00210 |
| vs_ma50 | -0.00309 |
| vs_ma200 | +0.00640 |
| sector_rel_20 | +0.00014 |
| vs_spy_20 | +0.00082 |

## Limits

- Past relationships between price patterns and future returns are weak and change over time.
- Only ~30 large stocks: small sample, results are noisy.
- Survivorship bias: today's large companies were chosen with hindsight, which flatters any backtest.
- No transaction costs are included in these numbers.
