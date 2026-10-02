"""Local paper execution and protective position management."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import TradingConfig
from .journal import TradingJournal
from .market_data import BinancePublicMarketData
from .models import TradeProposal


def _iso_to_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


class PaperBroker:
    def __init__(
        self,
        cfg: TradingConfig,
        journal: TradingJournal,
        market: BinancePublicMarketData,
    ):
        self.cfg = cfg
        self.journal = journal
        self.market = market

    @property
    def fee_rate(self) -> float:
        return self.cfg.costs.taker_fee_bps / 10_000.0

    @property
    def slip_rate(self) -> float:
        return self.cfg.costs.slippage_bps / 10_000.0

    def open(self, proposal: TradeProposal) -> dict[str, Any]:
        if proposal.decision != "TRADE" or not proposal.direction:
            raise ValueError("Only an approved TRADE proposal can be paper-executed.")
        if proposal.quantity <= 0:
            raise ValueError("Proposal has no risk-approved quantity.")

        reference = float(proposal.entry_reference or 0)
        if proposal.direction == "LONG":
            fill = reference * (1.0 + self.slip_rate)
        else:
            fill = reference * (1.0 - self.slip_rate)
        fee = fill * proposal.quantity * self.fee_rate

        client_id = (
            f"paper-{proposal.strategy_version}-{proposal.symbol}-"
            f"{proposal.signal_timestamp}-{proposal.direction.lower()}"
        )
        opened_at = datetime.now(timezone.utc).isoformat()
        row = {
            "client_id": client_id,
            "symbol": proposal.symbol,
            "market_type": proposal.market_type,
            "strategy_version": proposal.strategy_version,
            "direction": proposal.direction,
            "quantity": proposal.quantity,
            "leverage": proposal.leverage,
            "entry": fill,
            "stop_loss": float(proposal.stop_loss),
            "take_profit": float(proposal.take_profit),
            "opened_at": opened_at,
            "entry_fees": fee,
        }
        position_id = self.journal.insert_position(row)
        self.journal.event(
            "PAPER_OPEN",
            {
                "position_id": position_id,
                "client_id": client_id,
                "entry_reference": reference,
                "entry_fill": fill,
                "quantity": proposal.quantity,
                "entry_fee": fee,
                "stop_loss": proposal.stop_loss,
                "take_profit": proposal.take_profit,
            },
            proposal.symbol,
        )
        return {"id": position_id, **row}

    def _exit_fill(self, direction: str, reference: float) -> float:
        if direction == "LONG":
            return reference * (1.0 - self.slip_rate)
        return reference * (1.0 + self.slip_rate)

    def _close(
        self,
        position: dict[str, Any],
        reference: float,
        reason: str,
    ) -> dict[str, Any]:
        fill = self._exit_fill(position["direction"], reference)
        qty = float(position["quantity"])
        fee = fill * qty * self.fee_rate
        sign = 1.0 if position["direction"] == "LONG" else -1.0
        gross = (fill - float(position["entry"])) * qty * sign
        net = gross - float(position["entry_fees"]) - fee
        self.journal.close_position(
            int(position["id"]),
            exit_price=fill,
            exit_fees=fee,
            realized_pnl=net,
            reason=reason,
        )
        payload = {
            "position_id": int(position["id"]),
            "reason": reason,
            "exit_reference": reference,
            "exit_fill": fill,
            "exit_fee": fee,
            "gross_pnl": gross,
            "net_realized_pnl": net,
        }
        self.journal.event("PAPER_CLOSE", payload, position["symbol"])
        return payload

    def manage(self) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for position in self.journal.open_positions():
            try:
                candles = self.market.klines(position["symbol"], "1m", limit=180)
                since = _iso_to_ms(position["last_checked_at"])
                relevant = [c for c in candles if c.close_time > since]
                stop = float(position["stop_loss"])
                target = float(position["take_profit"])

                exit_reference: float | None = None
                reason = ""
                for candle in relevant:
                    if position["direction"] == "LONG":
                        stop_hit = candle.low <= stop
                        target_hit = candle.high >= target
                    else:
                        stop_hit = candle.high >= stop
                        target_hit = candle.low <= target

                    # If both levels occur in the same one-minute candle, assume
                    # the stop happened first. This is conservative and avoids
                    # inventing intrabar sequencing.
                    if stop_hit:
                        exit_reference = stop
                        reason = "STOP"
                        break
                    if target_hit:
                        exit_reference = target
                        reason = "TAKE_PROFIT"
                        break

                if exit_reference is not None:
                    outcomes.append(self._close(position, exit_reference, reason))
                else:
                    self.journal.touch_position(int(position["id"]))
            except Exception as exc:
                # A data/service failure must not silently mark protection as
                # healthy. Keep the position open, record the failure, and let
                # the next cycle retry management before considering entries.
                self.journal.event(
                    "PAPER_PROTECTION_CHECK_FAILED",
                    {
                        "position_id": int(position["id"]),
                        "error": str(exc)[:500],
                    },
                    position["symbol"],
                )
                outcomes.append(
                    {
                        "position_id": int(position["id"]),
                        "status": "PROTECTION_CHECK_FAILED",
                        "error": str(exc)[:500],
                    }
                )
        return outcomes

    def close_all(self, reason: str = "MANUAL_CLOSE_ALL") -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for position in self.journal.open_positions():
            book = self.market.depth(position["symbol"], limit=20)
            reference = (
                book.best_bid
                if position["direction"] == "LONG"
                else book.best_ask
            )
            outcomes.append(self._close(position, reference, reason))
        return outcomes
