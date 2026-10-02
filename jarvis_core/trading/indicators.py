"""Small, reproducible indicator library used by the baseline strategy."""
from __future__ import annotations

import math
import statistics

from .models import Candle


def ema(values: list[float], period: int) -> list[float]:
    if period < 1 or len(values) < period:
        raise ValueError("Not enough values for EMA.")
    seed = statistics.fmean(values[:period])
    alpha = 2.0 / (period + 1.0)
    out = [math.nan] * (period - 1) + [seed]
    current = seed
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def rsi_wilder(values: list[float], period: int = 14) -> list[float]:
    if len(values) <= period:
        raise ValueError("Not enough values for RSI.")
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values, values[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = statistics.fmean(gains[:period])
    avg_loss = statistics.fmean(losses[:period])
    out = [math.nan] * period

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    out.append(value(avg_gain, avg_loss))
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        out.append(value(avg_gain, avg_loss))
    return out


def atr_wilder(candles: list[Candle], period: int = 14) -> list[float]:
    if len(candles) <= period:
        raise ValueError("Not enough candles for ATR.")
    trs: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        tr = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
        trs.append(tr)
        previous_close = candle.close
    initial = statistics.fmean(trs[:period])
    out = [math.nan] * period + [initial]
    current = initial
    for tr in trs[period:]:
        current = ((current * (period - 1)) + tr) / period
        out.append(current)
    return out


def median_volume_ratio(candles: list[Candle], lookback: int = 20) -> float:
    if len(candles) <= lookback:
        raise ValueError("Not enough candles for volume ratio.")
    baseline = statistics.median(c.volume for c in candles[-(lookback + 1):-1])
    if baseline <= 0:
        return 0.0
    return candles[-1].volume / baseline


def taker_buy_ratio(candles: list[Candle], lookback: int = 20) -> float:
    rows = candles[-lookback:]
    total = sum(c.quote_volume for c in rows)
    if total <= 0:
        return 0.5
    return sum(c.taker_buy_quote for c in rows) / total
