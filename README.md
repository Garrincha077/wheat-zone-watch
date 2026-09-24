# Wheat Trend Restart Watch

Standalone GitHub Actions watcher for a Micro Wheat (MZW) trend-restart setup.

This repository is intentionally isolated from StockScout-Unified. It does not import from, dispatch to, or write to any other repository. The workflow runs with `contents: read` only.

## Signal stages

- **0/4 — NO SETUP / INVALIDATED**
- **1/4 — WATCH**: rising 50-day SMA + rising 30-week SMA + valid structure
- **2/4 — WATCH CLOSELY**: 1/4 + price near the 50-day SMA + 10/20 EMA compression
- **3/4 — READY**: 2/4 + short EMAs stop falling / begin turning
- **4/4 — TRIGGER**: bullish 10/20 EMA alignment and upward re-expansion, with price above both short EMAs

The Telegram/status report always shows every condition as ✅ or ❌ so it is obvious what is still missing for 4/4.

## Data

Default market-data symbol: `ZW=F` from Yahoo Finance via yfinance. It is used as a liquid Chicago SRW Wheat proxy for MZW technical structure. Set `WHEAT_TICKER` in the workflow if the data source changes later.

No fixed price zone is used. The setup is normalized by ATR and moving-average structure.

## Key rules

- SMA50 rising: current SMA50 > SMA50 five trading bars ago
- SMA30W rising: current 30-week SMA > value four weekly bars ago
- Structure valid: close >= SMA50 - 1.5 ATR
- Not extended: close <= EMA20 + 1.5 ATR
- Near SMA50: close between SMA50 - 0.75 ATR and SMA50 + 1.25 ATR
- EMA compression: |EMA10 - EMA20| <= 0.35 ATR
- EMA10 flattening/rising: 3-bar normalized slope >= -0.05 ATR/day
- EMA20 flattening/rising: 3-bar normalized slope >= -0.03 ATR/day
- READY also requires at least one short EMA to have a positive slope
- TRIGGER requires EMA10 > EMA20, both short EMA slopes non-negative, close above both, and either a fresh bullish cross or widening positive EMA spread

ATR14 uses Wilder smoothing.

## Notifications

Scheduled runs are dry-run unless repository variable `WHEAT_DELIVER` is set to `true`.

To enable Telegram delivery, add these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Then create repository variable:

- `WHEAT_DELIVER=true`

A manual workflow run can force a current-status Telegram message even if the stage has not changed.

The watcher sends automatically only when the effective stage changes. A downgrade requires two consecutive daily bars below the current stage, except hard invalidation, which is immediate.

## Schedule

The watcher runs every 30 minutes from 00:00 through 20:59 UTC, Sunday through Friday. A freshness guard rejects stale data. The schedule is deliberately less frequent than 15 minutes to reduce GitHub Actions usage while still being timely for a daily-chart setup.

## Safety

The workflow has:

- `permissions: contents: read`
- 5-minute timeout
- concurrency cancellation
- no cross-repository token
- no repository writes
- state stored only in GitHub Actions cache
