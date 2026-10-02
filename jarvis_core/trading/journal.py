"""Persistent SQLite journal for signals, positions, fills and audit events."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
from typing import Any

from .models import TradeProposal

STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
DB_PATH = STATE_DIR / "trading.sqlite3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingJournal:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                signal_timestamp INTEGER NOT NULL,
                decision TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                direction TEXT NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                opened_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                closed_at TEXT,
                exit REAL,
                status TEXT NOT NULL,
                entry_fees REAL NOT NULL DEFAULT 0,
                exit_fees REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                close_reason TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL
            );
            """
        )
        self.conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def event(self, event_type: str, payload: dict[str, Any], symbol: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events(created_at,event_type,symbol,payload_json) VALUES(?,?,?,?)",
            (_utc_now(), event_type, symbol, json.dumps(payload, sort_keys=True)),
        )
        self.conn.commit()

    def record_signal(self, proposal: TradeProposal) -> bool:
        key = (
            f"{proposal.symbol}:{proposal.strategy_version}:"
            f"{proposal.signal_timestamp}:{proposal.direction or 'WAIT'}"
        )
        try:
            self.conn.execute(
                """
                INSERT INTO signals(
                    signal_key,created_at,symbol,strategy_version,
                    signal_timestamp,decision,payload_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    key,
                    _utc_now(),
                    proposal.symbol,
                    proposal.strategy_version,
                    proposal.signal_timestamp,
                    proposal.decision,
                    json.dumps(proposal.as_dict(), sort_keys=True),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def open_positions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def open_position_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND symbol=? LIMIT 1",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None

    def insert_position(self, row: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO positions(
                client_id,symbol,market_type,strategy_version,direction,
                quantity,leverage,entry,stop_loss,take_profit,opened_at,
                last_checked_at,status,entry_fees
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["client_id"],
                row["symbol"],
                row["market_type"],
                row["strategy_version"],
                row["direction"],
                row["quantity"],
                row["leverage"],
                row["entry"],
                row["stop_loss"],
                row["take_profit"],
                row["opened_at"],
                row["opened_at"],
                "OPEN",
                row["entry_fees"],
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def close_position(
        self,
        position_id: int,
        *,
        exit_price: float,
        exit_fees: float,
        realized_pnl: float,
        reason: str,
    ) -> None:
        now = _utc_now()
        self.conn.execute(
            """
            UPDATE positions
               SET status='CLOSED', closed_at=?, last_checked_at=?,
                   exit=?, exit_fees=?, realized_pnl=?, close_reason=?
             WHERE id=? AND status='OPEN'
            """,
            (now, now, exit_price, exit_fees, realized_pnl, reason, position_id),
        )
        self.conn.commit()

    def touch_position(self, position_id: int) -> None:
        self.conn.execute(
            "UPDATE positions SET last_checked_at=? WHERE id=?",
            (_utc_now(), position_id),
        )
        self.conn.commit()

    def realized_pnl_today(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl),0) AS pnl
              FROM positions
             WHERE status='CLOSED' AND substr(closed_at,1,10)=?
            """,
            (today,),
        ).fetchone()
        return float(row["pnl"] or 0)

    def realized_pnl_total(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) AS pnl FROM positions WHERE status='CLOSED'"
        ).fetchone()
        return float(row["pnl"] or 0)

    def aggregate_open_notional(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(quantity*entry),0) AS n FROM positions WHERE status='OPEN'"
        ).fetchone()
        return float(row["n"] or 0)

    def peak_realized_equity(self, starting_capital: float) -> float:
        rows = self.conn.execute(
            "SELECT realized_pnl FROM positions WHERE status='CLOSED' ORDER BY id"
        ).fetchall()
        equity = starting_capital
        peak = equity
        for row in rows:
            equity += float(row["realized_pnl"] or 0)
            peak = max(peak, equity)
        return peak

    def performance(self, starting_capital: float) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status='CLOSED' ORDER BY id"
        ).fetchall()
        pnls = [float(r["realized_pnl"] or 0) for r in rows]
        total_fees = sum(
            float(r["entry_fees"] or 0) + float(r["exit_fees"] or 0)
            for r in rows
        )
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        realized = sum(pnls)
        equity = starting_capital + realized
        peak = self.peak_realized_equity(starting_capital)
        drawdown = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
        return {
            "closed_trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_percent": (len(wins) / len(rows) * 100.0) if rows else None,
            "gross_profit": gross_win,
            "gross_loss": gross_loss,
            "profit_factor": (gross_win / gross_loss) if gross_loss else None,
            "realized_pnl": realized,
            "net_fees": total_fees,
            "realized_equity": equity,
            "realized_drawdown_percent": max(drawdown, 0.0),
        }

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(r) for r in rows]
