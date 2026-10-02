"""Orchestrator for market analysis, risk validation and paper execution."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from .config import TradingConfig, load_config, write_config
from .journal import TradingJournal
from .market_data import BinancePublicMarketData, MarketDataError
from .models import TradeProposal
from .paper import PaperBroker
from .risk import AccountState, apply_risk
from .strategy import generate_proposal


class TradingEngine:
    def __init__(
        self,
        cfg: TradingConfig | None = None,
        *,
        journal: TradingJournal | None = None,
    ):
        self.cfg = cfg or load_config()
        self.cfg.validate()
        self.journal = journal or TradingJournal()
        self.market = BinancePublicMarketData(
            self.cfg.market_type,
            stale_grace_seconds=self.cfg.stale_grace_seconds,
        )
        self.paper = PaperBroker(self.cfg, self.journal, self.market)

    def health(self) -> dict[str, Any]:
        local_ms = int(time.time() * 1000)
        server_ms = self.market.server_time_ms()
        symbols: dict[str, Any] = {}
        for symbol in self.cfg.permitted_symbols:
            rules = self.market.symbol_rules(symbol)
            symbols[symbol] = {
                "status": rules["status"],
                "tick_size": rules["tick_size"],
                "step_size": rules["step_size"],
                "min_qty": rules["min_qty"],
                "min_notional": rules["min_notional"],
                "order_types": rules["order_types"],
            }
        return {
            "mode": self.cfg.mode,
            "enabled": self.cfg.enabled,
            "paused": self.cfg.paused,
            "market_type": self.cfg.market_type,
            "strategy_version": self.cfg.strategy.version,
            "clock_skew_ms": local_ms - server_ms,
            "symbols": symbols,
        }

    def _unrealized(self) -> float:
        total = 0.0
        for pos in self.journal.open_positions():
            book = self.market.depth(pos["symbol"], limit=10)
            exit_reference = (
                book.best_bid if pos["direction"] == "LONG" else book.best_ask
            )
            sign = 1.0 if pos["direction"] == "LONG" else -1.0
            gross = (
                exit_reference - float(pos["entry"])
            ) * float(pos["quantity"]) * sign
            estimated_exit_fee = (
                exit_reference
                * float(pos["quantity"])
                * self.cfg.costs.taker_fee_bps
                / 10_000.0
            )
            total += gross - float(pos["entry_fees"]) - estimated_exit_fee
        return total

    def account_state(self) -> AccountState:
        capital = self.cfg.risk.allocated_capital_quote
        realized = self.journal.realized_pnl_total()
        realized_equity = capital + realized
        unrealized = self._unrealized()
        return AccountState(
            open_positions=len(self.journal.open_positions()),
            aggregate_notional=self.journal.aggregate_open_notional(),
            realized_pnl_today=self.journal.realized_pnl_today(),
            current_equity=realized_equity + unrealized,
            peak_equity=self.journal.peak_realized_equity(capital),
            state_certain=True,
        )

    def proposal(self, symbol: str) -> TradeProposal:
        symbol = symbol.upper()
        if symbol not in self.cfg.permitted_symbols:
            raise ValueError(f"{symbol} is not in permitted_symbols.")

        server_time = self.market.server_time_ms()
        signal = self.market.klines(
            symbol, self.cfg.strategy.signal_interval, limit=250
        )
        h1 = self.market.klines(symbol, "1h", limit=250)
        h4 = self.market.klines(symbol, "4h", limit=250)
        book = self.market.depth(symbol, limit=20)
        recent = self.market.recent_trades(symbol, limit=100)
        if server_time - int(recent[-1]["time"]) > 120_000:
            raise MarketDataError("Recent trade feed is stale.")

        futures = self.market.futures_context(symbol)
        proposal = generate_proposal(
            symbol=symbol,
            market_type=self.cfg.market_type,
            signal_candles=signal,
            context_1h=h1,
            context_4h=h4,
            book=book,
            futures=futures,
            server_time_ms=server_time,
            cfg=self.cfg.strategy,
            leverage=self.cfg.risk.max_leverage,
        )
        proposal.data_health["recent_trade_time"] = int(recent[-1]["time"])

        if proposal.decision == "TRADE":
            rules = self.market.symbol_rules(symbol)
            if rules["status"] != "TRADING":
                proposal.decision = "WAIT"
                proposal.risk_rejections.append(
                    f"symbol status is {rules['status']!r}, not TRADING"
                )
                return proposal

            required_order = "MARKET"
            if required_order not in rules["order_types"]:
                proposal.decision = "WAIT"
                proposal.risk_rejections.append(
                    f"{required_order} order type is not supported"
                )
                return proposal

            proposal = apply_risk(
                proposal,
                self.cfg,
                self.account_state(),
                step_size=rules["step_size"],
                min_qty=rules["min_qty"],
                max_qty=rules["max_qty"],
                min_notional=rules["min_notional"],
                funding_rate=futures.last_funding_rate,
            )
        return proposal

    def scan(self) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []
        for symbol in self.cfg.permitted_symbols:
            try:
                proposals.append(self.proposal(symbol))
            except Exception as exc:
                p = TradeProposal(
                    decision="WAIT",
                    symbol=symbol,
                    market_type=self.cfg.market_type,
                    direction=None,
                    strategy_version=self.cfg.strategy.version,
                    signal_timestamp=0,
                    expires_at=0,
                    order_type="NONE",
                    entry_reference=None,
                    stop_loss=None,
                    take_profit=None,
                    leverage=self.cfg.risk.max_leverage,
                    failure_reasons=[f"market/data failure: {exc}"],
                    data_health={"healthy": False},
                )
                proposals.append(p)
        return proposals

    def cycle(self) -> dict[str, Any]:
        exits = self.paper.manage()
        opened: list[dict[str, Any]] = []
        proposals: list[dict[str, Any]] = []

        if not self.cfg.enabled or self.cfg.paused:
            return {
                "managed_exits": exits,
                "opened": [],
                "proposals": [],
                "new_entries": "disabled" if not self.cfg.enabled else "paused",
            }

        for symbol in self.cfg.permitted_symbols:
            if self.journal.open_position_for_symbol(symbol):
                continue
            proposal = self.proposal(symbol)
            is_new = self.journal.record_signal(proposal)
            proposals.append(proposal.as_dict())
            if proposal.decision == "TRADE" and is_new:
                opened.append(self.paper.open(proposal))
            elif proposal.decision == "WAIT":
                self.journal.event(
                    "TRADE_REJECTED_OR_WAIT",
                    proposal.as_dict(),
                    symbol,
                )

        return {
            "managed_exits": exits,
            "opened": opened,
            "proposals": proposals,
            "new_entries": "eligible",
        }

    def set_enabled(self, value: bool) -> None:
        self.cfg.enabled = bool(value)
        write_config(self.cfg)
        self.journal.event(
            "CONFIG_CHANGE",
            {"enabled": self.cfg.enabled, "mode": self.cfg.mode},
        )

    def set_paused(self, value: bool) -> None:
        self.cfg.paused = bool(value)
        write_config(self.cfg)
        self.journal.event(
            "CONFIG_CHANGE",
            {"paused": self.cfg.paused, "mode": self.cfg.mode},
        )

    def status(self) -> dict[str, Any]:
        state = self.account_state()
        performance = self.journal.performance(
            self.cfg.risk.allocated_capital_quote
        )
        return {
            "mode": self.cfg.mode,
            "enabled": self.cfg.enabled,
            "paused": self.cfg.paused,
            "market_type": self.cfg.market_type,
            "symbols": self.cfg.permitted_symbols,
            "strategy": self.cfg.strategy.version,
            "open_positions": self.journal.open_positions(),
            "aggregate_open_notional": state.aggregate_notional,
            "current_equity_estimate": state.current_equity,
            "realized_pnl_today": state.realized_pnl_today,
            "performance": performance,
        }
