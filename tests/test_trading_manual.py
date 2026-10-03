"""Offline behavioural checks: no credentials, network or exchange orders."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from jarvis_core.trading.config import TradingConfig, RiskConfig, ManualTradingConfig
from jarvis_core.trading.manual import ManualRequest, ManualReviewStore, DemoMarketData, build_review
from jarvis_core.trading.market_data import BinancePublicMarketData, MarketDataError
from jarvis_core.trading.models import BookSnapshot, Candle, FuturesContext
from jarvis_core.trading.risk import AccountState
from jarvis_core.trading.telegram_approval import ApprovalStore, DemoTelegramApprovalService, TelegramApprovalConfig
from jarvis_core.trading import demo_exchange


def config():
    return TradingConfig(risk=RiskConfig(allocated_capital_quote=10000),
                         manual=ManualTradingConfig(enabled=True, max_leverage=10, max_margin_quote=10))


def account():
    return AccountState(0, 0, 0, current_equity=10000, peak_equity=10000)


class FakeMarket:
    def __init__(self):
        self.now = int(time.time() * 1000)
        self.book = BookSnapshot(99.99, 100, 1, 1000, 1000, 0, self.now)
        self.rules = dict(symbol="BTCUSDT", status="TRADING", order_types=["MARKET"],
                          tick_size=.01, step_size=.001, min_qty=.001, max_qty=1000, min_notional=5)

    def server_time_ms(self):
        return self.now

    def klines(self, symbol, interval, **kwargs):
        step = {"15m": 900000, "1h": 3600000, "4h": 14400000}[interval]
        end = self.now // step * step
        return [Candle(end - (80-i)*step, 100, 101, 99, 100, 10,
                       end - (79-i)*step - 1, 1000, 10, 5, 500) for i in range(80)]

    def recent_trades(self, *args, **kwargs):
        return [{"time": self.now, "price": 100}]

    def futures_context(self, *args):
        return FuturesContext(last_funding_rate=0)

    def symbol_rules(self, *args):
        return self.rules

    def depth(self, *args, **kwargs):
        return self.book


def review(request=None, cfg=None, state=None, market=None, **kwargs):
    return build_review(request or ManualRequest("BTCUSDT", "LONG", 10, margin_quote=10),
        cfg or config(), state or account(), market=market or FakeMarket(), available=10000,
        actual_margin="ISOLATED", actual_leverage=2, tolerance_bps=10, account_blockers=[], **kwargs)


class ManualReviewTests(unittest.TestCase):
    def test_review_warns_about_wait_but_preserves_user_intent(self):
        cfg = config()
        r = review(cfg=cfg)
        self.assertTrue(r["can_propose"], r["blockers"])
        self.assertEqual(r["recommendation"], "WAIT")
        self.assertEqual(r["payload"]["direction"], "LONG")
        self.assertEqual(r["payload"]["source"], "MANUAL")
        self.assertLessEqual(r["metrics"]["maximum_notional_quote"], 100)
        self.assertAlmostEqual(r["metrics"]["adverse_one_percent_loss_before_costs"], .999)
        self.assertGreater(r["metrics"]["estimated_loss_at_stop"], 2)
        self.assertTrue(r["payload"]["suggested_stop"])
        self.assertEqual(cfg.risk.max_leverage, 2)
        self.assertFalse(cfg.enabled)  # Does not enable autonomous PAPER entries.

    def test_margin_and_notional_are_distinct(self):
        margin = review()["metrics"]["notional_quote"]
        notional = review(ManualRequest("BTCUSDT", "LONG", 10, notional_quote=10))["metrics"]["notional_quote"]
        self.assertGreater(margin, 9 * notional)
        self.assertLessEqual(notional, 10)

    def test_invalid_or_ambiguous_requests_are_rejected(self):
        for values in ({}, {"margin_quote": 10, "notional_quote": 10},
                       {"margin_quote": float("nan")}, {"margin_quote": -1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ManualRequest("BTCUSDT", "LONG", 10, **values).validate()
        with self.assertRaises(ValueError):
            ManualRequest("BTCUSDT", "LONG", 1.5, margin_quote=10).validate()

    def test_hard_limits_cannot_be_overridden(self):
        cases = []
        cfg = config(); cfg.manual.enabled = False; cases.append((cfg, account(), "disabled"))
        cfg = config(); cfg.manual.max_leverage = 2; cases.append((cfg, account(), "leverage limit"))
        cfg = config(); cfg.manual.max_margin_quote = 5; cases.append((cfg, account(), "margin limit"))
        cfg = config(); cfg.paused = True; cases.append((cfg, account(), "paused"))
        cfg = config(); cfg.risk.risk_per_trade_percent = .001; cases.append((cfg, account(), "loss budget"))
        cfg = config(); cfg.risk.max_aggregate_exposure_percent = .5; cases.append((cfg, account(), "exposure"))
        state = account(); state.realized_pnl_today = -300; cases.append((config(), state, "daily loss"))
        state = account(); state.state_certain = False; cases.append((config(), state, "uncertain"))
        state = account(); state.open_positions = 2; cases.append((config(), state, "simultaneous"))
        for cfg, state, expected in cases:
            with self.subTest(expected=expected):
                r = review(cfg=cfg, state=state)
                self.assertFalse(r["can_propose"])
                self.assertIn(expected, " ".join(r["blockers"]))

    def test_minimum_notional_blocks_without_increasing_user_amount(self):
        market = FakeMarket(); market.rules["min_notional"] = 100
        r = review(market=market)
        self.assertFalse(r["can_propose"])
        self.assertIn("exchange minimum", " ".join(r["blockers"]))
        self.assertLess(r["metrics"]["notional_quote"], 100)

    def test_short_geometry_and_bad_stops(self):
        r = review(ManualRequest("BTCUSDT", "SHORT", 10, margin_quote=10, stop_loss=102, take_profit=96))
        self.assertTrue(r["can_propose"], r["blockers"])
        bad = review(ManualRequest("BTCUSDT", "LONG", 10, margin_quote=10, stop_loss=102, take_profit=96))
        self.assertFalse(bad["can_propose"])
        self.assertIn("correct sides", " ".join(bad["blockers"]))

    def test_stale_or_nonfinite_books_fail_closed(self):
        for book in (replace(FakeMarket().book, observed_at_ms=1),
                     replace(FakeMarket().book, best_ask=float("nan"))):
            market = FakeMarket(); market.book = book
            with self.assertRaises(MarketDataError):
                review(market=market)

    def test_exchange_info_selects_requested_symbol(self):
        with patch("jarvis_core.trading.market_data._request_json", return_value={"symbols": [
            {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]}):
            self.assertEqual(BinancePublicMarketData("futures").exchange_info("ETHUSDT")["symbol"], "ETHUSDT")

    def test_manual_market_is_demo_only(self):
        self.assertEqual(DemoMarketData().bases, (demo_exchange.DEMO_BASE_URL,))


class ManualFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ApprovalStore(Path(self.temp.name) / "trading.sqlite3")
        self.addCleanup(self.store.conn.close)
        self.market = FakeMarket()
        self.svc = object.__new__(DemoTelegramApprovalService)
        self.svc._reload_from_disk = False
        self.svc.trading_cfg = config()
        self.svc.telegram_cfg = TelegramApprovalConfig(123, 123)
        self.svc.store = self.store
        self.svc.bot = Mock()
        self.svc.bot.send.return_value = {"message_id": 1234}
        self.svc.engine = Mock()
        self.svc._manual_context = Mock(return_value=(account(), 0, "ISOLATED", 2, 10000, []))
        self.market_patch = patch("jarvis_core.trading.telegram_approval.DemoMarketData", return_value=self.market)
        self.market_patch.start(); self.addCleanup(self.market_patch.stop)

    def make_review(self):
        return self.svc.review_manual(ManualRequest("BTCUSDT", "LONG", 10, margin_quote=10))

    def approve(self, row):
        self.store.decide(row["proposal_id"], approve=True, user_id=123, callback_id="approval-1")

    def test_review_persists_without_message_or_order(self):
        with patch.object(demo_exchange, "market_order") as trade:
            r = self.make_review()
        self.svc.bot.send.assert_not_called(); trade.assert_not_called()
        saved = ManualReviewStore(self.store.conn).get(r["review_id"])
        self.assertEqual(saved, r)

    def test_propose_requires_review_and_never_executes(self):
        with self.assertRaises(ValueError):
            self.svc.propose_manual("missing")
        r = self.make_review()
        with patch.object(demo_exchange, "market_order") as trade, patch.object(demo_exchange, "change_leverage") as leverage:
            row = self.svc.propose_manual(r["review_id"])
        trade.assert_not_called(); leverage.assert_not_called()
        self.assertEqual(row["status"], "PENDING")
        self.assertIn("MANUAL TRADE", self.svc.bot.send.call_args.args[0])
        self.assertIn("Assessment: WAIT", self.svc.bot.send.call_args.args[0])
        self.assertIn("2x to 10x", self.svc.bot.send.call_args.args[0])

    def test_manual_review_does_not_allow_unapproved_execution(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        with patch.object(demo_exchange, "market_order") as trade, self.assertRaises(ValueError):
            self.svc.execute_approved(row["proposal_id"])
        trade.assert_not_called()

    def test_expired_review_and_changed_price_require_new_review(self):
        r = self.make_review()
        self.market.book = replace(self.market.book, best_ask=101)
        with self.assertRaisesRegex(ValueError, "Price moved"):
            self.svc.propose_manual(r["review_id"])
        self.svc.bot.send.assert_not_called()
        with patch("jarvis_core.trading.manual.time.time", return_value=time.time() + 301):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.svc.propose_manual(r["review_id"])

    def test_blocked_review_cannot_be_sent(self):
        self.svc.trading_cfg.manual.max_leverage = 2
        r = self.make_review()
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.svc.propose_manual(r["review_id"])
        self.svc.bot.send.assert_not_called()

    def test_failed_send_retries_same_proposal(self):
        r = self.make_review()
        self.svc.bot.send.side_effect = RuntimeError("Telegram offline")
        with self.assertRaises(RuntimeError):
            self.svc.propose_manual(r["review_id"])
        pending = self.store.pending(); self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["telegram_message_id"])
        self.svc.bot.send.side_effect = None
        row = self.svc.propose_manual(r["review_id"])
        self.assertEqual(row["proposal_id"], pending[0]["proposal_id"])
        self.assertEqual(row["telegram_message_id"], 1234)
        self.assertEqual(len(self.store.pending()), 1)

    def test_scanner_continues_other_symbols_with_manual_pending(self):
        self.svc.propose_manual(self.make_review()["review_id"])
        self.svc.create_and_send = Mock(return_value=None)
        self.svc.propose_scan()
        self.svc.create_and_send.assert_called_once_with("ETHUSDT")

    def test_symbol_failure_does_not_stop_scanning(self):
        self.svc.create_and_send = Mock(side_effect=[RuntimeError("BTC unavailable"), None])
        self.assertEqual(self.svc.propose_scan(), [])
        self.assertEqual(self.svc.create_and_send.call_count, 2)
        self.svc.engine.journal.event.assert_called()

    def test_changed_account_or_risk_limit_invalidates_approval(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.svc.trading_cfg.manual.max_leverage = 2
        with patch.object(demo_exchange, "market_order") as trade:
            result = self.svc.execute_approved(row["proposal_id"])
        self.assertEqual(result["status"], "INVALIDATED"); trade.assert_not_called()

    def test_approved_manual_flow_executes_once_and_survives_notification_failure(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.svc.bot.send.side_effect = RuntimeError("Telegram offline after approval")
        leverage = {"value": 2}
        def set_leverage(symbol, value):
            leverage["value"] = value
            return {"leverage": value}
        def signed(method, path, params=None, **kwargs):
            if path == "/fapi/v1/order":
                return {"orderId": 77, "status": "FILLED", "executedQty": "0.999", "origQty": "0.999", "avgPrice": "100"}
            if path == "/fapi/v1/algoOrder":
                return {"algoId": 78, "algoStatus": "NEW"}
            self.fail("Unexpected exchange endpoint: " + path)
        with patch.object(demo_exchange, "DB_PATH", self.store.path), \
             patch.object(demo_exchange, "server_time_ms", return_value=self.market.now), \
             patch.object(demo_exchange, "positions", return_value=[]), \
             patch.object(demo_exchange, "open_orders", return_value=[]), \
             patch.object(demo_exchange, "open_algo_orders", return_value=[]), \
             patch.object(demo_exchange, "symbol_config", side_effect=lambda symbol: {"leverage": leverage["value"]}), \
             patch.object(demo_exchange, "change_leverage", side_effect=set_leverage), \
             patch.object(demo_exchange, "_signed_request", side_effect=signed) as api:
            result = self.svc.execute_approved(row["proposal_id"])
            self.assertEqual(result["entry"]["status"], "FILLED")
            self.assertEqual(leverage["value"], 10)
            self.assertEqual(self.store.get(row["proposal_id"])["status"], "EXECUTED")
            protective = [c for c in api.call_args_list if c.args[1] == "/fapi/v1/algoOrder"]
            self.assertEqual(len(protective), 2)
            self.assertEqual(protective[0].args[2]["reduceOnly"], "true")
            with self.assertRaises(ValueError):
                self.svc.execute_approved(row["proposal_id"])
            self.store.mark(row["proposal_id"], "CLOSED")
            self.svc.restore_manual_leverage()
            self.assertEqual(leverage["value"], 2)
        self.svc.engine.journal.event.assert_called()

    def test_leverage_cannot_change_before_approval(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        with patch.object(demo_exchange, "DB_PATH", self.store.path), \
             patch.object(demo_exchange, "change_leverage") as change:
            with self.assertRaises(demo_exchange.DemoExchangeError):
                demo_exchange.change_approved_leverage(row["proposal_id"], "BTCUSDT", 10)
            change.assert_not_called()

    def test_expired_approved_request_does_not_reserve_symbol_forever(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.store.conn.execute("UPDATE telegram_trade_proposals SET expires_at_ms=1 WHERE proposal_id=?", (row["proposal_id"],))
        self.store.conn.commit()
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.get(row["proposal_id"])["status"], "EXPIRED")

    def test_only_one_poller_can_run(self):
        second = ApprovalStore(self.store.path)
        try:
            with self.store.poller_lock():
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with second.poller_lock():
                        self.fail("Second Telegram poller acquired lock")
            with second.poller_lock():
                pass  # Clean shutdown releases it.
        finally:
            second.conn.close()

    def test_partial_fill_receives_protection_only_for_filled_quantity(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.svc._confirm_order = Mock(return_value={"status": "CANCELED", "executedQty": ".4", "origQty": ".999", "avgPrice": "100"})
        with patch.object(demo_exchange, "change_approved_leverage"), \
             patch.object(demo_exchange, "symbol_config", return_value={"leverage": 10}), \
             patch.object(demo_exchange, "market_order", return_value={"response": {"orderId": 1}}), \
             patch.object(demo_exchange, "place_protection", return_value={}) as protect:
            self.svc.execute_approved(row["proposal_id"])
        self.assertEqual(protect.call_args.kwargs["quantity"], .4)

    def test_protection_failure_still_closes_when_telegram_is_down(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.svc._confirm_order = Mock(return_value={"status": "FILLED", "executedQty": ".999", "avgPrice": "100"})
        self.svc.bot.send.side_effect = RuntimeError("Telegram offline")
        with patch.object(demo_exchange, "change_approved_leverage"), \
             patch.object(demo_exchange, "symbol_config", return_value={"leverage": 10}), \
             patch.object(demo_exchange, "market_order", return_value={"response": {"orderId": 1}}), \
             patch.object(demo_exchange, "place_protection", side_effect=RuntimeError("protection rejected")), \
             patch.object(demo_exchange, "close_symbol_position", return_value=[]) as emergency:
            result = self.svc.execute_approved(row["proposal_id"])
        emergency.assert_called_once_with("BTCUSDT")
        self.assertIn("emergency_close", result["protection"])
        self.assertTrue(result["protection"]["algo"])

    def test_confirmation_failure_is_not_reported_as_executed(self):
        row = self.svc.propose_manual(self.make_review()["review_id"])
        self.approve(row)
        self.svc._confirm_order = Mock(side_effect=RuntimeError("timeout"))
        with patch.object(demo_exchange, "change_approved_leverage"), \
             patch.object(demo_exchange, "symbol_config", return_value={"leverage": 10}), \
             patch.object(demo_exchange, "market_order", return_value={"response": {"orderId": 1}}), \
             self.assertRaises(RuntimeError):
            self.svc.execute_approved(row["proposal_id"])
        self.assertEqual(self.store.get(row["proposal_id"])["status"], "EXECUTION_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
