"""Read-only manual-trade reviews; execution remains behind Telegram approval.

User intent replaces signal gates, never account/risk checks. Amounts denote a
maximum margin or notional, not a loss budget. No model-generated probabilities.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
import secrets
import time
from typing import Any

from .config import TradingConfig
from .demo_exchange import DEMO_BASE_URL
from .indicators import atr_wilder
from .market_data import BinancePublicMarketData, MarketDataError
from .models import TradeProposal
from .risk import AccountState, apply_risk, _round_down, _round_nearest
from .strategy import generate_proposal


def validate_book(book, now: int) -> None:
    if not all(math.isfinite(v) for v in (book.best_bid, book.best_ask, book.spread_bps)):
        raise MarketDataError("Demo order book contains non-finite values.")
    if not 0 < book.best_bid < book.best_ask or book.spread_bps < 0:
        raise MarketDataError("Demo order book is crossed or invalid.")
    if not 0 <= now - book.observed_at_ms <= 30000:
        raise MarketDataError("Demo order book is stale.")


class DemoMarketData(BinancePublicMarketData):
    """Price and filters from the SAME environment as the requested order."""
    def __init__(self):
        super().__init__("futures")

    @property
    def bases(self) -> tuple[str, ...]:
        return (DEMO_BASE_URL,)


@dataclass(frozen=True)
class ManualRequest:
    symbol: str
    direction: str
    leverage: int
    margin_quote: float | None = None
    notional_quote: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None

    def validate(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("Direction must be LONG or SHORT.")
        if (self.margin_quote is None) == (self.notional_quote is None):
            raise ValueError("Specify exactly one amount: margin OR total notional.")
        if not math.isfinite(self.leverage) or not 1 <= self.leverage <= 125 or int(self.leverage) != self.leverage:
            raise ValueError("Leverage must be an integer from 1 to 125.")
        for value in (self.margin_quote, self.notional_quote, self.stop_loss, self.take_profit):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("Amounts and prices must be finite and positive.")

    @property
    def notional_limit(self) -> float:
        self.validate()
        return (float(self.margin_quote) * self.leverage if self.margin_quote is not None
                else float(self.notional_quote))


class ManualReviewStore:
    """Immutable review snapshots share the approval DB, not Markdown memory."""
    def __init__(self, conn):
        self.conn = conn
        conn.execute("""CREATE TABLE IF NOT EXISTS manual_trade_reviews (
            review_id TEXT PRIMARY KEY, expires_at_ms INTEGER NOT NULL,
            report_json TEXT NOT NULL)""")
        conn.commit()

    def save(self, report: dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO manual_trade_reviews VALUES(?,?,?)", (
            report["review_id"], report["expires_at_ms"], json.dumps(report, sort_keys=True)))
        self.conn.commit()

    def get(self, review_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT report_json FROM manual_trade_reviews WHERE review_id=?",
                                (review_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown manual review. Run manual-review first.")
        report = json.loads(row[0])
        if int(report["expires_at_ms"]) < int(time.time() * 1000):
            raise ValueError("Manual review expired. Obtain a fresh review.")
        return report


def assess_fixed_trade(payload: dict[str, Any], cfg: TradingConfig, state: AccountState,
                       *, rules: dict[str, Any], available: float,
                       funding: float | None) -> tuple[list[str], dict[str, float]]:
    """Check frozen quantity/geometry at BOTH ends of the approved price range."""
    req = ManualRequest(**payload["manual_request"])
    req.validate()
    cfg.validate()
    numeric = [available, funding or 0, state.current_equity, state.peak_equity,
               state.aggregate_notional, state.realized_pnl_today, state.unrealized_pnl,
               *[rules[k] for k in ("tick_size", "step_size", "min_qty", "max_qty", "min_notional")],
               cfg.costs.taker_fee_bps, cfg.costs.slippage_bps,
               cfg.risk.allocated_capital_quote, cfg.risk.risk_per_trade_percent,
               cfg.risk.max_aggregate_exposure_percent, cfg.risk.daily_loss_limit_percent,
               cfg.risk.max_drawdown_percent]
    if not all(math.isfinite(v) for v in numeric):
        return ["Non-finite account, exchange or risk data."], {}
    if rules["tick_size"] <= 0 or rules["step_size"] <= 0:
        return ["Invalid Demo price/quantity increments."], {}
    if (float(payload["leverage"]) != req.leverage or payload["symbol"] != req.symbol
            or payload["direction"] != req.direction):
        return ["Proposal differs from the reviewed manual request."], {}
    entry, qty = float(payload["entry_reference"]), float(payload["quantity"])
    stop, target = float(payload["stop_loss"]), float(payload["take_profit"])
    tolerance = float(payload["approval_price_tolerance_bps"]) / 10000
    errors: list[str] = []
    if not all(math.isfinite(v) and v > 0 for v in (entry, qty, stop, target)):
        return ["Invalid price or quantity."], {}
    if not math.isfinite(tolerance) or not 0 < tolerance <= .01:
        return ["Invalid price tolerance."], {}
    low, high = entry * (1 - tolerance), entry * (1 + tolerance)
    if not cfg.manual.enabled:
        errors.append("Manual Demo proposals are disabled; explicitly configure manual limits first.")
    if cfg.market_type != "futures" or cfg.margin_mode != "ISOLATED":
        errors.append("Manual Demo trades require futures with ISOLATED margin.")
    if req.leverage > cfg.manual.max_leverage:
        errors.append("Requested leverage exceeds the separate manual leverage limit.")
    margin = high * qty / req.leverage
    if req.notional_limit / req.leverage > cfg.manual.max_margin_quote + 1e-9:
        errors.append("Requested margin exceeds the manual margin limit.")
    if high * qty > req.notional_limit + 1e-8:
        errors.append("Quantity would exceed the requested amount within the price tolerance.")
    geometry_ok = (stop < low and target > high if req.direction == "LONG"
                   else target < low and stop > high)
    if not geometry_ok:
        errors.append("Stop and target must be on opposite correct sides of the entire entry range.")
    if rules.get("symbol") != req.symbol or rules.get("status") != "TRADING":
        errors.append("Demo symbol is unavailable or not trading.")
    if "MARKET" not in rules.get("order_types", []):
        errors.append("Demo symbol does not support MARKET orders.")
    for price in (stop, target):
        tick = rules["tick_size"]
        if tick <= 0 or abs(price - _round_nearest(price, tick)) > max(1e-10, tick * 1e-7):
            errors.append("Protective prices do not match the Demo tick size.")
    risk_cfg = deepcopy(cfg)
    risk_cfg.risk.max_leverage = cfg.manual.max_leverage
    checks = []
    for price in (low, high):
        proposal = TradeProposal(
            decision="TRADE", symbol=req.symbol, market_type="futures", direction=req.direction,
            strategy_version="manual-v1", signal_timestamp=payload["signal_timestamp"],
            expires_at=payload["expires_at"], order_type="MARKET", entry_reference=price,
            stop_loss=stop, take_profit=target, leverage=req.leverage,
        )
        # Shared portfolio limits; no risk-based resize of the user's quantity.
        checked = apply_risk(proposal, risk_cfg, state, require_enabled=False,
            fixed_quantity=qty, funding_rate=funding,
            **{key: rules[key] for key in ("step_size", "tick_size", "min_qty", "max_qty", "min_notional")})
        errors.extend(checked.risk_rejections)
        checks.append(checked)
    worst = max(checks, key=lambda p: p.estimated_loss_at_stop)
    costs = (high + max(stop, target)) * qty * (cfg.costs.taker_fee_bps + cfg.costs.slippage_bps) / 10000
    costs += high * qty * abs(funding or 0) if cfg.costs.include_one_funding_interval else 0
    if margin + high * qty * cfg.costs.taker_fee_bps / 10000 > available:
        errors.append("Insufficient available Demo balance for margin and entry fee.")
    # Compute even if shared risk validation returned early, so blocked reviews
    # still explain the actual requested loss rather than displaying zero risk.
    worst_entry = high if req.direction == "LONG" else low
    loss = qty * (abs(worst_entry - stop)
        + (worst_entry + stop) * (cfg.costs.taker_fee_bps + cfg.costs.slippage_bps) / 10000
        + (worst_entry * abs(funding or 0) if cfg.costs.include_one_funding_interval else 0))
    if loss >= margin:
        errors.append("Estimated stop loss consumes the initial margin; liquidation may precede the stop.")
    target_profit = qty * abs(target - worst_entry) - costs if geometry_ok else 0.0
    metrics = {
        "notional_quote": entry * qty, "maximum_notional_quote": high * qty,
        "estimated_margin_quote": entry * qty / req.leverage,
        "maximum_margin_quote": margin, "estimated_loss_at_stop": loss,
        "loss_percent_of_margin": loss / margin * 100,
        "loss_percent_of_allocated_capital": loss / cfg.risk.allocated_capital_quote * 100,
        "estimated_net_target_profit": target_profit, "estimated_costs": costs,
        "net_reward_risk": target_profit / loss if loss > 0 else 0,
        "adverse_one_percent_loss_before_costs": entry * qty * .01,
        "adverse_three_percent_loss_before_costs": entry * qty * .03,
        "estimated_entry_fee": worst.estimated_entry_fee,
        "estimated_exit_fee": worst.estimated_exit_fee,
        "estimated_slippage": worst.estimated_slippage,
        "estimated_funding": worst.estimated_funding,
    }
    return list(dict.fromkeys(errors)), metrics


def build_review(request: ManualRequest, cfg: TradingConfig, state: AccountState,
                 *, market: BinancePublicMarketData, available: float,
                 actual_margin: str, actual_leverage: float,
                 tolerance_bps: float, account_blockers: list[str]) -> dict[str, Any]:
    request.validate()
    if request.symbol not in cfg.permitted_symbols:
        raise ValueError("Symbol is not permitted by trading configuration.")
    signal = market.klines(request.symbol, "15m", limit=250)
    h1 = market.klines(request.symbol, "1h", limit=250)
    h4 = market.klines(request.symbol, "4h", limit=250)
    recent = market.recent_trades(request.symbol, limit=100)
    futures = market.futures_context(request.symbol)
    rules = market.symbol_rules(request.symbol)
    book = market.depth(request.symbol, limit=20)
    now = market.server_time_ms()
    if not recent or not 0 <= now - int(recent[-1]["time"]) <= 120000:
        raise MarketDataError("Recent Demo trade feed is stale or inconsistent.")
    validate_book(book, now)
    baseline = generate_proposal(symbol=request.symbol, market_type="futures",
        signal_candles=signal, context_1h=h1, context_4h=h4, book=book,
        futures=futures, server_time_ms=now, cfg=cfg.strategy,
        leverage=cfg.risk.max_leverage)
    entry = book.best_ask if request.direction == "LONG" else book.best_bid
    if not all(math.isfinite(rules[k]) and rules[k] > 0 for k in ("tick_size", "step_size")):
        raise MarketDataError("Invalid Demo price/quantity increments.")
    atr = atr_wilder(signal, cfg.strategy.atr_period)[-1]
    if not math.isfinite(atr) or atr <= 0:
        raise MarketDataError("No valid ATR for a manual review.")
    if request.stop_loss is None:
        stop = (min(signal[-1].low - cfg.strategy.stop_atr_buffer * atr, entry - .25 * atr)
                if request.direction == "LONG" else
                max(signal[-1].high + cfg.strategy.stop_atr_buffer * atr, entry + .25 * atr))
    else:
        stop = request.stop_loss
    stop = _round_nearest(stop, rules["tick_size"])
    target = request.take_profit
    if target is None:
        target = entry + (1 if request.direction == "LONG" else -1) * cfg.strategy.reward_risk * abs(entry - stop)
    target = _round_nearest(target, rules["tick_size"])
    maximum_price = entry * (1 + tolerance_bps / 10000)
    qty = _round_down(request.notional_limit / maximum_price, rules["step_size"])
    review_id = secrets.token_hex(8)
    payload = TradeProposal(decision="TRADE", symbol=request.symbol,
        market_type="futures", direction=request.direction, strategy_version="manual-v1",
        signal_timestamp=now, expires_at=now + 300000, order_type="MARKET",
        market_regime=baseline.market_regime, entry_reference=entry, stop_loss=stop,
        take_profit=target, leverage=request.leverage, spread_bps=book.spread_bps,
        quantity=qty, evidence=baseline.evidence,
        data_health={"environment": "DEMO", "server_time_ms": now,
                     "signal_candle_close_time": signal[-1].close_time}).as_dict()
    payload.update(source="MANUAL", manual_review_id=review_id, manual_request=asdict(request),
        approval_price_tolerance_bps=tolerance_bps, margin_mode="ISOLATED",
        previous_leverage=actual_leverage, restore_leverage=actual_leverage,
        suggested_stop=request.stop_loss is None, suggested_target=request.take_profit is None)
    errors, metrics = assess_fixed_trade(payload, cfg, state, rules=rules,
                                         available=available, funding=futures.last_funding_rate)
    errors.extend(account_blockers)
    if actual_margin != "ISOLATED":
        errors.append("Demo account must already use ISOLATED margin; review will not change margin mode.")
    if not math.isfinite(actual_leverage) or not 1 <= actual_leverage <= 125 or int(actual_leverage) != actual_leverage:
        errors.append("Demo account leverage could not be verified.")
    if book.spread_bps > cfg.strategy.max_spread_bps:
        errors.append("Current spread exceeds the execution liquidity limit.")
    warnings = []
    agrees = baseline.decision == "TRADE" and baseline.direction == request.direction
    if not agrees:
        warnings.append(f"Baseline strategy does not endorse this {request.direction}: {baseline.decision} {baseline.direction or ''}.".strip())
        warnings.extend(baseline.failure_reasons)
    if request.leverage > cfg.risk.max_leverage:
        warnings.append(f"Requested {request.leverage}x exceeds the strategy's {cfg.risk.max_leverage:g}x leverage; manual limits are separate.")
    if metrics.get("loss_percent_of_margin", 0) >= 25:
        warnings.append("The estimated stop loss consumes at least one quarter of this trade's initial margin.")
    if metrics.get("net_reward_risk", 0) < 1:
        warnings.append("Estimated net target profit is smaller than the estimated stop loss.")
    recommendation = ("DO_NOT_SUBMIT" if errors else "WAIT" if not agrees else
                      "CONSIDER_LOWER_EXPOSURE" if warnings else "REVIEW_FOR_APPROVAL")
    alternatives = ["Wait for the requested direction to qualify under the baseline strategy."] if not agrees else []
    if request.leverage > cfg.risk.max_leverage:
        alternatives.append(f"At the same requested margin and {cfg.risk.max_leverage:g}x, exposure would be about "
            f"{request.notional_limit / request.leverage * cfg.risk.max_leverage:.2f} USDT. "
            "Lower leverage at unchanged notional does not reduce price-move P&L; reducing notional does.")
    alternatives.append("Review a smaller position with the same stop; exchange minimums still apply. Changes require a new review.")
    payload.update({key: value for key, value in metrics.items() if key.startswith("estimated_")})
    payload["risk_review"] = {"recommendation": recommendation, "warnings": warnings,
                              "metrics": metrics, "alternatives": alternatives}
    return {"environment": "DEMO", "review_id": review_id, "expires_at_ms": now + 300000,
        "can_propose": not errors, "blockers": list(dict.fromkeys(errors)),
        "recommendation": recommendation, "warnings": warnings, "alternatives": alternatives,
        "metrics": metrics, "strategy_assessment": baseline.as_dict(), "payload": payload,
        "limitations": ["Estimates, not a win probability or a guarantee.",
            "Stops can slip; initial margin is not a guaranteed maximum loss.",
            "Exact liquidation price is unknown before exchange calculation; maintenance margin and fees matter.",
            "Demo prices and fills may differ from the live market."]}
