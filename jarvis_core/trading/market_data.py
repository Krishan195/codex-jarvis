"""Public Binance market-data adapters with strict candle validation."""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import BookSnapshot, Candle, FuturesContext

SPOT_BASE = "https://api.binance.com"
SPOT_DATA_BASE = "https://data-api.binance.vision"
FUTURES_BASE = "https://fapi.binance.com"

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


class MarketDataError(RuntimeError):
    pass


def _request_json(
    bases: tuple[str, ...],
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 8.0,
) -> Any:
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None}
    )
    suffix = path + (("?" + query) if query else "")
    last_error: Exception | None = None
    for base in bases:
        req = urllib.request.Request(
            base.rstrip("/") + suffix,
            headers={
                "User-Agent": "codex-jarvis-trader/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and "code" in data and "msg" in data:
                raise MarketDataError(
                    f"Binance API error {data['code']}: {data['msg']}"
                )
            return data
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            MarketDataError,
        ) as exc:
            last_error = exc
    raise MarketDataError(f"Binance request failed: {last_error}")


def _symbol(value: str) -> str:
    out = "".join(ch for ch in value.upper() if ch.isalnum())
    if not out:
        raise ValueError("Symbol is empty.")
    return out


def _parse_klines(raw: list[Any]) -> list[Candle]:
    candles: list[Candle] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 11:
            raise MarketDataError("Malformed Binance kline row.")
        candles.append(
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
    return candles


def validate_completed_candles(
    candles: list[Candle],
    interval: str,
    server_time_ms: int,
    *,
    stale_grace_seconds: int = 120,
) -> list[Candle]:
    if interval not in INTERVAL_MS:
        raise MarketDataError(f"Unsupported interval for validation: {interval}")
    complete = [c for c in candles if c.close_time < server_time_ms]
    if len(complete) < 60:
        raise MarketDataError(
            f"Insufficient completed {interval} candles: {len(complete)}"
        )
    step = INTERVAL_MS[interval]
    for prev, cur in zip(complete, complete[1:]):
        if cur.open_time <= prev.open_time:
            raise MarketDataError("Klines are out of order.")
        if cur.open_time - prev.open_time != step:
            raise MarketDataError(
                f"Missing/inconsistent {interval} kline between "
                f"{prev.open_time} and {cur.open_time}."
            )
    age = server_time_ms - complete[-1].close_time
    max_age = step + int(stale_grace_seconds) * 1000
    if age < 0 or age > max_age:
        raise MarketDataError(
            f"Latest completed {interval} candle is stale: age={age}ms."
        )
    return complete


class BinancePublicMarketData:
    def __init__(self, market_type: str, *, stale_grace_seconds: int = 120):
        if market_type not in {"spot", "futures"}:
            raise ValueError("market_type must be spot or futures")
        self.market_type = market_type
        self.stale_grace_seconds = int(stale_grace_seconds)

    @property
    def bases(self) -> tuple[str, ...]:
        if self.market_type == "spot":
            return (SPOT_DATA_BASE, SPOT_BASE)
        return (FUTURES_BASE,)

    @property
    def prefix(self) -> str:
        return "/api/v3" if self.market_type == "spot" else "/fapi/v1"

    def server_time_ms(self) -> int:
        data = _request_json(self.bases, self.prefix + "/time")
        return int(data["serverTime"])

    def exchange_info(self, symbol: str) -> dict[str, Any]:
        data = _request_json(
            self.bases,
            self.prefix + "/exchangeInfo",
            {"symbol": _symbol(symbol)},
        )
        rows = data.get("symbols") or []
        if not rows:
            raise MarketDataError(f"Symbol not found: {symbol}")
        return rows[0]

    def klines(self, symbol: str, interval: str, *, limit: int = 250) -> list[Candle]:
        if interval not in INTERVAL_MS:
            raise MarketDataError(f"Unsupported interval: {interval}")
        raw = _request_json(
            self.bases,
            self.prefix + "/klines",
            {
                "symbol": _symbol(symbol),
                "interval": interval,
                "limit": max(60, min(int(limit), 1000)),
            },
        )
        server_time = self.server_time_ms()
        return validate_completed_candles(
            _parse_klines(raw),
            interval,
            server_time,
            stale_grace_seconds=self.stale_grace_seconds,
        )

    def depth(self, symbol: str, *, limit: int = 20) -> BookSnapshot:
        observed = self.server_time_ms()
        raw = _request_json(
            self.bases,
            self.prefix + "/depth",
            {"symbol": _symbol(symbol), "limit": max(5, min(int(limit), 100))},
        )
        bids = [(float(p), float(q)) for p, q, *_ in raw.get("bids", [])]
        asks = [(float(p), float(q)) for p, q, *_ in raw.get("asks", [])]
        if not bids or not asks:
            raise MarketDataError("Order book is empty.")
        if bids[0][0] >= asks[0][0]:
            raise MarketDataError("Order book is crossed/inconsistent.")
        bid_notional = sum(p * q for p, q in bids)
        ask_notional = sum(p * q for p, q in asks)
        total = bid_notional + ask_notional
        mid = (bids[0][0] + asks[0][0]) / 2.0
        return BookSnapshot(
            best_bid=bids[0][0],
            best_ask=asks[0][0],
            spread_bps=((asks[0][0] - bids[0][0]) / mid) * 10_000.0,
            bid_notional=bid_notional,
            ask_notional=ask_notional,
            imbalance=((bid_notional - ask_notional) / total) if total else 0.0,
            observed_at_ms=observed,
        )

    def recent_trades(self, symbol: str, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = _request_json(
            self.bases,
            self.prefix + "/trades",
            {"symbol": _symbol(symbol), "limit": max(1, min(int(limit), 1000))},
        )
        rows: list[dict[str, Any]] = []
        for item in raw:
            rows.append(
                {
                    "price": float(item["price"]),
                    "qty": float(item["qty"]),
                    "time": int(item["time"]),
                    "is_buyer_maker": bool(item.get("isBuyerMaker", False)),
                }
            )
        if not rows:
            raise MarketDataError("No recent trades returned.")
        return rows

    def futures_context(self, symbol: str) -> FuturesContext:
        if self.market_type != "futures":
            return FuturesContext()
        sym = _symbol(symbol)
        premium = _request_json(
            (FUTURES_BASE,),
            "/fapi/v1/premiumIndex",
            {"symbol": sym},
        )
        oi = _request_json(
            (FUTURES_BASE,),
            "/fapi/v1/openInterest",
            {"symbol": sym},
        )
        return FuturesContext(
            mark_price=float(premium.get("markPrice") or 0) or None,
            index_price=float(premium.get("indexPrice") or 0) or None,
            last_funding_rate=float(premium.get("lastFundingRate") or 0),
            next_funding_time=int(premium.get("nextFundingTime") or 0) or None,
            open_interest=float(oi.get("openInterest") or 0) or None,
        )

    def symbol_rules(self, symbol: str) -> dict[str, Any]:
        info = self.exchange_info(symbol)
        filters = {
            row.get("filterType"): row
            for row in info.get("filters", [])
            if isinstance(row, dict) and row.get("filterType")
        }
        lot = filters.get("LOT_SIZE") or {}
        market_lot = filters.get("MARKET_LOT_SIZE") or lot
        price = filters.get("PRICE_FILTER") or {}
        minimum = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        return {
            "symbol": info.get("symbol"),
            "status": info.get("status"),
            "order_types": list(info.get("orderTypes") or []),
            "tick_size": float(price.get("tickSize") or 0),
            "min_qty": float(market_lot.get("minQty") or lot.get("minQty") or 0),
            "max_qty": float(market_lot.get("maxQty") or lot.get("maxQty") or 0),
            "step_size": float(market_lot.get("stepSize") or lot.get("stepSize") or 0),
            "min_notional": float(
                minimum.get("notional")
                or minimum.get("minNotional")
                or 0
            ),
        }
