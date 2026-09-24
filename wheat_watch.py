from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf


STAGE_NAMES = {
    0: "NO SETUP / INVALIDATED",
    1: "WATCH",
    2: "WATCH CLOSELY",
    3: "READY",
    4: "TRIGGER",
}


@dataclass(frozen=True)
class Metrics:
    bar_date: str
    price: float
    atr14: float
    ema10: float
    ema20: float
    sma50: float
    sma30w: float
    distance_sma50_atr: float
    ema_spread_atr: float
    ema10_slope_atr_day: float
    ema20_slope_atr_day: float


@dataclass(frozen=True)
class Checks:
    sma50_rising: bool
    sma30w_rising: bool
    structure_valid: bool
    not_extended: bool
    price_near_sma50: bool
    ema_compressed: bool
    recent_pullback_compression: bool
    ema10_flattening_or_rising: bool
    ema20_flattening_or_rising: bool
    at_least_one_short_ema_rising: bool
    ema10_above_ema20: bool
    close_above_short_emas: bool
    bullish_reexpansion: bool


@dataclass(frozen=True)
class Evaluation:
    computed_stage: int
    hard_invalidated: bool
    metrics: Metrics
    checks: Checks


def _normalize_yfinance_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(col[0]) for col in out.columns]
    rename = {str(col).title(): col for col in out.columns}
    required = ["Open", "High", "Low", "Close"]
    missing = [name for name in required if name not in rename]
    if missing:
        raise RuntimeError(f"Missing OHLC columns from market data: {missing}")
    out = out.rename(columns={rename[name]: name for name in required})
    return out[required].apply(pd.to_numeric, errors="coerce").dropna()


def fetch_daily(symbol: str, period: str = "2y") -> pd.DataFrame:
    raw = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"No market data returned for {symbol}")
    frame = _normalize_yfinance_columns(raw)
    if len(frame) < 180:
        raise RuntimeError(f"Not enough daily bars for 30-week/50-day model: {len(frame)}")
    return frame


def wilder_atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def evaluate(frame: pd.DataFrame) -> Evaluation:
    close = frame["Close"]
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50).mean()
    atr14 = wilder_atr(frame, 14)

    weekly_close = close.resample("W-FRI").last().dropna()
    sma30w = weekly_close.rolling(30).mean()
    if len(sma30w.dropna()) < 5:
        raise RuntimeError("Not enough weekly bars for 30-week slope")

    latest_atr = float(atr14.iloc[-1])
    if not pd.notna(latest_atr) or latest_atr <= 0:
        raise RuntimeError("ATR14 is unavailable or invalid")

    price = float(close.iloc[-1])
    e10 = float(ema10.iloc[-1])
    e20 = float(ema20.iloc[-1])
    s50 = float(sma50.iloc[-1])
    s30w = float(sma30w.iloc[-1])

    sma50_rising = s50 > float(sma50.iloc[-6])
    sma30w_rising = s30w > float(sma30w.iloc[-5])
    structure_valid = price >= s50 - 1.50 * latest_atr
    not_extended = price <= e20 + 1.50 * latest_atr

    near = (sma50 - 0.75 * atr14 <= close) & (close <= sma50 + 1.25 * atr14)
    compressed = (ema10 - ema20).abs() <= 0.35 * atr14
    not_ext_series = close <= ema20 + 1.50 * atr14
    setup_series = (near & compressed & not_ext_series).fillna(False)
    recent_pullback_compression = bool(setup_series.tail(10).any())

    ema10_slope = (ema10 - ema10.shift(3)) / (3.0 * atr14)
    ema20_slope = (ema20 - ema20.shift(3)) / (3.0 * atr14)
    e10_slope = float(ema10_slope.iloc[-1])
    e20_slope = float(ema20_slope.iloc[-1])

    ema10_flat = e10_slope >= -0.05
    ema20_flat = e20_slope >= -0.03
    at_least_one_rising = e10_slope > 0 or e20_slope > 0

    ready_series = (
        setup_series
        & (ema10_slope >= -0.05)
        & (ema20_slope >= -0.03)
        & ((ema10_slope > 0) | (ema20_slope > 0))
    ).fillna(False)
    recent_ready = bool(ready_series.tail(5).any())

    ema10_above = e10 > e20
    close_above_short = price > e10 and price > e20
    spread = ema10 - ema20
    fresh_cross = bool(ema10.iloc[-1] > ema20.iloc[-1] and ema10.iloc[-2] <= ema20.iloc[-2])
    widening_positive_spread = bool(spread.iloc[-1] > 0 and spread.iloc[-1] > spread.iloc[-2])
    bullish_reexpansion = bool(fresh_cross or widening_positive_spread)

    checks = Checks(
        sma50_rising=sma50_rising,
        sma30w_rising=sma30w_rising,
        structure_valid=structure_valid,
        not_extended=not_extended,
        price_near_sma50=bool(near.iloc[-1]),
        ema_compressed=bool(compressed.iloc[-1]),
        recent_pullback_compression=recent_pullback_compression,
        ema10_flattening_or_rising=ema10_flat,
        ema20_flattening_or_rising=ema20_flat,
        at_least_one_short_ema_rising=at_least_one_rising,
        ema10_above_ema20=ema10_above,
        close_above_short_emas=close_above_short,
        bullish_reexpansion=bullish_reexpansion,
    )

    hard_invalidated = not (sma50_rising and sma30w_rising and structure_valid)
    trend_ok = sma50_rising and sma30w_rising and structure_valid

    trigger = (
        trend_ok
        and not_extended
        and recent_pullback_compression
        and e10_slope > 0
        and e20_slope >= 0
        and ema10_above
        and close_above_short
        and bullish_reexpansion
    )
    ready = trend_ok and not_extended and recent_pullback_compression and recent_ready
    watch_closely = trend_ok and not_extended and recent_pullback_compression

    if trigger:
        stage = 4
    elif ready:
        stage = 3
    elif watch_closely:
        stage = 2
    elif trend_ok:
        stage = 1
    else:
        stage = 0

    metrics = Metrics(
        bar_date=str(frame.index[-1].date()),
        price=price,
        atr14=latest_atr,
        ema10=e10,
        ema20=e20,
        sma50=s50,
        sma30w=s30w,
        distance_sma50_atr=(price - s50) / latest_atr,
        ema_spread_atr=abs(e10 - e20) / latest_atr,
        ema10_slope_atr_day=e10_slope,
        ema20_slope_atr_day=e20_slope,
    )
    return Evaluation(stage, hard_invalidated, metrics, checks)


def check_market_data_freshness(bar_date: str, max_calendar_days: int = 4) -> None:
    last_date = datetime.strptime(bar_date, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    age = (today - last_date).days
    if age < 0 or age > max_calendar_days:
        raise RuntimeError(f"Stale market data: latest bar is {bar_date} ({age} calendar days old)")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_hysteresis(evaluation: Evaluation, previous: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    computed = evaluation.computed_stage
    bar_date = evaluation.metrics.bar_date

    if "effective_stage" not in previous:
        effective = computed
        pending = 0
    else:
        prior = int(previous.get("effective_stage", 0))
        pending = int(previous.get("pending_downgrade_bars", 0))
        same_bar = str(previous.get("last_bar_date", "")) == bar_date

        if evaluation.hard_invalidated:
            effective = 0
            pending = 0
        elif computed >= prior:
            effective = computed
            pending = 0
        else:
            effective = prior
            if not same_bar:
                pending += 1
            if pending >= 2:
                effective = computed
                pending = 0

    state = {
        "effective_stage": effective,
        "pending_downgrade_bars": pending,
        "last_bar_date": bar_date,
        "last_notified_stage": previous.get("last_notified_stage"),
    }
    return effective, state


def trigger_requirements(checks: Checks) -> list[tuple[str, bool]]:
    return [
        ("SMA50 rising", checks.sma50_rising),
        ("SMA30W rising", checks.sma30w_rising),
        ("Structure valid", checks.structure_valid),
        ("Not extended", checks.not_extended),
        ("Recent pullback + EMA compression armed", checks.recent_pullback_compression),
        ("EMA10 rising", checks.ema10_flattening_or_rising and checks.at_least_one_short_ema_rising),
        ("EMA20 non-falling", checks.ema20_flattening_or_rising),
        ("EMA10 > EMA20", checks.ema10_above_ema20),
        ("Close > EMA10 & EMA20", checks.close_above_short_emas),
        ("Bullish EMA re-expansion", checks.bullish_reexpansion),
    ]


def build_message(symbol: str, effective_stage: int, evaluation: Evaluation) -> str:
    m = evaluation.metrics
    lines = [
        f"🌾 WHEAT — {effective_stage}/4 {STAGE_NAMES[effective_stage]}",
        f"Source: {symbol} (MZW proxy) | Price: {m.price:.2f}",
        "",
    ]

    requirements = trigger_requirements(evaluation.checks)
    for label, passed in requirements:
        lines.append(f"{'✅' if passed else '❌'} {label}")

    missing = [label for label, passed in requirements if not passed]
    lines.append("")
    if effective_stage == 4:
        lines.append("4/4 COMPLETE")
    elif missing:
        lines.append("Still needed for 4/4: " + "; ".join(missing))
    else:
        lines.append("All 4/4 checks currently pass.")

    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram delivery requested but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    response.raise_for_status()


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_github_output(**values: Any) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=os.environ.get("WHEAT_TICKER", "ZW=F"))
    parser.add_argument("--state-path", default=".state/wheat.json")
    parser.add_argument("--snapshot-path", default="snapshot.json")
    parser.add_argument("--report-path", default="report.md")
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--force-notify", action="store_true")
    args = parser.parse_args()

    frame = fetch_daily(args.symbol)
    evaluation = evaluate(frame)
    check_market_data_freshness(evaluation.metrics.bar_date)

    state_path = Path(args.state_path)
    previous = load_state(state_path)
    effective_stage, state = apply_hysteresis(evaluation, previous)

    prior_notified = previous.get("last_notified_stage")
    should_notify = bool(
        args.force_notify
        or (
            args.deliver
            and prior_notified != effective_stage
            and (effective_stage > 0 or (isinstance(prior_notified, int) and prior_notified > 0))
        )
    )

    message = build_message(args.symbol, effective_stage, evaluation)
    print(message)

    if should_notify:
        send_telegram(message)
        state["last_notified_stage"] = effective_stage

    old_state = previous
    state_changed = state != old_state
    save_json(state_path, state)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol,
        "computed_stage": evaluation.computed_stage,
        "effective_stage": effective_stage,
        "stage_name": STAGE_NAMES[effective_stage],
        "hard_invalidated": evaluation.hard_invalidated,
        "delivered": should_notify,
        "metrics": asdict(evaluation.metrics),
        "checks": asdict(evaluation.checks),
        "trigger_requirements": [
            {"name": label, "passed": passed}
            for label, passed in trigger_requirements(evaluation.checks)
        ],
    }
    save_json(Path(args.snapshot_path), snapshot)
    Path(args.report_path).write_text("## Wheat Trend Restart Watch\n\n" + message + "\n", encoding="utf-8")

    write_github_output(
        state_changed=state_changed,
        effective_stage=effective_stage,
        computed_stage=evaluation.computed_stage,
        delivered=should_notify,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
