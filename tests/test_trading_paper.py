from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from jarvis_core.trading.config import TradingConfig, RiskConfig
from jarvis_core.trading.journal import TradingJournal
from jarvis_core.trading.market_data import MarketDataError, validate_completed_candles
from jarvis_core.trading.models import Candle, TradeProposal
from jarvis_core.trading.risk import AccountState, apply_risk


def candle(open_time: int, close: float = 100.0) -> Candle:
    return Candle(
        open_time=open_time,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10,
        close_time=open_time + 899_999,
        quote_volume=1000,
        trades=100,
        taker_buy_base=5,
        taker_buy_quote=500,
    )


class CandleValidationTests(unittest.TestCase):
    def test_completed_candles_drop_open_bar_and_preserve_order(self):
        step = 900_000
        rows = [candle(i * step) for i in range(61)]
        server_time = rows[-1].open_time + 200_000
        out = validate_completed_candles(
            rows,
            "15m",
            server_time,
            stale_grace_seconds=120,
        )
        self.assertEqual(len(out), 60)
        self.assertLess(out[-1].close_time, server_time)

    def test_gap_is_rejected(self):
        step = 900_000
        rows = [candle(i * step) for i in range(61)]
        rows.pop(30)
        with self.assertRaises(MarketDataError):
            validate_completed_candles(
                rows,
                "15m",
                rows[-1].close_time + 1,
                stale_grace_seconds=120,
            )


class RiskTests(unittest.TestCase):
    def config(self) -> TradingConfig:
        cfg = TradingConfig(
            enabled=True,
            risk=RiskConfig(
                allocated_capital_quote=10_000,
                max_leverage=2,
                risk_per_trade_percent=0.5,
                max_aggregate_exposure_percent=50,
                max_simultaneous_positions=2,
                daily_loss_limit_percent=2,
                max_drawdown_percent=10,
            ),
        )
        cfg.validate()
        return cfg

    def proposal(self) -> TradeProposal:
        return TradeProposal(
            decision="TRADE",
            symbol="BTCUSDT",
            market_type="futures",
            direction="LONG",
            strategy_version="trend-pullback-v1",
            signal_timestamp=1,
            expires_at=2,
            order_type="MARKET",
            entry_reference=100.0,
            stop_loss=98.0,
            take_profit=104.0,
            leverage=2.0,
        )

    def test_position_size_stays_inside_loss_budget(self):
        p = apply_risk(
            self.proposal(),
            self.config(),
            AccountState(
                open_positions=0,
                aggregate_notional=0,
                realized_pnl_today=0,
                current_equity=10_000,
                peak_equity=10_000,
            ),
            step_size=0.001,
            min_qty=0.001,
            max_qty=1000,
            min_notional=5,
            funding_rate=0.0001,
        )
        self.assertEqual(p.decision, "TRADE")
        self.assertGreater(p.quantity, 0)
        self.assertLessEqual(p.estimated_loss_at_stop, 50.01)

    def test_daily_loss_limit_blocks_new_entry(self):
        p = apply_risk(
            self.proposal(),
            self.config(),
            AccountState(
                open_positions=0,
                aggregate_notional=0,
                realized_pnl_today=-250,
                current_equity=9750,
                peak_equity=10_000,
            ),
            step_size=0.001,
            min_qty=0.001,
            max_qty=1000,
            min_notional=5,
        )
        self.assertEqual(p.decision, "WAIT")
        self.assertTrue(any("daily loss" in x for x in p.risk_rejections))


class JournalTests(unittest.TestCase):
    def test_signal_dedup_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "journal.sqlite3"
            p = TradeProposal(
                decision="WAIT",
                symbol="BTCUSDT",
                market_type="futures",
                direction=None,
                strategy_version="trend-pullback-v1",
                signal_timestamp=123,
                expires_at=456,
                order_type="NONE",
                entry_reference=None,
                stop_loss=None,
                take_profit=None,
                leverage=2,
            )
            first = TradingJournal(path)
            self.assertTrue(first.record_signal(p))
            self.assertFalse(first.record_signal(p))
            first.conn.close()

            second = TradingJournal(path)
            self.assertFalse(second.record_signal(p))
            second.event("RESTART_RECOVERY_TEST", {"ok": True})
            events = second.recent_events()
            self.assertEqual(events[0]["event_type"], "RESTART_RECOVERY_TEST")


if __name__ == "__main__":
    unittest.main()
