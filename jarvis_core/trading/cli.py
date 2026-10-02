"""Command-line interface for the deterministic Jarvis trading engine."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys
import time

from .config import CONFIG_PATH, initialize_paper_config, load_config
from .engine import TradingEngine
from .journal import TradingJournal


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis-trader",
        description=(
            "Deterministic Binance market analysis, risk validation and PAPER execution."
        ),
    )
    p.add_argument("--config", help="Override trading config path.")
    sub = p.add_subparsers(dest="cmd", required=True)

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
