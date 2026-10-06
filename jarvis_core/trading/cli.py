"""Command-line interface for the deterministic Jarvis trading engine."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import signal
import sys
import time

from .config import CONFIG_PATH, initialize_paper_config, load_config, write_config
from .manual import ManualRequest
from .engine import TradingEngine
from .journal import TradingJournal
from . import demo_exchange
from .telegram_approval import (
    DemoTelegramApprovalService,
    TelegramApprovalConfig,
    write_telegram_config,
    discover_private_chats,
)


def _json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def cmd_init(args) -> int:
    path = initialize_paper_config(
        args.capital,
        market_type=args.market,
        path=Path(args.config).expanduser() if args.config else None,
    )
    print(f"Created PAPER trading config: {path}")
    print("New entries remain disabled. Review the limits, then run: jarvis-trader enable-paper")
    return 0


def _engine(args) -> TradingEngine:
    path = Path(args.config).expanduser() if getattr(args, "config", None) else CONFIG_PATH
    return TradingEngine(load_config(path))


def cmd_health(args) -> int:
    _json(_engine(args).health())
    return 0


def cmd_scan(args) -> int:
    _json([p.as_dict() for p in _engine(args).scan()])
    return 0


def cmd_cycle(args) -> int:
    _json(_engine(args).cycle())
    return 0


def cmd_loop(args) -> int:
    engine = _engine(args)
    seconds = args.seconds or engine.cfg.paper_loop_seconds
    print(
        f"Paper loop running every {seconds}s. Existing positions are managed "
        "even while new entries are paused. Ctrl+C to stop."
    )
    try:
        while True:
            _json(engine.cycle())
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("Paper loop stopped.")
    return 0


def cmd_enable(args) -> int:
    engine = _engine(args)
    engine.set_enabled(True)
    print("PAPER new entries enabled.")
    return 0


def cmd_disable(args) -> int:
    engine = _engine(args)
    engine.set_enabled(False)
    print("PAPER new entries disabled; existing protective management is unchanged.")
    return 0


def cmd_pause(args) -> int:
    engine = _engine(args)
    engine.set_paused(True)
    print("New PAPER entries paused. Existing positions remain managed.")
    return 0


def cmd_resume(args) -> int:
    engine = _engine(args)
    engine.set_paused(False)
    print("Eligible PAPER entries resumed.")
    return 0


def cmd_positions(args) -> int:
    _json(_engine(args).journal.open_positions())
    return 0


def cmd_status(args) -> int:
    _json(_engine(args).status())
    return 0


def cmd_performance(args) -> int:
    engine = _engine(args)
    _json(engine.journal.performance(engine.cfg.risk.allocated_capital_quote))
    return 0


def cmd_events(args) -> int:
    _json(_engine(args).journal.recent_events(args.limit))
    return 0


def cmd_close_all(args) -> int:
    engine = _engine(args)
    _json(engine.paper.close_all("MANUAL_CLOSE_MANAGED"))
    return 0


def cmd_cancel_pending(args) -> int:
    engine = _engine(args)
    engine.journal.event(
        "CANCEL_PENDING_REQUEST",
        {
            "result": "no pending entry-order layer in baseline v1; entries are simulated as immediate market fills"
        },
    )
    print("Baseline PAPER v1 has no resting entry orders; nothing to cancel.")
    return 0


def cmd_demo_health(_args) -> int:
    _json(demo_exchange.auth_health())
    return 0


def cmd_demo_balance(_args) -> int:
    _json(demo_exchange.balance())
    return 0


def cmd_demo_symbol_config(args) -> int:
    _json(demo_exchange.symbol_config(args.symbol))
    return 0


def cmd_demo_set_symbol(args) -> int:
    # Explicit DEMO-only account configuration; never called automatically.
    margin = demo_exchange.change_margin_type(args.symbol, args.margin)
    leverage = demo_exchange.change_leverage(args.symbol, args.leverage)
    _json({
        "environment": "DEMO",
        "margin_result": margin,
        "leverage_result": leverage,
        "effective": demo_exchange.symbol_config(args.symbol),
    })
    return 0


def cmd_demo_positions(args) -> int:
    _json(demo_exchange.positions(args.symbol))
    return 0


def cmd_demo_open_orders(args) -> int:
    _json(demo_exchange.open_orders(args.symbol))
    return 0


def cmd_demo_order_test(args) -> int:
    _json(
        demo_exchange.market_order(
            args.symbol,
            args.side,
            args.quantity,
            client_id=args.client_id,
            reduce_only=args.reduce_only,
            test_only=True,
        )
    )
    return 0


def cmd_demo_market(args) -> int:
    raise RuntimeError(
        "Direct Demo entry submission is disabled. New exposure must pass "
        "the Telegram approval service. Use demo-order-test only for non-trading "
        "API validation, or telegram-propose/telegram-loop for approved Demo trades."
    )


def cmd_demo_order(args) -> int:
    _json(demo_exchange.query_order(args.symbol, client_id=args.client_id))
    return 0


def cmd_demo_cancel(args) -> int:
    _json(demo_exchange.cancel_order(args.symbol, client_id=args.client_id))
    return 0


def cmd_demo_close(args) -> int:
    _json(demo_exchange.close_symbol_position(args.symbol))
    return 0


def cmd_telegram_health(_args) -> int:
    service = DemoTelegramApprovalService()
    _json(service.health())
    return 0


def cmd_telegram_scan(_args) -> int:
    _json(DemoTelegramApprovalService().preview_scan())
    return 0


def cmd_telegram_discover(_args) -> int:
    rows = discover_private_chats()
    if not rows:
        print(
            "No private Telegram chat found yet. Send /start to your bot, "
            "then run this command again."
        )
    else:
        _json(rows)
    return 0


def cmd_telegram_config(args) -> int:
    cfg = TelegramApprovalConfig(
        user_id=args.user_id,
        chat_id=args.chat_id,
        approval_expiry_seconds=args.expiry,
        price_tolerance_bps=args.price_tolerance_bps,
        poll_timeout_seconds=args.poll_timeout,
    )
    path = write_telegram_config(cfg)
    print(f"Saved Telegram trading approval config: {path}")
    print("Bot token remains separate in Linux Secret Service.")
    return 0


def cmd_telegram_propose(args) -> int:
    service = DemoTelegramApprovalService()
    row = service.create_and_send(args.symbol.upper())
    if row is None:
        print("No qualifying DEMO trade proposal was generated.")
    else:
        print(
            f"Sent DEMO proposal {row['proposal_id']} to the authorized private chat. "
            f"Status={row['status']}."
        )
    return 0


def cmd_telegram_poll(_args) -> int:
    service = DemoTelegramApprovalService()
    with service.store.poller_lock():
        count = service.poll_once()
    print(f"Processed {count} Telegram update(s).")
    return 0


def cmd_telegram_loop(args) -> int:
    service = DemoTelegramApprovalService()
    with service.store.poller_lock():
        return _telegram_loop(service, args)


def _telegram_loop(service, args) -> int:
    scan_every = max(15, int(args.scan_seconds))
    next_scan = 0.0
    consecutive_errors = 0
    stopping = False
    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    previous_handlers = {sig: signal.signal(sig, request_stop)
                         for sig in (signal.SIGINT, signal.SIGTERM)}
    print(
        "DEMO Telegram approval loop running. New entries require an authorized "
        "Approve button. Ctrl+C to stop."
    )
    try:
        while not stopping:
            try:
                now = time.monotonic()
                if now >= next_scan:
                    created = service.propose_scan()
                    if created:
                        print("Sent proposal(s): " + ", ".join(created))
                    print("Latest Demo scan: " + json.dumps(service.latest_scan()))
                    next_scan = now + scan_every
                if stopping:
                    break
                service.poll_once()
                consecutive_errors = 0
            except Exception as exc:
                # Execution-side ambiguous/failure states are persisted before
                # they reach here. Never auto-retry an order from this loop.
                consecutive_errors += 1
                delay = min(30, 2 ** min(consecutive_errors, 4))
                print(
                    f"DEMO loop warning: {exc}. "
                    f"Continuing in {delay}s; no order is retried automatically.",
                    file=sys.stderr,
                )
                time.sleep(delay)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        print("DEMO Telegram approval loop stopped.")
    return 0


def cmd_telegram_pending(_args) -> int:
    service = DemoTelegramApprovalService()
    _json(service.store.pending())
    return 0


def cmd_manual_config(args) -> int:
    cfg = load_config()
    if args.enable:
        if args.max_leverage is None or args.max_margin is None:
            raise ValueError("Enabling manual Demo proposals requires --max-leverage and --max-margin.")
        cfg.manual.max_leverage = args.max_leverage
        cfg.manual.max_margin_quote = args.max_margin
    cfg.manual.enabled = bool(args.enable)
    cfg.validate()
    write_config(cfg)
    _json({"environment": "DEMO", "manual": vars(cfg.manual),
           "strategy_leverage_unchanged": cfg.risk.max_leverage})
    return 0


def cmd_manual_review(args) -> int:
    request = ManualRequest(symbol=args.symbol.upper(), direction=args.direction,
        leverage=args.leverage, margin_quote=args.margin, notional_quote=args.notional,
        stop_loss=args.stop, take_profit=args.target)
    _json(DemoTelegramApprovalService().review_manual(request))
    return 0


def cmd_manual_propose(args) -> int:
    row = DemoTelegramApprovalService().propose_manual(args.review_id)
    _json({"environment": "DEMO", "proposal_id": row["proposal_id"],
           "status": row["status"], "telegram_message_id": row["telegram_message_id"],
           "execution": "Requires authorized Telegram approval and running telegram-loop."})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis-trader",
        description=(
            "Deterministic Binance market analysis, PAPER execution, and Binance Futures Demo integration."
        ),
    )
    p.add_argument("--config", help="Override trading config path.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("manual-config", help="Explicit local limits for manual Demo proposals.")
    mode = s.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    s.add_argument("--max-leverage", type=int)
    s.add_argument("--max-margin", type=float)
    s.set_defaults(func=cmd_manual_config)

    s = sub.add_parser("manual-review", help="Review a user-directed Demo trade; sends no message or order.")
    s.add_argument("symbol")
    s.add_argument("direction", choices=["LONG", "SHORT"])
    amount = s.add_mutually_exclusive_group(required=True)
    amount.add_argument("--margin", type=float, help="Maximum initial margin in USDT, fees extra.")
    amount.add_argument("--notional", type=float, help="Maximum total position exposure in USDT.")
    s.add_argument("--leverage", type=int, required=True)
    s.add_argument("--stop", type=float)
    s.add_argument("--target", type=float)
    s.set_defaults(func=cmd_manual_review)

    s = sub.add_parser("manual-propose", help="Send an unchanged, reviewed manual trade for Telegram approval.")
    s.add_argument("review_id")
    s.set_defaults(func=cmd_manual_propose)

    s = sub.add_parser("init")
    s.add_argument("--capital", type=float, required=True)
    s.add_argument("--market", choices=["spot", "futures"], default="futures")
    s.add_argument("--config")
    s.set_defaults(func=cmd_init)

    for name, func in (
        ("health", cmd_health),
        ("scan", cmd_scan),
        ("paper-cycle", cmd_cycle),
        ("enable-paper", cmd_enable),
        ("disable-paper", cmd_disable),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("positions", cmd_positions),
        ("status", cmd_status),
        ("performance", cmd_performance),
        ("close-managed", cmd_close_all),
        ("cancel-pending", cmd_cancel_pending),
    ):
        s = sub.add_parser(name)
        s.set_defaults(func=func)

    s = sub.add_parser("paper-loop")
    s.add_argument("--seconds", type=int)
    s.set_defaults(func=cmd_loop)

    s = sub.add_parser("events")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_events)
    s = sub.add_parser("demo-health")
    s.set_defaults(func=cmd_demo_health)

    s = sub.add_parser("demo-balance")
    s.set_defaults(func=cmd_demo_balance)

    s = sub.add_parser("demo-symbol-config")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_demo_symbol_config)

    s = sub.add_parser("demo-set-symbol")
    s.add_argument("symbol")
    s.add_argument("--leverage", type=int, required=True)
    s.add_argument("--margin", choices=["ISOLATED", "CROSSED", "isolated", "crossed"], required=True)
    s.set_defaults(func=cmd_demo_set_symbol)

    s = sub.add_parser("demo-positions")
    s.add_argument("--symbol")
    s.set_defaults(func=cmd_demo_positions)

    s = sub.add_parser("demo-open-orders")
    s.add_argument("--symbol")
    s.set_defaults(func=cmd_demo_open_orders)

    s = sub.add_parser("demo-order-test")
    s.add_argument("symbol")
    s.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    s.add_argument("quantity", type=float)
    s.add_argument("--client-id")
    s.add_argument("--reduce-only", action="store_true")
    s.set_defaults(func=cmd_demo_order_test)

    s = sub.add_parser("demo-market")
    s.add_argument("symbol")
    s.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    s.add_argument("quantity", type=float)
    s.add_argument("--client-id")
    s.add_argument("--reduce-only", action="store_true")
    s.set_defaults(func=cmd_demo_market)

    s = sub.add_parser("demo-order")
    s.add_argument("symbol")
    s.add_argument("client_id")
    s.set_defaults(func=cmd_demo_order)

    s = sub.add_parser("demo-cancel")
    s.add_argument("symbol")
    s.add_argument("client_id")
    s.set_defaults(func=cmd_demo_cancel)

    s = sub.add_parser("demo-close")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_demo_close)

    s = sub.add_parser("telegram-health")
    s.set_defaults(func=cmd_telegram_health)

    s = sub.add_parser("telegram-scan", help="Diagnose Demo strategy gates; sends no message or order.")
    s.set_defaults(func=cmd_telegram_scan)

    s = sub.add_parser("telegram-discover")
    s.set_defaults(func=cmd_telegram_discover)

    s = sub.add_parser("telegram-config")
    s.add_argument("--user-id", type=int, required=True)
    s.add_argument("--chat-id", type=int, required=True)
    s.add_argument("--expiry", type=int, default=120)
    s.add_argument("--price-tolerance-bps", type=float, default=10.0)
    s.add_argument("--poll-timeout", type=int, default=25)
    s.set_defaults(func=cmd_telegram_config)

    s = sub.add_parser("telegram-propose")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_telegram_propose)

    s = sub.add_parser("telegram-poll")
    s.set_defaults(func=cmd_telegram_poll)

    s = sub.add_parser("telegram-loop")
    s.add_argument("--scan-seconds", type=int, default=60)
    s.set_defaults(func=cmd_telegram_loop)

    s = sub.add_parser("telegram-pending")
    s.set_defaults(func=cmd_telegram_pending)

    return p


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"Jarvis trader error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
