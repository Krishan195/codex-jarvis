"""Offline regression tests for Demo scans, Telegram diagnosis, and loop lifecycle."""
import argparse
import signal
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis_core.trading import demo_exchange
from jarvis_core.trading.cli import _telegram_loop
from jarvis_core.trading.config import TradingConfig, RiskConfig
from jarvis_core.trading.journal import TradingJournal
from jarvis_core.trading.engine import TradingEngine
from jarvis_core.trading.manual import DemoMarketData
from jarvis_core.trading.models import TradeProposal
from jarvis_core.trading.risk import AccountState
from jarvis_core.trading.telegram_approval import (
    ApprovalStore, DemoTelegramApprovalService, TelegramApprovalConfig,
    TelegramBot, TelegramAPIError,
    _proposal_message, _keyboard,
)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'trading.sqlite3'
        self.store = ApprovalStore(self.db)
        self.addCleanup(self.store.conn.close)
        journal = TradingJournal(self.db)
        self.addCleanup(journal.conn.close)
        cfg = TradingConfig(risk=RiskConfig(allocated_capital_quote=5000))
        bot = Mock()
        bot.send.return_value = {'message_id': 42}
        with patch('jarvis_core.trading.engine.TradingJournal', return_value=journal):
            self.svc = DemoTelegramApprovalService(cfg, TelegramApprovalConfig(123, 123),
                                                   store=self.store, bot=bot)
        self.svc._demo_state = Mock(return_value=(AccountState(0, 0, 0, current_equity=5000,
                                               peak_equity=5000), 0, 'ISOLATED', 2))
        self.payload = dict(decision='TRADE', symbol='BTCUSDT', market_type='futures',
            direction='LONG', strategy_version='trend-pullback-v1', signal_timestamp=1000,
            expires_at=9999999999999, order_type='MARKET', entry_reference=100,
            stop_loss=98, take_profit=104, leverage=2, quantity=1)
        self.svc.engine.proposal = Mock(return_value=TradeProposal(**self.payload))
        for name, value in [('positions', []), ('open_orders', []), ('open_algo_orders', []),
                            ('position_mode', {'dualSidePosition': False})]:
            p = patch.object(demo_exchange, name, return_value=value)
            p.start(); self.addCleanup(p.stop)

    def test_automatic_execution_uses_demo_data_but_paper_keeps_public_data(self):
        self.assertEqual(self.svc.engine.market.bases, (demo_exchange.DEMO_BASE_URL,))
        paper = TradingEngine(self.svc.trading_cfg, journal=self.svc.engine.journal)
        self.assertNotEqual(paper.market.bases, self.svc.engine.market.bases)

    def test_preview_qualifying_trade_never_sends_or_executes(self):
        with patch.object(demo_exchange, 'market_order') as order:
            out = self.svc.preview_scan()
        self.assertEqual(out[0]['proposal']['decision'], 'TRADE')
        self.svc.bot.send.assert_not_called()
        order.assert_not_called()
        self.assertEqual(self.store.pending(), [])

    def test_wait_reason_is_reported_without_message(self):
        self.svc.engine.proposal.return_value = TradeProposal(**{**self.payload,
            'decision': 'WAIT', 'failure_reasons': ['No completed-candle pullback']})
        self.assertIsNone(self.svc.create_and_send('BTCUSDT'))
        self.assertIn('No completed-candle pullback', self.svc.latest_scan()['BTCUSDT']['reasons'])
        self.svc.bot.send.assert_not_called()

    def test_qualifying_scan_sends_once_and_requires_approval(self):
        with patch.object(demo_exchange, 'market_order') as order:
            row = self.svc.create_and_send('BTCUSDT')
            self.assertIsNone(self.svc.create_and_send('BTCUSDT'))
        self.assertEqual(row['status'], 'PENDING')
        self.assertEqual(row['payload']['data_health']['environment'], 'DEMO')
        self.svc.bot.send.assert_called_once()
        order.assert_not_called()

    def test_approval_card_shows_actual_direction_target_and_net_reward(self):
        for direction, stop, target, marker in [('LONG', 98, 104, '🟢'), ('SHORT', 102, 96, '🔴')]:
            with self.subTest(direction=direction):
                p = {**self.payload, 'direction': direction, 'stop_loss': stop,
                     'take_profit': target, 'estimated_loss_at_stop': 2,
                     'estimated_entry_fee': .1, 'estimated_spread_cost': .2}
                row = {'payload': p, 'proposal_id': 'preview', 'expires_at_ms': 1791276600000}
                message = _proposal_message(row, exposure=0, margin_mode='ISOLATED', tolerance_bps=10)
                self.assertTrue(message.startswith(f'{marker} BTC / USDT — {direction}'))
                self.assertIn(f'TP1  {target} USDT (full position)', message)
                self.assertIn('Risk / Reward (net est.): 1 : 1.85', message)
                self.assertIn('Status: ⏳ Waiting for approval', message)
                self.assertIn('emergency reduce-only close', message)
                self.assertNotIn('TP2', message)
                self.assertNotIn('Confidence:', message)
                self.assertLess(len(message.encode('utf-16-le')) // 2, 4096)

    def test_approval_card_keeps_negative_net_target_and_callback_identity(self):
        p = {**self.payload, 'estimated_loss_at_stop': 2, 'estimated_entry_fee': 5}
        row = {'payload': p, 'proposal_id': 'preview', 'expires_at_ms': 1791276600000}
        message = _proposal_message(row, exposure=0, margin_mode='ISOLATED', tolerance_bps=10)
        self.assertIn('Net target profit (est.): -1.0000 USDT', message)
        buttons = _keyboard('preview')['inline_keyboard'][0]
        self.assertEqual([b['callback_data'] for b in buttons],
                         ['trade:approve:preview', 'trade:reject:preview'])

    def test_failed_delivery_can_retry_same_unexpired_proposal(self):
        self.svc.bot.send.side_effect = TelegramAPIError('offline')
        with self.assertRaises(TelegramAPIError):
            self.svc.create_and_send('BTCUSDT')
        first = self.store.pending()[0]
        self.svc.bot.send.side_effect = None
        row = self.svc.create_and_send('BTCUSDT')
        self.assertEqual(row['proposal_id'], first['proposal_id'])
        self.assertEqual(row['telegram_message_id'], 42)

    def test_exchange_blockers_prevent_unexecutable_proposals(self):
        for method, value in [('positions', [{'position_amt': '1'}]),
                              ('open_algo_orders', [{'algoId': 123}]),
                              ('position_mode', {'dualSidePosition': True})]:
            with self.subTest(method=method), patch.object(demo_exchange, method, return_value=value):
                self.assertIsNone(self.svc.create_and_send('BTCUSDT'))
        self.svc.bot.send.assert_not_called()

    def test_loop_status_uses_lock_not_stale_file(self):
        self.assertFalse(self.store.poller_running())
        with self.store.poller_lock():
            self.assertTrue(self.store.poller_running())
            with self.assertRaises(RuntimeError):
                with self.store.poller_lock():
                    pass
        self.assertFalse(self.store.poller_running())

    def test_api_conflict_is_visible_and_token_is_redacted(self):
        bot = object.__new__(TelegramBot)
        bot.cfg = TelegramApprovalConfig(123, 123)
        bot.base = 'https://api.telegram.org/botDO_NOT_LOG_TOKEN'
        error = urllib.error.HTTPError(bot.base, 409, 'Conflict', {}, None)
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(TelegramAPIError) as caught:
                bot.updates(0)
        self.assertIn('another poller', str(caught.exception))
        self.assertNotIn('DO_NOT_LOG_TOKEN', str(caught.exception))

    def test_shutdown_finishes_current_poll_before_exit(self):
        svc = Mock()
        svc.propose_scan.return_value = []
        svc.latest_scan.return_value = {}
        completed = []
        def poll():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            completed.append(True)
        svc.poll_once.side_effect = poll
        before = signal.getsignal(signal.SIGTERM)
        self.assertEqual(_telegram_loop(svc, argparse.Namespace(scan_seconds=60)), 0)
        self.assertEqual(completed, [True])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)


if __name__ == '__main__':
    unittest.main()
