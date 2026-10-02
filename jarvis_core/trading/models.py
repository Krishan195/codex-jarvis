"""Shared trading data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class BookSnapshot:
    best_bid: float
    best_ask: float
    spread_bps: float
    bid_notional: float
    ask_notional: float
    imbalance: float
    observed_at_ms: int


@dataclass(frozen=True)
class FuturesContext:
    mark_price: float | None = None
    index_price: float | None = None
    last_funding_rate: float | None = None
    next_funding_time: int | None = None
    open_interest: float | None = None


@dataclass
class TradeProposal:
    decision: str
    symbol: str
    market_type: str
    direction: str | None
    strategy_version: str
    signal_timestamp: int
    expires_at: int
    order_type: str
    entry_reference: float | None
    stop_loss: float | None
    take_profit: float | None
    leverage: float
    quantity: float = 0.0
    estimated_loss_at_stop: float = 0.0
    estimated_entry_fee: float = 0.0
    estimated_exit_fee: float = 0.0
    estimated_slippage: float = 0.0
    estimated_funding: float = 0.0
    evidence: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    data_health: dict[str, Any] = field(default_factory=dict)
    risk_rejections: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "direction": self.direction,
            "strategy_version": self.strategy_version,
            "signal_timestamp": self.signal_timestamp,
            "expires_at": self.expires_at,
            "order_type": self.order_type,
            "entry_reference": self.entry_reference,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "leverage": self.leverage,
            "quantity": self.quantity,
            "estimated_loss_at_stop": self.estimated_loss_at_stop,
            "estimated_entry_fee": self.estimated_entry_fee,
            "estimated_exit_fee": self.estimated_exit_fee,
            "estimated_slippage": self.estimated_slippage,
            "estimated_funding": self.estimated_funding,
            "evidence": self.evidence,
            "failure_reasons": self.failure_reasons,
            "data_health": self.data_health,
            "risk_rejections": self.risk_rejections,
        }
