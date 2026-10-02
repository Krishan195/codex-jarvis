from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from jarvis_core.trading.telegram_approval import (
    ApprovalStore,
    DemoTelegramApprovalService,
    TelegramApprovalConfig,
)
from jarvis_core.trading.config import TradingConfig, RiskConfig
from jarvis_core.trading.models import BookSnapshot, TradeProposal
from jarvis_core.trading import demo_exchange


def proposal_payload() -> dict:
    return {
        "decision": "TRADE",
        "symbol": "BTCUSDT",
        "market_type": "futures",
        "direction": "LONG",
        "strategy_version": "trend-pullback-v1",
        "signal_timestamp": 1000,
        "expires_at": 9999999999999,
        "order_type": "MARKET",
        "market_regime": "trend-up",
        "entry_conditions": ["all deterministic gates passed"],
        "entry_reference": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "leverage": 2.0,
        "spread_bps": 1.0,
        "quantity": 1.0,
        "estimated_loss_at_stop": 2.25,
        "estimated_entry_fee": 0.05,
        "estimated_exit_fee": 0.05,
        "estimated_slippage": 0.10,
        "estimated_spread_cost": 0.005,
        "estimated_funding": 0.0,
        "evidence": ["1h/4h aligned", "15m reclaim"],
        "failure_reasons": [],
        "data_health": {"healthy": True},
        "risk_rejections": [],
        "approval_price_tolerance_bps": 10.0,
        "margin_mode": "ISOLATED",
    }


class FakeBot:
    def __init__(self):
        self.sent = []
        self.callbacks = []

    def send(self, text, *, keyboard=None):
        self.sent.append((text, keyboard))
        return {"message_id": len(self.sent)}

    def answer_callback(self, callback_id, text, *, alert=False):
        self.callbacks.append((callback_id, text, alert))

    def updates(self, offset):
        return []


def service_with_store(store: ApprovalStore) -> DemoTelegramApprovalService:
    service = object.__new__(DemoTelegramApprovalService)
    service.telegram_cfg = TelegramApprovalConfig(user_id=123, chat_id=456)
    service.store = store
    service.bot = FakeBot()
    service.trading_cfg = TradingConfig(
        enabled=True,
        margin_mode="ISOLATED",
        risk=RiskConfig(
            allocated_capital_quote=10_000,
            max_leverage=2,
        ),
    )
    service.engine = None
    return service


class ApprovalStoreTests(unittest.TestCase):
    def test_duplicate_signal_creates_one_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            first = store.create(proposal_payload(), expiry_seconds=120)
            second = store.create(proposal_payload(), expiry_seconds=120)
            self.assertTrue(first["_created"])
            self.assertFalse(second["_created"])
            self.assertEqual(first["proposal_id"], second["proposal_id"])

    def test_expired_proposal_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            store.conn.execute(
                "UPDATE telegram_trade_proposals SET expires_at_ms=? WHERE proposal_id=?",
                (int(time.time() * 1000) - 1, row["proposal_id"]),
            )
            store.conn.commit()
            ok, state = store.decide(
                row["proposal_id"],
                approve=True,
                user_id=123,
                callback_id="cb1",
            )
            self.assertFalse(ok)
            self.assertIn("expired", state.lower())
            self.assertEqual(store.get(row["proposal_id"])["status"], "EXPIRED")

    def test_duplicate_approval_and_execution_claim_are_single_use(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            ok, state = store.decide(
                row["proposal_id"], approve=True, user_id=123, callback_id="cb1"
            )
            self.assertTrue(ok)
            self.assertEqual(state, "APPROVED")

            ok2, state2 = store.decide(
                row["proposal_id"], approve=True, user_id=123, callback_id="cb2"
            )
            self.assertFalse(ok2)
            self.assertIn("APPROVED", state2)

            store.claim_for_execution(row["proposal_id"])
            with self.assertRaises(ValueError):
                store.claim_for_execution(row["proposal_id"])


class TelegramAuthorizationTests(unittest.TestCase):
    def test_unauthorized_callback_does_not_approve(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            service = service_with_store(store)
            service.handle_callback(
                {
                    "id": "unauth",
                    "from": {"id": 999},
                    "message": {
                        "chat": {"id": 456, "type": "private"}
                    },
                    "data": f"trade:approve:{row['proposal_id']}",
                }
            )
            self.assertEqual(store.get(row["proposal_id"])["status"], "PENDING")
            self.assertTrue(service.bot.callbacks[-1][2])

    def test_authorized_reject_is_final(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            service = service_with_store(store)
            service.handle_callback(
                {
                    "id": "reject1",
                    "from": {"id": 123},
                    "message": {
                        "chat": {"id": 456, "type": "private"}
                    },
                    "data": f"trade:reject:{row['proposal_id']}",
                }
            )
            self.assertEqual(store.get(row["proposal_id"])["status"], "REJECTED")


class RevalidationTests(unittest.TestCase):
    def test_price_move_beyond_approved_tolerance_invalidates(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            service = service_with_store(store)

            class FakeEngine:
                class Market:
                    @staticmethod
                    def depth(symbol, limit=20):
                        return BookSnapshot(
                            best_bid=101.99,
                            best_ask=102.00,
                            spread_bps=1.0,
                            bid_notional=1_000_000,
                            ask_notional=1_000_000,
                            imbalance=0.0,
                            observed_at_ms=int(time.time() * 1000),
                        )

                market = Market()

                @staticmethod
                def proposal(symbol, account_state_override=None, require_enabled=True):
                    p = proposal_payload()
                    return TradeProposal(
                        decision=p["decision"],
                        symbol=p["symbol"],
                        market_type=p["market_type"],
                        direction=p["direction"],
                        strategy_version=p["strategy_version"],
                        signal_timestamp=p["signal_timestamp"],
                        expires_at=p["expires_at"],
                        order_type=p["order_type"],
                        market_regime=p["market_regime"],
                        entry_conditions=p["entry_conditions"],
                        entry_reference=p["entry_reference"],
                        stop_loss=p["stop_loss"],
                        take_profit=p["take_profit"],
                        leverage=p["leverage"],
                        spread_bps=p["spread_bps"],
                        quantity=p["quantity"],
                        estimated_loss_at_stop=p["estimated_loss_at_stop"],
                        estimated_entry_fee=p["estimated_entry_fee"],
                        estimated_exit_fee=p["estimated_exit_fee"],
                        estimated_slippage=p["estimated_slippage"],
                        estimated_spread_cost=p["estimated_spread_cost"],
                        estimated_funding=p["estimated_funding"],
                        evidence=p["evidence"],
                    )

            service.engine = FakeEngine()
            service._demo_state = lambda symbol: (
                object(), 0.0, "ISOLATED", 2.0
            )

            with patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.open_orders",
                return_value=[],
            ), patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.positions",
                return_value=[],
            ), patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.server_time_ms",
                return_value=int(time.time() * 1000),
            ):
                with self.assertRaisesRegex(RuntimeError, "Price moved"):
                    service.revalidate(row)


class ExecutionFlowTests(unittest.TestCase):
    def test_approved_demo_flow_confirms_fill_and_protection(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            ok, _ = store.decide(
                row["proposal_id"], approve=True, user_id=123, callback_id="ok"
            )
            self.assertTrue(ok)
            service = service_with_store(store)
            service.revalidate = lambda _row: {"ok": True}
            service._confirm_order = lambda symbol, client_id: {
                "status": "FILLED",
                "orderId": 777,
                "executedQty": "1",
                "avgPrice": "100.1",
            }

            with patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.make_client_id",
                return_value="jv-demo-BTCUSDT-p123",
            ), patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.market_order",
                return_value={
                    "response": {"status": "NEW", "orderId": 777}
                },
            ), patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.place_protection",
                return_value={
                    "stop_client_id": "stop1",
                    "target_client_id": "target1",
                    "quantity": 1.0,
                },
            ):
                result = service.execute_approved(row["proposal_id"])

            self.assertEqual(result["entry"]["status"], "FILLED")
            self.assertEqual(store.get(row["proposal_id"])["status"], "EXECUTED")
            messages = "\n".join(x[0] for x in service.bot.sent)
            self.assertIn("Order accepted", messages)
            self.assertIn("Protective orders confirmed", messages)

    def test_connection_failure_does_not_mark_executed(self):
        with tempfile.TemporaryDirectory() as td:
            store = ApprovalStore(Path(td) / "state.sqlite3")
            row = store.create(proposal_payload(), expiry_seconds=120)
            store.decide(
                row["proposal_id"], approve=True, user_id=123, callback_id="ok"
            )
            service = service_with_store(store)
            service.revalidate = lambda _row: {"ok": True}

            with patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.make_client_id",
                return_value="jv-demo-BTCUSDT-p123",
            ), patch(
                "jarvis_core.trading.telegram_approval.demo_exchange.market_order",
                side_effect=RuntimeError("connection failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "connection failure"):
                    service.execute_approved(row["proposal_id"])

            self.assertEqual(
                store.get(row["proposal_id"])["status"],
                "EXECUTION_UNKNOWN",
            )


class ExecutionBoundaryTests(unittest.TestCase):
    def test_new_demo_exposure_without_claimed_approval_is_blocked(self):
        with self.assertRaisesRegex(Exception, "claimed Telegram proposal"):
            demo_exchange.market_order(
                "BTCUSDT",
                "BUY",
                1.0,
                client_id="jv-demo-BTCUSDT-noapproval",
                test_only=False,
                reduce_only=False,
            )

    def test_claimed_exact_proposal_passes_execution_gate(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.sqlite3"
            store = ApprovalStore(db)
            row = store.create(proposal_payload(), expiry_seconds=120)
            store.decide(
                row["proposal_id"],
                approve=True,
                user_id=123,
                callback_id="cb",
            )
            store.claim_for_execution(row["proposal_id"])

            with patch.object(demo_exchange, "DB_PATH", db), patch.object(
                demo_exchange,
                "server_time_ms",
                return_value=int(time.time() * 1000),
            ), patch.object(
                demo_exchange,
                "_signed_request",
                return_value={"status": "FILLED", "orderId": 42},
            ):
                result = demo_exchange.market_order(
                    "BTCUSDT",
                    "BUY",
                    1.0,
                    client_id="jv-demo-BTCUSDT-approved",
                    test_only=False,
                    reduce_only=False,
                    proposal_id=row["proposal_id"],
                )
            self.assertEqual(result["response"]["orderId"], 42)

    def test_approved_quantity_cannot_be_increased(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.sqlite3"
            store = ApprovalStore(db)
            row = store.create(proposal_payload(), expiry_seconds=120)
            store.decide(
                row["proposal_id"],
                approve=True,
                user_id=123,
                callback_id="cb",
            )
            store.claim_for_execution(row["proposal_id"])

            with patch.object(demo_exchange, "DB_PATH", db), patch.object(
                demo_exchange,
                "server_time_ms",
                return_value=int(time.time() * 1000),
            ):
                with self.assertRaisesRegex(Exception, "quantity differs"):
                    demo_exchange.market_order(
                        "BTCUSDT",
                        "BUY",
                        2.0,
                        client_id="jv-demo-BTCUSDT-too-big",
                        test_only=False,
                        reduce_only=False,
                        proposal_id=row["proposal_id"],
                    )


if __name__ == "__main__":
    unittest.main()
