"""Binance-specialist market analysis and paper-trading tools for Jarvis.

This module deliberately uses public Binance market-data endpoints only. It does
not contain live-account credentials or live order execution. The paper ledger
is local and separate from Binance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import sqlite3
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

SPOT_BASES = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)
FUTURES_BASES = ("https://fapi.binance.com",)
STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
PAPER_DB = STATE_DIR / "binance-paper.sqlite3"

INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


class BinanceError(RuntimeError):
    pass


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float
    trades: int
    taker_buy_base: float
    taker_buy_quote: float


def _url_json(
    bases: Iterable[str],
    path: str,
    params: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> Any:
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None}
    )
    suffix = path + (("?" + query) if query else "")
    last_error: Exception | None = None
    for base in bases:
        url = base.rstrip("/") + suffix
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "codex-jarvis-binance/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and "code" in payload and "msg" in payload:
                raise BinanceError(f"Binance API error {payload['code']}: {payload['msg']}")
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, BinanceError) as exc:
            last_error = exc
    raise BinanceError(f"Binance market-data request failed: {last_error}")


def _prefix(market: str) -> str:
    return "/api/v3" if market == "spot" else "/fapi/v1"


def _bases(market: str) -> tuple[str, ...]:
    return SPOT_BASES if market == "spot" else FUTURES_BASES


def _symbol(value: str) -> str:
    symbol = "".join(ch for ch in value.upper() if ch.isalnum())
    if not symbol:
        raise ValueError("Symbol is empty.")
    return symbol


def klines(symbol: str, interval: str, market: str = "spot", limit: int = 250) -> list[Candle]:
    if interval not in INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")
    limit = max(50, min(int(limit), 1000))
    raw = _url_json(
        _bases(market),
        _prefix(market) + "/klines",
        {"symbol": _symbol(symbol), "interval": interval, "limit": limit},
    )
    out: list[Candle] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 11:
            continue
        out.append(
            Candle(
                open_time=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                close_time=int(row[6]),
                quote_volume=float(row[7]),
                trades=int(row[8]),
                taker_buy_base=float(row[9]),
                taker_buy_quote=float(row[10]),
            )
        )
    if len(out) < 50:
        raise BinanceError("Not enough candle data returned for analysis.")
    return out


def ticker(symbol: str, market: str = "spot") -> dict[str, Any]:
    data = _url_json(
        _bases(market),
        _prefix(market) + "/ticker/24hr",
        {"symbol": _symbol(symbol)},
    )
    return {
        "symbol": data.get("symbol", _symbol(symbol)),
        "last_price": float(data.get("lastPrice", 0) or 0),
        "change_percent_24h": float(data.get("priceChangePercent", 0) or 0),
        "high_24h": float(data.get("highPrice", 0) or 0),
        "low_24h": float(data.get("lowPrice", 0) or 0),
        "base_volume_24h": float(data.get("volume", 0) or 0),
        "quote_volume_24h": float(data.get("quoteVolume", 0) or 0),
        "trades_24h": int(data.get("count", 0) or 0),
    }


def depth(symbol: str, market: str = "spot", limit: int = 20) -> dict[str, Any]:
    raw = _url_json(
        _bases(market),
        _prefix(market) + "/depth",
        {"symbol": _symbol(symbol), "limit": max(5, min(limit, 100))},
    )
    bids = [(float(p), float(q)) for p, q, *_ in raw.get("bids", [])]
    asks = [(float(p), float(q)) for p, q, *_ in raw.get("asks", [])]
    bid_notional = sum(p * q for p, q in bids)
    ask_notional = sum(p * q for p, q in asks)
    total = bid_notional + ask_notional
    imbalance = (bid_notional - ask_notional) / total if total else 0.0
    best_bid = bids[0][0] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread_bps = ((best_ask - best_bid) / mid * 10000) if mid else 0.0
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
        "bid_notional": bid_notional,
        "ask_notional": ask_notional,
        "imbalance": imbalance,
    }


def futures_context(symbol: str) -> dict[str, Any]:
    sym = _symbol(symbol)
    out: dict[str, Any] = {}
    try:
        premium = _url_json(
            FUTURES_BASES,
            "/fapi/v1/premiumIndex",
            {"symbol": sym},
        )
        out.update(
            {
                "mark_price": float(premium.get("markPrice", 0) or 0),
                "index_price": float(premium.get("indexPrice", 0) or 0),
                "last_funding_rate": float(premium.get("lastFundingRate", 0) or 0),
                "next_funding_time": int(premium.get("nextFundingTime", 0) or 0),
            }
        )
    except Exception:
        pass
    try:
        oi = _url_json(
            FUTURES_BASES,
            "/fapi/v1/openInterest",
            {"symbol": sym},
        )
        out["open_interest"] = float(oi.get("openInterest", 0) or 0)
    except Exception:
        pass
    return out


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return math.nan
    gains: list[float] = []
    losses: list[float] = []
    for a, b in zip(values[-(period + 1):-1], values[-period:]):
        change = b - a
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) <= period:
        return math.nan
    trs: list[float] = []
    previous = candles[-(period + 1)].close
    for candle in candles[-period:]:
        trs.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous),
                abs(candle.low - previous),
            )
        )
        previous = candle.close
    return sum(trs) / len(trs)


def _bollinger(values: list[float], period: int = 20, deviations: float = 2.0) -> tuple[float, float, float]:
    window = values[-period:]
    mid = statistics.fmean(window)
    std = statistics.pstdev(window)
    return mid - deviations * std, mid, mid + deviations * std


def _vwap(candles: list[Candle], lookback: int = 50) -> float:
    rows = candles[-lookback:]
    total_volume = sum(c.volume for c in rows)
    if not total_volume:
        return rows[-1].close
    return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in rows) / total_volume


def _levels(candles: list[Candle], price: float, lookback: int = 80) -> tuple[list[float], list[float]]:
    rows = candles[-lookback:]
    swing_lows: list[float] = []
    swing_highs: list[float] = []
    for i in range(2, len(rows) - 2):
        group = rows[i - 2:i + 3]
        if rows[i].low == min(x.low for x in group):
            swing_lows.append(rows[i].low)
        if rows[i].high == max(x.high for x in group):
            swing_highs.append(rows[i].high)

    def unique_near(values: list[float], reverse: bool = False) -> list[float]:
        ordered = sorted(set(values), reverse=reverse)
        selected: list[float] = []
        for value in ordered:
            if not selected or all(abs(value - x) / max(price, 1e-12) > 0.002 for x in selected):
                selected.append(value)
            if len(selected) == 3:
                break
        return selected

    supports = unique_near([x for x in swing_lows if x < price], reverse=True)
    resistances = unique_near([x for x in swing_highs if x > price])
    if not supports:
        supports = [min(x.low for x in rows)]
    if not resistances:
        resistances = [max(x.high for x in rows)]
    return supports, resistances


def analyze(symbol: str, interval: str = "15m", market: str = "spot", limit: int = 250) -> dict[str, Any]:
    candles = klines(symbol, interval, market, limit)
    closes = [c.close for c in candles]
    price = closes[-1]
    ema20 = _ema_series(closes, 20)[-1]
    ema50 = _ema_series(closes, 50)[-1]
    ema200 = _ema_series(closes, 200)[-1] if len(closes) >= 200 else _ema_series(closes, min(len(closes), 100))[-1]
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(ema12, ema26)]
    macd = macd_series[-1]
    signal = _ema_series(macd_series, 9)[-1]
    macd_hist = macd - signal
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(candles, 14)
    bb_low, bb_mid, bb_high = _bollinger(closes, 20)
    vwap50 = _vwap(candles, 50)
    roc10 = ((price / closes[-11]) - 1.0) * 100 if len(closes) >= 11 else 0.0

    recent = candles[-20:]
    quote_total = sum(c.quote_volume for c in recent)
    taker_buy = sum(c.taker_buy_quote for c in recent)
    buy_flow = taker_buy / quote_total if quote_total else 0.5

    previous_volumes = [c.volume for c in candles[-21:-1]]
    volume_ratio = candles[-1].volume / statistics.fmean(previous_volumes) if previous_volumes and statistics.fmean(previous_volumes) else 0.0

    book = depth(symbol, market, 20)
    supports, resistances = _levels(candles, price)
    t = ticker(symbol, market)

    if price > ema20 > ema50:
        trend = "bullish"
    elif price < ema20 < ema50:
        trend = "bearish"
    else:
        trend = "mixed"

    score = 0
    score += 25 if trend == "bullish" else -25 if trend == "bearish" else 0
    score += 15 if macd_hist > 0 else -15
    score += 10 if price > vwap50 else -10
    score += 10 if buy_flow > 0.53 else -10 if buy_flow < 0.47 else 0
    score += 10 if book["imbalance"] > 0.10 else -10 if book["imbalance"] < -0.10 else 0
    score += 10 if roc10 > 0.5 else -10 if roc10 < -0.5 else 0
    if rsi14 >= 70:
        score -= 8
    elif rsi14 <= 30:
        score += 8
    score = max(-100, min(100, score))
    bias = "bullish" if score >= 25 else "bearish" if score <= -25 else "neutral/mixed"

    result: dict[str, Any] = {
        "symbol": _symbol(symbol),
        "market": market,
        "interval": interval,
        "price": price,
        "bias": bias,
        "technical_bias_score": score,
        "trend": trend,
        "rsi14": rsi14,
        "ema20": ema20,
        "ema50": ema50,
        "ema200_reference": ema200,
        "macd": macd,
        "macd_signal": signal,
        "macd_histogram": macd_hist,
        "atr14": atr14,
        "atr_percent": (atr14 / price * 100.0) if price else 0.0,
        "bollinger_low": bb_low,
        "bollinger_mid": bb_mid,
        "bollinger_high": bb_high,
        "vwap50": vwap50,
        "roc10_percent": roc10,
        "volume_ratio": volume_ratio,
        "taker_buy_flow_ratio": buy_flow,
        "order_book_imbalance": book["imbalance"],
        "spread_bps": book["spread_bps"],
        "supports": supports,
        "resistances": resistances,
        "change_percent_24h": t["change_percent_24h"],
        "high_24h": t["high_24h"],
        "low_24h": t["low_24h"],
        "quote_volume_24h": t["quote_volume_24h"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if market == "futures":
        result["futures"] = futures_context(symbol)
    return result


def _fmt_price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:,.4f}"
    return f"{value:.8f}"


def format_analysis(data: dict[str, Any]) -> str:
    lines = [
        f"{data['symbol']} {data['market']} {data['interval']}",
        f"Price: {_fmt_price(data['price'])}",
        f"Bias: {data['bias']} ({data['technical_bias_score']:+d}/100), trend={data['trend']}",
        f"RSI14: {data['rsi14']:.1f} | MACD hist: {data['macd_histogram']:.6g}",
        f"EMA20/50: {_fmt_price(data['ema20'])} / {_fmt_price(data['ema50'])}",
        f"VWAP50: {_fmt_price(data['vwap50'])} | ATR: {data['atr_percent']:.2f}%",
        f"10-bar momentum: {data['roc10_percent']:+.2f}% | volume ratio: {data['volume_ratio']:.2f}x",
        f"Buy flow: {data['taker_buy_flow_ratio']*100:.1f}% | book imbalance: {data['order_book_imbalance']:+.2f}",
        f"Spread: {data['spread_bps']:.2f} bps",
        "Support: " + ", ".join(_fmt_price(x) for x in data["supports"]),
        "Resistance: " + ", ".join(_fmt_price(x) for x in data["resistances"]),
        f"24h: {data['change_percent_24h']:+.2f}% | high {_fmt_price(data['high_24h'])} | low {_fmt_price(data['low_24h'])}",
    ]
    future = data.get("futures") or {}
    if future:
        if "mark_price" in future:
            lines.append(
                f"Futures mark: {_fmt_price(future['mark_price'])} | funding: {future.get('last_funding_rate', 0)*100:+.4f}%"
            )
        if "open_interest" in future:
            lines.append(f"Open interest: {future['open_interest']:,.4f}")
    return "\n".join(lines)


def multi_analysis(symbol: str, intervals: list[str], market: str) -> list[dict[str, Any]]:
    return [analyze(symbol, interval, market) for interval in intervals]


def _paper_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError:
        pass
    conn = sqlite3.connect(PAPER_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            leverage REAL NOT NULL DEFAULT 1,
            entry REAL NOT NULL,
            exit REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            pnl REAL,
            return_on_margin REAL,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open'
        )
        """
    )
    conn.commit()
    try:
        os.chmod(PAPER_DB, 0o600)
    except OSError:
        pass
    return conn


def paper_open(
    symbol: str,
    side: str,
    quantity: float,
    market: str,
    leverage: float = 1.0,
    entry: float | None = None,
    note: str = "",
) -> int:
    side = side.lower()
    if side not in {"long", "short"}:
        raise ValueError("Side must be long or short.")
    if market == "spot" and side == "short":
        raise ValueError("Spot paper mode does not open short positions; use --market futures.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    if leverage < 1 or leverage > 125:
        raise ValueError("Paper leverage must be between 1 and 125.")
    px = float(entry) if entry is not None else ticker(symbol, market)["last_price"]
    conn = _paper_db()
    cur = conn.execute(
        """
        INSERT INTO paper_trades(
          symbol,market,side,quantity,leverage,entry,opened_at,note,status
        ) VALUES(?,?,?,?,?,?,?,?, 'open')
        """,
        (
            _symbol(symbol),
            market,
            side,
            quantity,
            leverage,
            px,
            datetime.now(timezone.utc).isoformat(),
            note[:4000],
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def paper_close(trade_id: int, exit_price: float | None = None) -> dict[str, Any]:
    conn = _paper_db()
    row = conn.execute(
        "SELECT * FROM paper_trades WHERE id=?",
        (trade_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown paper trade #{trade_id}.")
    if row["status"] != "open":
        raise ValueError(f"Paper trade #{trade_id} is already closed.")

    px = float(exit_price) if exit_price is not None else ticker(row["symbol"], row["market"])["last_price"]
    direction = 1.0 if row["side"] == "long" else -1.0
    pnl = (px - float(row["entry"])) * float(row["quantity"]) * direction
    notional = float(row["entry"]) * float(row["quantity"])
    margin = notional / max(float(row["leverage"]), 1.0)
    rom = (pnl / margin * 100.0) if margin else 0.0
    closed = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE paper_trades
           SET exit=?, closed_at=?, pnl=?, return_on_margin=?, status='closed'
         WHERE id=?
        """,
        (px, closed, pnl, rom, trade_id),
    )
    conn.commit()
    return {
        "id": trade_id,
        "symbol": row["symbol"],
        "side": row["side"],
        "entry": float(row["entry"]),
        "exit": px,
        "quantity": float(row["quantity"]),
        "leverage": float(row["leverage"]),
        "pnl_quote": pnl,
        "return_on_margin_percent": rom,
    }


def paper_rows(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = _paper_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def paper_stats() -> dict[str, Any]:
    conn = _paper_db()
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE status='closed' ORDER BY id"
    ).fetchall()
    if not rows:
        return {
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_percent": None,
            "net_pnl_quote": 0.0,
            "profit_factor": None,
            "average_return_on_margin_percent": None,
        }
    pnls = [float(r["pnl"] or 0) for r in rows]
    returns = [float(r["return_on_margin"] or 0) for r in rows]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "closed_trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_percent": len(wins) / len(rows) * 100.0,
        "net_pnl_quote": sum(pnls),
        "profit_factor": (gross_win / gross_loss) if gross_loss else None,
        "average_return_on_margin_percent": statistics.fmean(returns),
    }


def risk_calc(entry: float, stop: float, account: float, risk_percent: float, leverage: float = 1.0) -> dict[str, float]:
    if entry <= 0 or stop <= 0 or account <= 0:
        raise ValueError("Entry, stop and account values must be positive.")
    if not 0 < risk_percent <= 100:
        raise ValueError("Risk percent must be above 0 and at most 100.")
    if leverage < 1:
        raise ValueError("Leverage must be at least 1.")
    distance = abs(entry - stop)
    if not distance:
        raise ValueError("Entry and stop cannot be identical.")
    risk_amount = account * risk_percent / 100.0
    quantity = risk_amount / distance
    notional = quantity * entry
    return {
        "risk_amount": risk_amount,
        "stop_distance": distance,
        "stop_distance_percent": distance / entry * 100.0,
        "quantity": quantity,
        "notional": notional,
        "estimated_margin": notional / leverage,
        "leverage": leverage,
    }


def cmd_quote(args) -> int:
    data = ticker(args.symbol, args.market)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(
            f"{data['symbol']} {args.market}: {_fmt_price(data['last_price'])} "
            f"24h={data['change_percent_24h']:+.2f}% "
            f"H={_fmt_price(data['high_24h'])} L={_fmt_price(data['low_24h'])}"
        )
    return 0


def cmd_analyze(args) -> int:
    data = analyze(args.symbol, args.interval, args.market, args.limit)
    print(json.dumps(data, indent=2) if args.json else format_analysis(data))
    return 0


def cmd_multi(args) -> int:
    intervals = [x.strip() for x in args.intervals.split(",") if x.strip()]
    if not intervals:
        raise ValueError("At least one interval is required.")
    rows = multi_analysis(args.symbol, intervals, args.market)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for i, row in enumerate(rows):
            if i:
                print("\n" + "-" * 48)
            print(format_analysis(row))
    return 0


def cmd_risk(args) -> int:
    data = risk_calc(args.entry, args.stop, args.account, args.risk_percent, args.leverage)
    print(json.dumps(data, indent=2))
    return 0


def cmd_paper_open(args) -> int:
    trade_id = paper_open(
        args.symbol,
        args.side,
        args.quantity,
        args.market,
        args.leverage,
        args.entry,
        args.note or "",
    )
    row = paper_rows("open", 100)
    trade = next(r for r in row if r["id"] == trade_id)
    print(
        f"Paper trade #{trade_id} opened: {trade['side'].upper()} "
        f"{trade['quantity']} {trade['symbol']} @ {_fmt_price(trade['entry'])} "
        f"({trade['market']}, {trade['leverage']}x)"
    )
    return 0


def cmd_paper_close(args) -> int:
    print(json.dumps(paper_close(args.id, args.exit), indent=2))
    return 0


def cmd_positions(args) -> int:
    rows = paper_rows("open", args.limit)
    if not rows:
        print("No open paper positions.")
        return 0
    for row in rows:
        print(
            f"#{row['id']} {row['symbol']} {row['side'].upper()} "
            f"qty={row['quantity']} entry={_fmt_price(row['entry'])} "
            f"{row['leverage']}x {row['market']}"
        )
    return 0


def cmd_history(args) -> int:
    rows = paper_rows(None, args.limit)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_stats(_args) -> int:
    print(json.dumps(paper_stats(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis-binance",
        description="Binance market analysis and local paper-trading lab for Jarvis.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def market_args(s):
        s.add_argument("symbol")
        s.add_argument("--market", choices=["spot", "futures"], default="spot")

    s = sub.add_parser("quote")
    market_args(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("analyze")
    market_args(s)
    s.add_argument("--interval", default="15m", choices=sorted(INTERVALS))
    s.add_argument("--limit", type=int, default=250)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("multi")
    market_args(s)
    s.add_argument("--intervals", default="15m,1h,4h")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_multi)

    s = sub.add_parser("risk")
    s.add_argument("--entry", type=float, required=True)
    s.add_argument("--stop", type=float, required=True)
    s.add_argument("--account", type=float, required=True)
    s.add_argument("--risk-percent", type=float, required=True)
    s.add_argument("--leverage", type=float, default=1.0)
    s.set_defaults(func=cmd_risk)

    s = sub.add_parser("paper-open")
    market_args(s)
    s.add_argument("--side", choices=["long", "short"], required=True)
    s.add_argument("--quantity", type=float, required=True)
    s.add_argument("--leverage", type=float, default=1.0)
    s.add_argument("--entry", type=float)
    s.add_argument("--note")
    s.set_defaults(func=cmd_paper_open)

    s = sub.add_parser("paper-close")
    s.add_argument("id", type=int)
    s.add_argument("--exit", type=float)
    s.set_defaults(func=cmd_paper_close)

    s = sub.add_parser("positions")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_positions)

    s = sub.add_parser("history")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("stats")
    s.set_defaults(func=cmd_stats)

    return p


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"Jarvis Binance error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
