"""Transparent baseline strategy: trend-pullback-v1.

Hypothesis:
Trade only in the direction of aligned 1h/4h trends, then enter when a
completed 15m candle reclaims the fast EMA after a pullback. Momentum,
volatility, volume, liquidity and order-book conditions are gates, not votes.

No confidence/win-probability score is produced. WAIT is normal.
"""
from __future__ import annotations

from typing import Any

from .config import StrategyConfig
from .indicators import atr_wilder, ema, median_volume_ratio, rsi_wilder, taker_buy_ratio
from .models import BookSnapshot, Candle, FuturesContext, TradeProposal


def _frame(candles: list[Candle], cfg: StrategyConfig) -> dict[str, float]:
    closes = [c.close for c in candles]
    fast = ema(closes, cfg.ema_fast)
    slow = ema(closes, cfg.ema_slow)
    rsi = rsi_wilder(closes, cfg.rsi_period)
    atr = atr_wilder(candles, cfg.atr_period)
    return {
        "close": closes[-1],
        "prev_close": closes[-2],
        "ema_fast": fast[-1],
        "prev_ema_fast": fast[-2],
        "ema_slow": slow[-1],
        "rsi": rsi[-1],
        "atr": atr[-1],
        "atr_percent": (atr[-1] / closes[-1]) * 100.0,
        "volume_ratio": median_volume_ratio(candles, 20),
        "taker_buy_ratio": taker_buy_ratio(candles, 20),
    }


def generate_proposal(
    *,
    symbol: str,
    market_type: str,
    signal_candles: list[Candle],
    context_1h: list[Candle],
    context_4h: list[Candle],
    book: BookSnapshot,
    futures: FuturesContext,
    server_time_ms: int,
    cfg: StrategyConfig,
    leverage: float,
) -> TradeProposal:
    s = _frame(signal_candles, cfg)
    h1 = _frame(context_1h, cfg)
    h4 = _frame(context_4h, cfg)

    signal_time = signal_candles[-1].close_time
    expires_at = signal_time + cfg.signal_expiry_minutes * 60_000

    long_checks: list[tuple[bool, str]] = [
        (h1["ema_fast"] > h1["ema_slow"], "1h EMA trend is bullish"),
        (h4["ema_fast"] > h4["ema_slow"], "4h EMA trend is bullish"),
        (s["ema_fast"] > s["ema_slow"], "15m EMA trend is bullish"),
        (
            s["prev_close"] <= s["prev_ema_fast"] and s["close"] > s["ema_fast"],
            "15m completed candle reclaimed EMA fast after a pullback",
        ),
        (
            cfg.long_rsi_min <= s["rsi"] <= cfg.long_rsi_max,
            f"15m RSI is inside long gate [{cfg.long_rsi_min}, {cfg.long_rsi_max}]",
        ),
        (
            cfg.min_atr_percent <= s["atr_percent"] <= cfg.max_atr_percent,
            "15m ATR percentage is inside configured volatility gate",
        ),
        (
            s["volume_ratio"] >= cfg.min_volume_ratio,
            "15m signal volume meets configured median-volume ratio",
        ),
        (
            book.spread_bps <= cfg.max_spread_bps,
            "spread is inside configured liquidity limit",
        ),
        (
            book.imbalance >= -cfg.max_adverse_book_imbalance,
            "order-book imbalance is not strongly adverse to a long",
        ),
        (server_time_ms <= expires_at, "signal has not expired"),
    ]

    short_checks: list[tuple[bool, str]] = [
        (h1["ema_fast"] < h1["ema_slow"], "1h EMA trend is bearish"),
        (h4["ema_fast"] < h4["ema_slow"], "4h EMA trend is bearish"),
        (s["ema_fast"] < s["ema_slow"], "15m EMA trend is bearish"),
        (
            s["prev_close"] >= s["prev_ema_fast"] and s["close"] < s["ema_fast"],
            "15m completed candle rejected EMA fast after a pullback",
        ),
        (
            cfg.short_rsi_min <= s["rsi"] <= cfg.short_rsi_max,
            f"15m RSI is inside short gate [{cfg.short_rsi_min}, {cfg.short_rsi_max}]",
        ),
        (
            cfg.min_atr_percent <= s["atr_percent"] <= cfg.max_atr_percent,
            "15m ATR percentage is inside configured volatility gate",
        ),
        (
            s["volume_ratio"] >= cfg.min_volume_ratio,
            "15m signal volume meets configured median-volume ratio",
        ),
        (
            book.spread_bps <= cfg.max_spread_bps,
            "spread is inside configured liquidity limit",
        ),
        (
            book.imbalance <= cfg.max_adverse_book_imbalance,
            "order-book imbalance is not strongly adverse to a short",
        ),
        (server_time_ms <= expires_at, "signal has not expired"),
    ]

    long_ok = all(ok for ok, _ in long_checks)
    short_ok = all(ok for ok, _ in short_checks)

    if h1["ema_fast"] > h1["ema_slow"] and h4["ema_fast"] > h4["ema_slow"]:
        regime = "trend-up"
    elif h1["ema_fast"] < h1["ema_slow"] and h4["ema_fast"] < h4["ema_slow"]:
        regime = "trend-down"
    else:
        regime = "mixed"

    direction: str | None = None
    checks: list[tuple[bool, str]]
    if long_ok and not short_ok:
        direction = "LONG"
        checks = long_checks
        entry = book.best_ask
        stop = min(
            signal_candles[-1].low - cfg.stop_atr_buffer * s["atr"],
            entry - 0.25 * s["atr"],
        )
        risk_distance = entry - stop
        target = entry + cfg.reward_risk * risk_distance
    elif short_ok and not long_ok:
        direction = "SHORT"
        checks = short_checks
        entry = book.best_bid
        stop = max(
            signal_candles[-1].high + cfg.stop_atr_buffer * s["atr"],
            entry + 0.25 * s["atr"],
        )
        risk_distance = stop - entry
        target = entry - cfg.reward_risk * risk_distance
    else:
        direction = None
        entry = stop = target = None
        checks = long_checks if sum(x for x, _ in long_checks) >= sum(x for x, _ in short_checks) else short_checks

    evidence = [text for ok, text in checks if ok]
    failures = [f"FAILED condition: {text}" for ok, text in checks if not ok]
    evidence.extend(
        [
            f"15m RSI={s['rsi']:.2f}",
            f"15m ATR={s['atr_percent']:.3f}%",
            f"volume ratio={s['volume_ratio']:.2f}x median",
            f"taker-buy ratio={s['taker_buy_ratio']:.3f}",
            f"spread={book.spread_bps:.3f} bps",
            f"book imbalance={book.imbalance:+.3f}",
        ]
    )
    if futures.last_funding_rate is not None:
        evidence.append(f"latest funding={futures.last_funding_rate:+.8f}")
    if futures.open_interest is not None:
        evidence.append(f"open interest={futures.open_interest:.6f}")

    return TradeProposal(
        decision="TRADE" if direction else "WAIT",
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        strategy_version=cfg.version,
        signal_timestamp=signal_time,
        expires_at=expires_at,
        order_type="MARKET",
        market_regime=regime,
        entry_conditions=[text for _ok, text in checks],
        entry_reference=entry,
        stop_loss=stop,
        take_profit=target,
        leverage=float(leverage),
        spread_bps=book.spread_bps,
        evidence=evidence,
        failure_reasons=failures,
        data_health={
            "signal_candle_close_time": signal_time,
            "context_1h_close_time": context_1h[-1].close_time,
            "context_4h_close_time": context_4h[-1].close_time,
            "book_observed_at": book.observed_at_ms,
            "server_time_ms": server_time_ms,
        },
    )
