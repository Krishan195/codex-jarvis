"""Deterministic position sizing and portfolio risk validation."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .config import TradingConfig
from .models import TradeProposal


@dataclass
class AccountState:
    open_positions: int
    aggregate_notional: float
    realized_pnl_today: float
    unrealized_pnl: float = 0.0
    current_equity: float = 0.0
    peak_equity: float = 0.0
    state_certain: bool = True


def _round_down(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor((value + 1e-12) / step) * step


def _round_nearest(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


def apply_risk(
    proposal: TradeProposal,
    cfg: TradingConfig,
    state: AccountState,
    *,
    step_size: float,
    tick_size: float,
    min_qty: float,
    max_qty: float,
    min_notional: float,
    funding_rate: float | None = None,
    require_enabled: bool = True,
    fixed_quantity: float | None = None,
) -> TradeProposal:
    errors: list[str] = []
    if proposal.decision != "TRADE":
        return proposal
    if require_enabled and not cfg.enabled:
        errors.append("paper trading is disabled")
    if cfg.paused:
        errors.append("new entries are paused")
    if not state.state_certain:
        errors.append("account state is uncertain")
    if proposal.symbol not in cfg.permitted_symbols:
        errors.append("symbol is not permitted")
    if proposal.market_type != cfg.market_type:
        errors.append("market type does not match configuration")
    if proposal.leverage > cfg.risk.max_leverage:
        errors.append("requested leverage exceeds configured maximum")
    if state.open_positions >= cfg.risk.max_simultaneous_positions:
        errors.append("maximum simultaneous positions reached")

    capital = cfg.risk.allocated_capital_quote
    daily_limit = capital * cfg.risk.daily_loss_limit_percent / 100.0
    daily_marked_pnl = state.realized_pnl_today + state.unrealized_pnl
    if daily_marked_pnl <= -daily_limit:
        errors.append("daily loss limit reached")

    drawdown = 0.0
    if state.peak_equity > 0:
        drawdown = max(0.0, (state.peak_equity - state.current_equity) / state.peak_equity * 100.0)
    if drawdown >= cfg.risk.max_drawdown_percent:
        errors.append("maximum drawdown limit reached")

    entry = float(proposal.entry_reference or 0)
    stop = float(proposal.stop_loss or 0)
    if tick_size > 0:
        stop = _round_nearest(stop, tick_size)
        proposal.stop_loss = stop
        if proposal.take_profit is not None:
            proposal.take_profit = _round_nearest(
                float(proposal.take_profit), tick_size
            )
    if entry <= 0 or stop <= 0 or entry == stop:
        errors.append("invalid entry/stop geometry")

    if errors:
        proposal.decision = "WAIT"
        proposal.risk_rejections = errors
        return proposal

    risk_budget = capital * cfg.risk.risk_per_trade_percent / 100.0
    fee_rate = cfg.costs.taker_fee_bps / 10_000.0
    slip_rate = cfg.costs.slippage_bps / 10_000.0
    distance = abs(entry - stop)

    # Conservative per-unit loss estimate: stop distance plus adverse entry/exit
    # slippage and taker fees. Optional funding uses one absolute funding interval.
    entry_fee_unit = entry * fee_rate
    exit_fee_unit = stop * fee_rate
    slippage_unit = (entry + stop) * slip_rate
    funding_unit = (
        entry * abs(float(funding_rate or 0.0))
        if cfg.costs.include_one_funding_interval
        else 0.0
    )
    unit_loss = distance + entry_fee_unit + exit_fee_unit + slippage_unit + funding_unit
    if fixed_quantity is None:
        quantity = _round_down(risk_budget / unit_loss, step_size)
    else:
        quantity = float(fixed_quantity)
        if not math.isfinite(quantity) or quantity <= 0:
            proposal.decision = "WAIT"
            proposal.risk_rejections = ["manual quantity must be finite and positive"]
            return proposal
        if abs(quantity - _round_down(quantity, step_size)) > max(1e-12, step_size * 1e-7):
            errors.append("manual quantity does not match exchange step size")
        if quantity * unit_loss > risk_budget + 1e-9:
            errors.append("manual trade exceeds the configured loss budget")

    if quantity <= 0 or quantity < min_qty:
        errors.append("risk-sized quantity is below exchange minimum")
    if max_qty > 0 and quantity > max_qty:
        if fixed_quantity is None:
            quantity = _round_down(max_qty, step_size)
        else:
            errors.append("manual quantity exceeds exchange maximum")

    notional = quantity * entry
    if min_notional > 0 and notional < min_notional:
        errors.append("risk-sized notional is below exchange minimum")

    exposure_limit = capital * cfg.risk.max_aggregate_exposure_percent / 100.0
    if state.aggregate_notional + notional > exposure_limit:
        errors.append("aggregate exposure limit would be exceeded")

    margin = notional / max(proposal.leverage, 1.0)
    if margin > max(state.current_equity, 0.0):
        errors.append("estimated margin exceeds current paper equity")

    if errors:
        proposal.decision = "WAIT"
        proposal.risk_rejections = errors
        return proposal

    proposal.quantity = quantity
    proposal.estimated_entry_fee = quantity * entry_fee_unit
    proposal.estimated_exit_fee = quantity * exit_fee_unit
    proposal.estimated_slippage = quantity * slippage_unit
    # Entry reference is already the executable side of the book (ask for a
    # long, bid for a short). Report half-spread versus midpoint separately;
    # it is not double-counted in the stop-distance calculation.
    proposal.estimated_spread_cost = (
        quantity * entry * (proposal.spread_bps / 20_000.0)
    )
    proposal.estimated_funding = quantity * funding_unit
    proposal.estimated_loss_at_stop = quantity * unit_loss
    return proposal
