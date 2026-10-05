"""Telegram-gated Binance Futures Demo execution.

Workflow:
market analysis -> stored proposal -> Telegram approval -> exact revalidation ->
Demo order -> actual order query -> protective orders -> Telegram updates.

The module is intentionally DEMO-only. Production Binance execution is not
implemented here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import fcntl
from contextlib import contextmanager
import os
import secrets as pysecrets
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from jarvis_core import secrets
from .config import CONFIG_PATH, TradingConfig, load_config, write_config
from .engine import TradingEngine
from .journal import DB_PATH, TradingJournal
from .risk import AccountState
from . import demo_exchange
from .manual import DemoMarketData, ManualRequest, ManualReviewStore, build_review, assess_fixed_trade, validate_book

TELEGRAM_TOKEN_SECRET = "telegram-trading-bot-token"
TELEGRAM_CONFIG_PATH = Path.home() / ".config" / "codex-jarvis" / "trading-telegram.json"


class TelegramTransientError(RuntimeError):
    """Temporary Telegram/network failure that is safe to retry."""



@dataclass
class TelegramApprovalConfig:
    user_id: int
    chat_id: int
    approval_expiry_seconds: int = 120
    price_tolerance_bps: float = 10.0
    poll_timeout_seconds: int = 25

    def validate(self) -> None:
        if not self.user_id:
            raise ValueError("Telegram user_id is required.")
        if not self.chat_id:
            raise ValueError("Telegram chat_id is required.")
        if not 15 <= self.approval_expiry_seconds <= 900:
            raise ValueError("approval_expiry_seconds must be between 15 and 900.")
        if not 0 < self.price_tolerance_bps <= 100:
            raise ValueError("price_tolerance_bps must be in (0, 100].")


def load_telegram_config() -> TelegramApprovalConfig:
    if not TELEGRAM_CONFIG_PATH.exists():
        raise FileNotFoundError(
            "Telegram trading config is missing. Run jarvis-trader telegram-config "
            "--user-id YOUR_ID --chat-id YOUR_PRIVATE_CHAT_ID."
        )
    data = json.loads(TELEGRAM_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = TelegramApprovalConfig(
        user_id=int(data["user_id"]),
        chat_id=int(data["chat_id"]),
        approval_expiry_seconds=int(data.get("approval_expiry_seconds", 120)),
        price_tolerance_bps=float(data.get("price_tolerance_bps", 10.0)),
        poll_timeout_seconds=int(data.get("poll_timeout_seconds", 25)),
    )
    cfg.validate()
    return cfg


def write_telegram_config(cfg: TelegramApprovalConfig) -> Path:
    cfg.validate()
    TELEGRAM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TELEGRAM_CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, TELEGRAM_CONFIG_PATH)
    return TELEGRAM_CONFIG_PATH


class ApprovalStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_trade_proposals (
                proposal_id TEXT PRIMARY KEY,
                signal_key TEXT,
                environment TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                telegram_message_id INTEGER,
                approved_at_ms INTEGER,
                approved_by_user_id INTEGER,
                decision_callback_id TEXT,
                execution_started_at_ms INTEGER,
                binance_order_id TEXT,
                binance_client_order_id TEXT,
                result_json TEXT,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_trade_proposals_status
                ON telegram_trade_proposals(status, expires_at_ms);
            CREATE TABLE IF NOT EXISTS telegram_update_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                last_update_id INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO telegram_update_state(singleton,last_update_id)
            VALUES(1,0);

            CREATE TABLE IF NOT EXISTS telegram_risk_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                peak_equity REAL NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO telegram_risk_state(singleton,peak_equity)
            VALUES(1,0);
            CREATE TABLE IF NOT EXISTS manual_leverage_restores (
                proposal_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                previous INTEGER NOT NULL, requested INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEEDED'
            );
            """
        )
        columns = {
            row["name"]
            for row in self.conn.execute(
                "PRAGMA table_info(telegram_trade_proposals)"
            ).fetchall()
        }
        if "signal_key" not in columns:
            self.conn.execute(
                "ALTER TABLE telegram_trade_proposals ADD COLUMN signal_key TEXT"
            )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_trade_proposals_signal
            ON telegram_trade_proposals(signal_key)
            WHERE signal_key IS NOT NULL
            """
        )
        self.conn.commit()

    def create(self, payload: dict[str, Any], *, expiry_seconds: int) -> dict[str, Any]:
        signal_key = (
            f"DEMO:{payload.get('symbol')}:{payload.get('strategy_version')}:"
            f"{payload.get('signal_timestamp')}:{payload.get('direction')}"
        )
        if payload.get("source") == "MANUAL":
            signal_key = "DEMO:MANUAL:" + payload["manual_review_id"]
        existing = self.conn.execute(
            "SELECT proposal_id FROM telegram_trade_proposals WHERE signal_key=?",
            (signal_key,),
        ).fetchone()
        if existing:
            row = self.get(existing["proposal_id"])
            row["_created"] = False
            return row

        proposal_id = pysecrets.token_hex(8)
        now = int(time.time() * 1000)
        row = {
            "proposal_id": proposal_id,
            "signal_key": signal_key,
            "environment": "DEMO",
            "created_at_ms": now,
            "expires_at_ms": min(now + int(expiry_seconds) * 1000,
                                 int(payload.get("expires_at") or now + int(expiry_seconds) * 1000)),
            "status": "PENDING",
            "payload_json": json.dumps(payload, sort_keys=True),
        }
        self.conn.execute(
            """
            INSERT INTO telegram_trade_proposals(
                proposal_id,signal_key,environment,created_at_ms,expires_at_ms,
                status,payload_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                row["proposal_id"], row["signal_key"], row["environment"],
                row["created_at_ms"], row["expires_at_ms"], row["status"],
                row["payload_json"],
            ),
        )
        self.conn.commit()
        out = self.get(proposal_id)
        out["_created"] = True
        return out

    def get(self, proposal_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM telegram_trade_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown proposal {proposal_id}")
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        if out.get("result_json"):
            out["result"] = json.loads(out["result_json"])
        return out

    def set_message_id(self, proposal_id: str, message_id: int) -> None:
        self.conn.execute(
            "UPDATE telegram_trade_proposals SET telegram_message_id=? WHERE proposal_id=?",
            (int(message_id), proposal_id),
        )
        self.conn.commit()

    def expire_due(self) -> int:
        now = int(time.time() * 1000)
        cur = self.conn.execute(
            """
            UPDATE telegram_trade_proposals
               SET status='EXPIRED', reason='approval window expired'
             WHERE status IN ('PENDING','APPROVED') AND expires_at_ms < ?
            """,
            (now,),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def decide(
        self,
        proposal_id: str,
        *,
        approve: bool,
        user_id: int,
        callback_id: str,
    ) -> tuple[bool, str]:
        self.expire_due()
        now = int(time.time() * 1000)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status,expires_at_ms FROM telegram_trade_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if not row:
                self.conn.rollback()
                return False, "Unknown proposal."
            if row["status"] != "PENDING":
                self.conn.rollback()
                return False, f"Proposal is already {row['status']}."
            if int(row["expires_at_ms"]) < now:
                self.conn.execute(
                    "UPDATE telegram_trade_proposals SET status='EXPIRED', reason='approval window expired' WHERE proposal_id=?",
                    (proposal_id,),
                )
                self.conn.commit()
                return False, "Proposal expired."
            status = "APPROVED" if approve else "REJECTED"
            self.conn.execute(
                """
                UPDATE telegram_trade_proposals
                   SET status=?, approved_at_ms=?, approved_by_user_id=?,
                       decision_callback_id=?, reason=?
                 WHERE proposal_id=? AND status='PENDING'
                """,
                (
                    status, now, int(user_id), callback_id,
                    "" if approve else "rejected by authorized user",
                    proposal_id,
                ),
            )
            self.conn.commit()
            return True, status
        except Exception:
            self.conn.rollback()
            raise

    def claim_for_execution(self, proposal_id: str) -> dict[str, Any]:
        now = int(time.time() * 1000)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM telegram_trade_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if not row:
                raise KeyError(proposal_id)
            if row["status"] != "APPROVED":
                raise ValueError(f"Proposal is not executable: {row['status']}")
            if int(row["expires_at_ms"]) < now:
                self.conn.execute(
                    "UPDATE telegram_trade_proposals SET status='EXPIRED',reason='expired before execution' WHERE proposal_id=?",
                    (proposal_id,),
                )
                self.conn.commit()
                raise ValueError("Proposal expired before execution.")
            cur = self.conn.execute(
                """
                UPDATE telegram_trade_proposals
                   SET status='EXECUTING', execution_started_at_ms=?
                 WHERE proposal_id=? AND status='APPROVED'
                """,
                (now, proposal_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Proposal was already claimed.")
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get(proposal_id)

    def mark(
        self,
        proposal_id: str,
        status: str,
        *,
        reason: str = "",
        result: dict[str, Any] | None = None,
        order_id: str | None = None,
        client_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE telegram_trade_proposals
               SET status=?, reason=?, result_json=?,
                   binance_order_id=COALESCE(?,binance_order_id),
                   binance_client_order_id=COALESCE(?,binance_client_order_id)
             WHERE proposal_id=?
            """,
            (
                status,
                reason[:1000],
                json.dumps(result, sort_keys=True) if result is not None else None,
                order_id,
                client_id,
                proposal_id,
            ),
        )
        self.conn.commit()

    def executed_open_candidates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT proposal_id FROM telegram_trade_proposals
             WHERE status='EXECUTED'
             ORDER BY created_at_ms
            """
        ).fetchall()
        return [self.get(r["proposal_id"]) for r in rows]

    def pending(self) -> list[dict[str, Any]]:
        self.expire_due()
        rows = self.conn.execute(
            """
            SELECT proposal_id FROM telegram_trade_proposals
             WHERE status IN ('PENDING','APPROVED','EXECUTING')
             ORDER BY created_at_ms
            """
        ).fetchall()
        return [self.get(r["proposal_id"]) for r in rows]

    @contextmanager
    def execution_lock(self):
        """One account-changing execution/reconciliation at a time across CLIs."""
        with self.path.with_suffix(".execution.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def poller_lock(self):
        """Prevent voice/CLI invocations from starting competing update consumers."""
        with self.path.with_suffix(".telegram-loop.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("A Telegram poller is already running. Keep that process; do not start another.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def update_peak_equity(self, current_equity: float) -> float:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT peak_equity FROM telegram_risk_state WHERE singleton=1"
            ).fetchone()
            peak = max(float(row["peak_equity"] or 0), float(current_equity))
            self.conn.execute(
                "UPDATE telegram_risk_state SET peak_equity=? WHERE singleton=1",
                (peak,),
            )
            self.conn.commit()
            return peak
        except Exception:
            self.conn.rollback()
            raise

    def get_offset(self) -> int:
        row = self.conn.execute(
            "SELECT last_update_id FROM telegram_update_state WHERE singleton=1"
        ).fetchone()
        return int(row["last_update_id"])

    def set_offset(self, update_id: int) -> None:
        self.conn.execute(
            "UPDATE telegram_update_state SET last_update_id=? WHERE singleton=1",
            (int(update_id),),
        )
        self.conn.commit()


def discover_private_chats() -> list[dict[str, Any]]:
    """Return only IDs needed for secure setup; never print message bodies."""
    token = secrets.get(TELEGRAM_TOKEN_SECRET)
    if not token:
        raise RuntimeError(
            "Telegram bot token is missing. Store it with "
            "'jarvis-core secret-set telegram-trading-bot-token'."
        )
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({"timeout": "0"}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Telegram discovery failed ({type(exc).__name__})"
        ) from exc
    if not result.get("ok"):
        raise RuntimeError("Telegram discovery request was rejected.")

    found: dict[tuple[int, int], dict[str, Any]] = {}
    for update in result.get("result") or []:
        message = update.get("message") or {}
        if not message and update.get("callback_query"):
            message = (update["callback_query"].get("message") or {})
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if update.get("callback_query"):
            sender = update["callback_query"].get("from") or sender
        if str(chat.get("type") or "") != "private":
            continue
        try:
            user_id = int(sender.get("id"))
            chat_id = int(chat.get("id"))
        except Exception:
            continue
        found[(user_id, chat_id)] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "chat_type": "private",
        }
    return list(found.values())


class TelegramBot:
    def __init__(self, cfg: TelegramApprovalConfig):
        self.cfg = cfg
        token = secrets.get(TELEGRAM_TOKEN_SECRET)
        if not token:
            raise RuntimeError(
                "Telegram bot token is missing. Store it locally with "
                "'jarvis-core secret-set telegram-trading-bot-token'."
            )
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 35,
    ) -> Any:
        data = urllib.parse.urlencode(
            {
                k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for k, v in (payload or {}).items()
                if v is not None
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/" + method,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            # Long-poll transport timeouts are expected occasionally. Keep the
            # token out of errors and let the caller retry safely.
            raise TelegramTransientError("Telegram long-poll timeout") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise TelegramTransientError("Telegram network timeout") from exc
            # DNS resets / temporary network loss are also retryable; never
            # stringify the URL because it contains the bot token.
            raise TelegramTransientError(
                f"Telegram network error ({type(reason).__name__ or 'URLError'})"
            ) from exc
        except Exception as exc:
            # Do not stringify transport exceptions here; some urllib errors
            # can include the request URL, and the Bot API token is part of it.
            raise RuntimeError(
                f"Telegram API failure ({type(exc).__name__})"
            ) from exc
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API rejected request: {result}")
        return result["result"]

    def send(self, text: str, *, keyboard: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.cfg.chat_id,
                "text": text,
                "reply_markup": keyboard,
                "disable_web_page_preview": "true",
            },
        )

    def answer_callback(self, callback_id: str, text: str, *, alert: bool = False) -> None:
        self._call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text[:200],
                "show_alert": "true" if alert else "false",
            },
        )

    def updates(self, offset: int) -> list[dict[str, Any]]:
        try:
            return self._call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": self.cfg.poll_timeout_seconds,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=self.cfg.poll_timeout_seconds + 10,
            )
        except TelegramTransientError:
            # Telegram long polling may occasionally time out without any
            # updates. Treat that as an empty poll, not a fatal service error.
            return []


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _proposal_message(
    row: dict[str, Any],
    *,
    exposure: float,
    margin_mode: str,
    tolerance_bps: float,
) -> str:
    p = row["payload"]
    entry = float(p["entry_reference"])
    qty = float(p["quantity"])
    notional = entry * qty
    margin = notional / max(float(p["leverage"]), 1.0)
    risk = float(p["estimated_loss_at_stop"])
    target = float(p["take_profit"])
    stop = float(p["stop_loss"])
    gross_target = abs(target - entry) * qty
    estimated_costs = (
        float(p.get("estimated_entry_fee", 0))
        + float(p.get("estimated_exit_fee", 0))
        + float(p.get("estimated_slippage", 0))
        + float(p.get("estimated_funding", 0))
    )
    net_target = max(0.0, gross_target - estimated_costs)
    rr = (net_target / risk) if risk > 0 else 0.0
    tolerance = entry * tolerance_bps / 10_000.0
    expires = datetime.fromtimestamp(
        row["expires_at_ms"] / 1000, timezone.utc
    ).astimezone().isoformat(timespec="seconds")
    brief = "; ".join(p.get("evidence", [])[:4])

    if p.get("source") == "MANUAL":
        review = p["risk_review"]
        metrics = review["metrics"]
        warnings = "\n".join("- " + text for text in review["warnings"][:6]) or "No additional rule-based warning."
        return (
            "DEMO — MANUAL TRADE APPROVAL REQUIRED\n"
            f"Proposal: {row['proposal_id']}\nExpires: {expires}\n"
            f"{p['symbol']} {p['direction']} MARKET; ISOLATED {p['leverage']}x\n"
            f"Quantity: {qty:g}; exposure about {notional:.2f} USDT\n"
            f"Estimated margin: {margin:.2f} USDT (fees extra)\n"
            f"Pre-submit price range: {_fmt_price(entry - tolerance)}–{_fmt_price(entry + tolerance)} (market fills can slip)\n"
            f"Stop: {_fmt_price(stop)}; target: {_fmt_price(target)}\n"
            f"ATR-suggested stop/target: {p['suggested_stop']}/{p['suggested_target']}\n"
            f"Estimated stop loss: {metrics['estimated_loss_at_stop']:.4f} USDT "
            f"({metrics['loss_percent_of_margin']:.1f}% of initial margin)\n"
            f"Estimated net target: {metrics['estimated_net_target_profit']:.4f} USDT\n"
            f"1% adverse move before costs: {metrics['adverse_one_percent_loss_before_costs']:.4f} USDT\n"
            f"Assessment: {review['recommendation']}\n{warnings}\n\n"
            f"Approval overrides strategy entry gates for THIS request only. It authorizes "
            f"setting this flat Demo symbol from {p['previous_leverage']:g}x to {p['leverage']}x, "
            f"then restoring {p['restore_leverage']:g}x once flat and clear of orders. "
            "It also covers the listed stop/target and an emergency reduce-only close if protection fails or the fill violates the approved range. "
            "Stops can slip; liquidation price is not guaranteed or predicted. No live order."
        )

    return (
        "DEMO — TRADE APPROVAL REQUIRED\n\n"
        f"Proposal: {row['proposal_id']}\n"
        f"Expires: {expires}\n"
        f"{p['symbol']} {p['market_type'].upper()} {p['direction']}\n"
        f"Strategy: {p['strategy_version']} — {brief}\n\n"
        f"Order: {p['order_type']}\n"
        f"Entry reference: {_fmt_price(entry)}\n"
        f"Permitted execution: {_fmt_price(entry - tolerance)} to {_fmt_price(entry + tolerance)}\n"
        f"Quantity: {qty}\n"
        f"Notional: {notional:.2f} USDT\n"
        f"Leverage: {p['leverage']}x\n"
        f"Margin mode: {margin_mode}\n"
        f"Estimated margin: {margin:.2f} USDT\n\n"
        f"Stop: {_fmt_price(stop)}\n"
        f"Target: {_fmt_price(target)}\n"
        "Trailing stop: none in baseline v1\n"
        f"Estimated loss at stop: {risk:.2f} USDT\n"
        f"Estimated net target profit: {net_target:.2f} USDT\n"
        f"Estimated costs: {estimated_costs:.2f} USDT\n"
        f"Risk/reward: {rr:.2f}R\n"
        f"Current account exposure: {exposure:.2f} USDT\n\n"
        "Approval covers this entry plus the listed stop/target and emergency "
        "reduce-only close if protection cannot be established."
    )


def _keyboard(proposal_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {
                "text": "Approve",
                "callback_data": f"trade:approve:{proposal_id}",
            },
            {
                "text": "Reject",
                "callback_data": f"trade:reject:{proposal_id}",
            },
        ]]
    }


class DemoTelegramApprovalService:
    def __init__(
        self,
        trading_cfg: TradingConfig | None = None,
        telegram_cfg: TelegramApprovalConfig | None = None,
        *,
        store: ApprovalStore | None = None,
        bot: TelegramBot | None = None,
    ):
        self._reload_from_disk = trading_cfg is None
        self.trading_cfg = trading_cfg or load_config(CONFIG_PATH)
        self.telegram_cfg = telegram_cfg or load_telegram_config()
        self.store = store or ApprovalStore()
        self.bot = bot or TelegramBot(self.telegram_cfg)
        self.engine = TradingEngine(self.trading_cfg)

    def _refresh_config(self) -> None:
        if getattr(self, "_reload_from_disk", False):
            self.trading_cfg = load_config(CONFIG_PATH)
            self.engine.cfg = self.trading_cfg

    def _notify(self, text: str) -> None:
        # A Telegram outage MUST NOT interrupt order reconciliation/protection.
        try:
            self.bot.send(text)
        except Exception:
            try:
                self.engine.journal.event("TELEGRAM_NOTIFICATION_FAILED", {"message": text})
            except Exception:
                pass  # Notification/audit failure cannot strand an unprotected fill.

    def _manual_context(self, symbol: str, *, ignore_proposal: str | None = None):
        state, exposure, margin, leverage = self._demo_state(symbol)
        balances = demo_exchange.balance()
        available = next((float(x.get("available_balance") or 0) for x in balances
                          if x.get("asset") == "USDT"), 0.0)
        blockers = []
        if any(abs(float(p["position_amt"])) > 0 for p in demo_exchange.positions(symbol)):
            blockers.append("An existing Demo position already occupies this symbol.")
        if demo_exchange.open_orders(symbol) or demo_exchange.open_algo_orders(symbol):
            blockers.append("Outstanding Demo orders exist for this symbol.")
        if demo_exchange.position_mode().get("dualSidePosition") not in (False, "false"):
            blockers.append("Manual execution currently requires one-way position mode.")
        config = demo_exchange.symbol_config(symbol)
        if config.get("is_auto_add_margin") in (True, "true"):
            blockers.append("Automatic addition of isolated margin must be disabled for manual trades.")
        for row in self.store.pending():
            if row["proposal_id"] != ignore_proposal and row["payload"]["symbol"] == symbol:
                blockers.append("Another pending or executing proposal occupies this symbol.")
        return state, exposure, margin, leverage, available, blockers

    def review_manual(self, request: ManualRequest) -> dict[str, Any]:
        self._refresh_config()
        request.validate()
        state, exposure, margin, leverage, available, blockers = self._manual_context(request.symbol)
        report = build_review(request, self.trading_cfg, state, market=DemoMarketData(),
            available=available, actual_margin=margin, actual_leverage=leverage,
            tolerance_bps=self.telegram_cfg.price_tolerance_bps, account_blockers=blockers)
        ManualReviewStore(self.store.conn).save(report)
        return report

    def propose_manual(self, review_id: str) -> dict[str, Any]:
        self._refresh_config()
        report = ManualReviewStore(self.store.conn).get(review_id)
        if not report["can_propose"]:
            raise ValueError("Review blocked: " + "; ".join(report["blockers"]))
        payload = report["payload"]
        # Recheck the frozen review. Never silently replace it with a new size,
        # side, stop or target after the voice discussion.
        existing = self.store.conn.execute("SELECT proposal_id FROM telegram_trade_proposals WHERE signal_key=?",
            ("DEMO:MANUAL:" + review_id,)).fetchone()
        if existing:
            row = self.store.get(existing[0])
            if row["status"] != "PENDING":
                raise ValueError("This review was already used. Obtain a new review for a new request.")
        else:
            row = {"payload": payload, "expires_at_ms": report["expires_at_ms"]}
        check = self._revalidate_manual(row)
        row = self.store.create(payload, expiry_seconds=self.telegram_cfg.approval_expiry_seconds)
        self._deliver(row, exposure=check["exposure"], margin_mode="ISOLATED")
        return self.store.get(row["proposal_id"])

    def _revalidate_manual(self, row: dict[str, Any]) -> dict[str, Any]:
        self._refresh_config()
        p = row["payload"]
        state, exposure, margin, leverage, available, errors = self._manual_context(
            p["symbol"], ignore_proposal=row.get("proposal_id"))
        if margin != "ISOLATED" or leverage != float(p["previous_leverage"]):
            errors.append("Account margin/leverage changed since review; obtain a fresh review.")
        market = DemoMarketData()
        book = market.depth(p["symbol"], limit=20)
        now = market.server_time_ms()
        if now > min(int(row["expires_at_ms"]), int(p["expires_at"])):
            errors.append("Manual review/approval expired.")
        validate_book(book, now)
        price = book.best_ask if p["direction"] == "LONG" else book.best_bid
        if abs(price - p["entry_reference"]) / p["entry_reference"] * 10000 > p["approval_price_tolerance_bps"]:
            errors.append("Price moved outside the reviewed tolerance; obtain a fresh review.")
        # The manual Demo commander lane already disclosed spread at review
        # time. Price-tolerance and exchange validation still fail closed here.
        fresh_errors, metrics = assess_fixed_trade(p, self.trading_cfg, state,
            rules=market.symbol_rules(p["symbol"]), available=available,
            funding=market.futures_context(p["symbol"]).last_funding_rate)

        # Manual Demo commander mode intentionally treats execution spread /
        # liquidity preference as advisory. The review already disclosed those
        # conditions to Boss before Telegram approval. Do not let a generic
        # downstream risk message silently re-promote the same condition into a
        # hard blocker after Boss presses Approve. All other account, exchange,
        # sizing, geometry, balance, expiry and price-tolerance failures remain
        # fail-closed.
        fresh_errors = [
            msg for msg in fresh_errors
            if "spread" not in msg.lower() and "liquidity" not in msg.lower()
        ]
        errors.extend(fresh_errors)
        if metrics.get("estimated_loss_at_stop", 0) > p["estimated_loss_at_stop"] + 1e-8:
            errors.append("Estimated loss exceeds the reviewed budget; obtain a fresh review.")
        if errors:
            raise ValueError("; ".join(dict.fromkeys(errors)))
        return {"current_entry": price, "exposure": exposure, "margin_mode": margin,
                "available_balance_checked": True, "manual_metrics": metrics}

    def _deliver(self, row: dict[str, Any], *, exposure: float, margin_mode: str) -> None:
        if row["status"] != "PENDING" or row.get("telegram_message_id"):
            return
        if row["expires_at_ms"] <= int(time.time() * 1000):
            self.store.expire_due()
            return
        msg = self.bot.send(_proposal_message(row, exposure=exposure, margin_mode=margin_mode,
            tolerance_bps=row["payload"].get("approval_price_tolerance_bps", self.telegram_cfg.price_tolerance_bps)),
            keyboard=_keyboard(row["proposal_id"]))
        self.store.set_message_id(row["proposal_id"], int(msg["message_id"]))

    def restore_manual_leverage(self) -> None:
        """Only restore the explicitly approved setting after confirmed flatness."""
        with self.store.execution_lock():
            rows = self.store.conn.execute("""SELECT r.* FROM manual_leverage_restores r
                JOIN telegram_trade_proposals p USING(proposal_id)
                WHERE r.status='NEEDED' AND p.status IN ('CLOSED','ORDER_FAILED','INVALIDATED')""").fetchall()
            for row in rows:
                try:
                    symbol = row["symbol"]
                    if any(abs(float(p["position_amt"])) > 0 for p in demo_exchange.positions(symbol)):
                        continue
                    if demo_exchange.open_orders(symbol) or demo_exchange.open_algo_orders(symbol):
                        continue
                    current = demo_exchange.symbol_config(symbol)["leverage"]
                    if current not in (row["previous"], row["requested"]):
                        # Do not overwrite a subsequent manual account change.
                        status = "MANUAL_INTERVENTION"
                    else:
                        if current != row["previous"]:
                            demo_exchange.change_leverage(symbol, row["previous"])
                        if demo_exchange.symbol_config(symbol)["leverage"] != row["previous"]:
                            raise RuntimeError("Restored leverage was not confirmed.")
                        status = "RESTORED"
                    self.store.conn.execute("UPDATE manual_leverage_restores SET status=? WHERE proposal_id=?",
                        (status, row["proposal_id"]))
                    self.store.conn.commit()
                    self._notify(f"DEMO — {symbol} leverage restoration: {status}.")
                except Exception as exc:
                    self.engine.journal.event("MANUAL_LEVERAGE_RESTORE_RETRY", {"error_type": type(exc).__name__}, row["symbol"])

    def health(self) -> dict[str, Any]:
        symbols: dict[str, Any] = {}
        for symbol in self.trading_cfg.permitted_symbols:
            row = demo_exchange.symbol_config(symbol)
            actual_margin = str(row.get("margin_type") or "UNKNOWN").upper()
            actual_leverage = float(row.get("leverage") or 0)
            symbols[symbol] = {
                "configured_margin_mode": self.trading_cfg.margin_mode,
                "actual_margin_mode": actual_margin,
                "margin_mode_matches": actual_margin == self.trading_cfg.margin_mode,
                "configured_leverage": float(self.trading_cfg.risk.max_leverage),
                "actual_leverage": actual_leverage,
                "leverage_matches": actual_leverage == float(self.trading_cfg.risk.max_leverage),
            }
        return {
            "environment": "DEMO",
            "live_execution_available": False,
            "telegram_user_id": self.telegram_cfg.user_id,
            "telegram_chat_id": self.telegram_cfg.chat_id,
            "approval_expiry_seconds": self.telegram_cfg.approval_expiry_seconds,
            "price_tolerance_bps": self.telegram_cfg.price_tolerance_bps,
            "paused": self.trading_cfg.paused,
            "manual_limits": asdict(self.trading_cfg.manual),
            "demo_api": demo_exchange.auth_health(),
            "symbols": symbols,
        }

    def _demo_state(self, symbol: str) -> tuple[AccountState, float, str, float]:
        balances = demo_exchange.balance()
        usdt = next((x for x in balances if x.get("asset") == "USDT"), None)
        if not usdt:
            raise RuntimeError("Demo account has no USDT balance.")
        balance = float(usdt.get("balance") or 0)
        available = float(usdt.get("available_balance") or 0)
        positions = demo_exchange.positions()
        active = [x for x in positions if abs(float(x["position_amt"])) > 0]
        exposure = sum(
            abs(float(x["position_amt"]) * float(x["mark_price"]))
            for x in active
        )
        unrealized = sum(float(x["unrealized_profit"]) for x in active)

        now = datetime.now(timezone.utc)
        day_start = datetime(
            now.year, now.month, now.day, tzinfo=timezone.utc
        )
        day_start_ms = int(day_start.timestamp() * 1000)
        realized_today = 0.0
        for permitted in self.trading_cfg.permitted_symbols:
            for trade in demo_exchange.user_trades(
                permitted,
                start_time_ms=day_start_ms,
            ):
                realized_today += float(trade.get("realizedPnl") or 0)
                if str(trade.get("commissionAsset") or "").upper() == "USDT":
                    realized_today -= float(trade.get("commission") or 0)

        current_equity = (
            min(balance, self.trading_cfg.risk.allocated_capital_quote)
            + unrealized
        )
        peak_equity = self.store.update_peak_equity(
            max(current_equity, self.trading_cfg.risk.allocated_capital_quote)
        )

        pending = demo_exchange.open_orders()
        unapproved_pending_entries = [
            order for order in pending
            if not bool(order.get("reduceOnly", False))
        ]

        state = AccountState(
            open_positions=len(active),
            aggregate_notional=exposure,
            realized_pnl_today=realized_today,
            unrealized_pnl=unrealized,
            current_equity=current_equity,
            peak_equity=peak_equity,
            state_certain=not unapproved_pending_entries,
        )
        unresolved = self.store.conn.execute("""SELECT COUNT(*) FROM telegram_trade_proposals
            WHERE status IN ('EXECUTING','EXECUTION_UNKNOWN','PROTECTION_EMERGENCY_FAILED')
            AND proposal_id != ?""", (getattr(self, "_executing_proposal", ""),)).fetchone()[0]
        if unresolved:
            state.state_certain = False
        symbol_cfg = demo_exchange.symbol_config(symbol)
        margin_mode = str(symbol_cfg.get("margin_type") or "UNKNOWN").upper()
        actual_leverage = float(symbol_cfg.get("leverage") or 0)
        if available <= 0:
            state.state_certain = False
        return state, exposure, margin_mode, actual_leverage

    def create_and_send(self, symbol: str) -> dict[str, Any] | None:
        self._refresh_config()
        if self.trading_cfg.paused:
            return None
        demo_state, exposure, margin_mode, actual_leverage = self._demo_state(symbol)
        if margin_mode != self.trading_cfg.margin_mode:
            self.engine.journal.event("TELEGRAM_SCAN_SKIPPED", {"reason": "margin mode mismatch"}, symbol)
            return None
        if actual_leverage != float(self.trading_cfg.risk.max_leverage):
            self.engine.journal.event("TELEGRAM_SCAN_SKIPPED", {"reason": "leverage mismatch"}, symbol)
            return None
        proposal = self.engine.proposal(
            symbol,
            account_state_override=demo_state,
            require_enabled=False,
        )
        if proposal.decision != "TRADE":
            self.engine.journal.event("TELEGRAM_SCAN_WAIT", proposal.as_dict(), symbol)
            return None
        payload = proposal.as_dict()
        payload["approval_price_tolerance_bps"] = self.telegram_cfg.price_tolerance_bps
        payload["margin_mode"] = margin_mode
        row = self.store.create(
            payload,
            expiry_seconds=self.telegram_cfg.approval_expiry_seconds,
        )
        if not row.pop("_created", False) and (row["status"] != "PENDING" or row.get("telegram_message_id")):
            return None
        self._deliver(row, exposure=exposure, margin_mode=margin_mode)
        return self.store.get(row["proposal_id"])

    @staticmethod
    def _same_trade(old: dict[str, Any], fresh: dict[str, Any]) -> tuple[bool, str]:
        immutable = (
            "symbol", "market_type", "direction", "strategy_version",
            "signal_timestamp", "order_type", "quantity", "leverage",
            "stop_loss", "take_profit",
        )
        for key in immutable:
            if old.get(key) != fresh.get(key):
                return False, f"{key} changed"
        return True, ""

    def revalidate(self, row: dict[str, Any]) -> dict[str, Any]:
        self._refresh_config()
        if row["payload"].get("source") == "MANUAL":
            return self._revalidate_manual(row)
        old = row["payload"]
        demo_state, exposure, margin_mode, actual_leverage = self._demo_state(old["symbol"])

        if margin_mode != str(old.get("margin_mode") or "").upper():
            raise RuntimeError("Margin mode changed after approval.")
        if actual_leverage != float(old["leverage"]):
            raise RuntimeError("Leverage changed after approval.")

        if demo_exchange.open_orders(old["symbol"]):
            raise RuntimeError("Pending Demo orders exist for the symbol.")
        if any(
            abs(float(x["position_amt"])) > 0
            for x in demo_exchange.positions(old["symbol"])
        ):
            raise RuntimeError("A Demo position already exists for the symbol.")

        fresh = self.engine.proposal(
            old["symbol"],
            account_state_override=demo_state,
            require_enabled=False,
        ).as_dict()
        if fresh["decision"] != "TRADE":
            raise RuntimeError("Signal no longer qualifies after approval.")
        same, reason = self._same_trade(old, fresh)
        if not same:
            raise RuntimeError(f"Proposal changed during revalidation: {reason}.")

        now = demo_exchange.server_time_ms()
        if now > int(row["expires_at_ms"]):
            raise RuntimeError("Proposal expired before execution.")

        book = self.engine.market.depth(old["symbol"], limit=20)
        current = book.best_ask if old["direction"] == "LONG" else book.best_bid
        reference = float(old["entry_reference"])
        delta_bps = abs(current - reference) / reference * 10_000.0
        tolerance = float(old["approval_price_tolerance_bps"])
        if delta_bps > tolerance:
            raise RuntimeError(
                f"Price moved {delta_bps:.2f} bps, beyond approved {tolerance:.2f} bps."
            )
        return {
            "fresh": fresh,
            "current_entry": current,
            "exposure": exposure,
            "margin_mode": margin_mode,
            "available_balance_checked": True,
        }

    def _confirm_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        deadline = time.time() + 15
        latest: dict[str, Any] = {}
        while time.time() < deadline:
            latest = demo_exchange.query_order(symbol, client_id=client_id)
            if str(latest.get("status")) in {
                "FILLED", "CANCELED", "REJECTED", "EXPIRED",
            }:
                return latest
            time.sleep(0.75)

        if str(latest.get("status")) not in {
            "FILLED", "CANCELED", "REJECTED", "EXPIRED",
        }:
            # Freeze the actual exposure before placing protection; otherwise a
            # later fill could exceed the approved/protected quantity.
            try:
                demo_exchange.cancel_order(symbol, client_id=client_id)
            finally:
                latest = demo_exchange.query_order(symbol, client_id=client_id)
        return latest

    def execute_approved(self, proposal_id: str) -> dict[str, Any]:
        with self.store.execution_lock():
            self._executing_proposal = proposal_id
            try:
                return self._execute_approved(proposal_id)
            finally:
                self._executing_proposal = ""

    def _execute_approved(self, proposal_id: str) -> dict[str, Any]:
        row = self.store.claim_for_execution(proposal_id)
        p = row["payload"]
        try:
            check = self.revalidate(row)
            if p.get("source") == "MANUAL" and p["previous_leverage"] != p["leverage"]:
                self.store.conn.execute("""INSERT OR IGNORE INTO manual_leverage_restores
                    (proposal_id,symbol,previous,requested) VALUES(?,?,?,?)""",
                    (proposal_id, p["symbol"], int(p["previous_leverage"]), int(p["leverage"])))
                self.store.conn.commit()
                demo_exchange.change_approved_leverage(proposal_id, p["symbol"], int(p["leverage"]))
                if demo_exchange.symbol_config(p["symbol"])["leverage"] != p["leverage"]:
                    raise RuntimeError("Demo leverage change was not confirmed. No entry submitted.")
        except Exception as exc:
            self.store.mark(
                proposal_id,
                "INVALIDATED",
                reason=str(exc),
            )
            self._notify(
                f"DEMO — Proposal {proposal_id} invalidated. No order submitted.\n{exc}"
            )
            return {"status": "INVALIDATED", "reason": str(exc)}

        side = "BUY" if p["direction"] == "LONG" else "SELL"
        client_id = demo_exchange.make_client_id(
            p["symbol"],
            "p" + proposal_id[:10],
        )
        try:
            result = demo_exchange.market_order(
                p["symbol"],
                side,
                float(p["quantity"]),
                client_id=client_id,
                reduce_only=False,
                test_only=False,
                proposal_id=proposal_id,
            )
        except Exception as exc:
            # demo_exchange already tries query-by-client-id reconciliation on
            # ambiguous network errors. If it still raises, do not retry here.
            self.store.mark(
                proposal_id,
                "EXECUTION_UNKNOWN",
                reason=str(exc),
                client_id=client_id,
            )
            self._notify(
                f"DEMO — Execution state UNKNOWN for proposal {proposal_id}. "
                "No automatic retry will occur. Reconcile the client order ID "
                "before any new entry."
            )
            raise
        response = result.get("response") or {}
        order_id = str(response.get("orderId") or "")
        self._notify(
            f"DEMO — Order accepted for {p['symbol']}. "
            f"Binance order ID: {order_id or 'pending-query'}."
        )

        try:
            actual = self._confirm_order(p["symbol"], client_id)
            if str(actual.get("status")) not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                raise RuntimeError("Entry has not reached a confirmed terminal state.")
        except Exception:
            self.store.mark(proposal_id, "EXECUTION_UNKNOWN",
                            reason="Order confirmation failed; reconcile before new entries.",
                            order_id=order_id, client_id=client_id)
            self._notify(f"DEMO — CRITICAL: entry confirmation uncertain for {p['symbol']}. "
                         f"Inspect Demo orders/positions immediately. Client ID: {client_id}. New entries blocked.")
            raise
        status = str(actual.get("status") or "UNKNOWN")
        executed_qty = float(actual.get("executedQty") or 0)
        avg_price = float(actual.get("avgPrice") or 0)
        if executed_qty > 0:
            original_qty = float(actual.get("origQty") or p["quantity"])
            fill_label = (
                "PARTIAL FILL"
                if executed_qty + 1e-12 < original_qty
                else "FULL FILL"
            )
            self._notify(
                f"DEMO — {fill_label}: {p['symbol']} actual quantity "
                f"{executed_qty}, average price {_fmt_price(avg_price)}. "
                f"Final order status: {status}."
            )
        else:
            self._notify(
                f"DEMO — Order status for {p['symbol']}: {status}; "
                "no fill has been reported yet."
            )

        if executed_qty <= 0 and status in {
            "CANCELED", "REJECTED", "EXPIRED",
        }:
            final = {
                "entry": actual,
                "protection": {},
                "revalidation": check,
            }
            self.store.mark(
                proposal_id,
                "ORDER_FAILED",
                reason=f"Binance terminal order status: {status}",
                result=final,
                order_id=order_id,
                client_id=client_id,
            )
            self._notify(
                f"DEMO — Entry did not fill for {p['symbol']}. "
                f"Final order status: {status}."
            )
            return final

        protection: dict[str, Any] = {}
        if executed_qty > 0:
            try:
                if p.get("source") == "MANUAL":
                    tolerance = p["approval_price_tolerance_bps"] / 10000
                    if not p["entry_reference"] * (1-tolerance) <= avg_price <= p["entry_reference"] * (1+tolerance):
                        raise RuntimeError("Actual fill is outside the reviewed price range.")
                protection = demo_exchange.place_protection(
                    p["symbol"],
                    direction=p["direction"],
                    quantity=executed_qty,
                    stop_loss=float(p["stop_loss"]),
                    take_profit=float(p["take_profit"]),
                    proposal_id=proposal_id,
                )
                self._notify(
                    f"DEMO — Protective orders confirmed for {p['symbol']}: "
                    f"stop {_fmt_price(float(p['stop_loss']))}, "
                    f"target {_fmt_price(float(p['take_profit']))}."
                )
            except Exception as exc:
                self._notify(
                    f"DEMO — PROTECTION FAILURE for {p['symbol']}: {exc}. "
                    "Emergency policy: submit a reduce-only market close now."
                )
                try:
                    emergency = demo_exchange.close_symbol_position(p["symbol"])
                    protection = {
                        "protection_error": str(exc),
                        "emergency_close": emergency,
                        **demo_exchange.protection_ids(p["symbol"], proposal_id),
                        "algo": True,
                    }
                    self._notify(
                        f"DEMO — Emergency reduce-only close submitted for "
                        f"{p['symbol']} after protection failure."
                    )
                except Exception as emergency_exc:
                    protection = {
                        "protection_error": str(exc),
                        "emergency_close_error": str(emergency_exc),
                    }
                    self.store.mark(
                        proposal_id,
                        "PROTECTION_EMERGENCY_FAILED",
                        reason=(
                            f"protection={exc}; emergency_close={emergency_exc}"
                        ),
                        result={
                            "entry": actual,
                            "protection": protection,
                            "revalidation": check,
                        },
                        order_id=order_id,
                        client_id=client_id,
                    )
                    self._notify(
                        f"DEMO — CRITICAL: emergency close also failed for "
                        f"{p['symbol']}. Manual Demo-account intervention is required."
                    )
                    return {
                        "entry": actual,
                        "protection": protection,
                        "revalidation": check,
                    }

        final = {
            "entry": actual,
            "protection": protection,
            "revalidation": check,
        }
        self.store.mark(
            proposal_id,
            "EXECUTED",
            result=final,
            order_id=order_id,
            client_id=client_id,
        )
        return final

    def reconcile_executed(self) -> None:
        with self.store.execution_lock():
            self._reconcile_executed()

    def _reconcile_executed(self) -> None:
        for row in self.store.executed_open_candidates():
            p = row["payload"]
            result = row.get("result") or {}
            entry = result.get("entry") or {}
            if float(entry.get("executedQty") or 0) <= 0:
                continue
            active = [
                x for x in demo_exchange.positions(p["symbol"])
                if abs(float(x["position_amt"])) > 0
            ]
            if active:
                continue

            protection = result.get("protection") or {}
            for key in ("stop_client_id", "target_client_id"):
                cid = protection.get(key)
                if cid:
                    try:
                        demo_exchange.cancel_protection(p["symbol"], client_id=cid,
                                                       algo=bool(protection.get("algo", False)))
                    except Exception:
                        pass

            start_ms = int(row["execution_started_at_ms"] or row["created_at_ms"])
            trades = demo_exchange.user_trades(
                p["symbol"],
                start_time_ms=start_ms,
            )
            realized = sum(float(x.get("realizedPnl") or 0) for x in trades)
            commissions = sum(float(x.get("commission") or 0) for x in trades)
            self.store.mark(
                row["proposal_id"],
                "CLOSED",
                reason="Demo position no longer open",
                result={
                    **result,
                    "close_summary": {
                        "realized_pnl": realized,
                        "commissions": commissions,
                    },
                },
            )
            self._notify(
                f"DEMO — Position closed for {p['symbol']}. "
                f"Realized P&L: {realized:.4f}; commissions: {commissions:.4f}."
            )

    def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        user_id = int((callback.get("from") or {}).get("id") or 0)
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        chat_type = str(chat.get("type") or "")
        data = str(callback.get("data") or "")

        if (
            user_id != self.telegram_cfg.user_id
            or chat_id != self.telegram_cfg.chat_id
            or chat_type != "private"
        ):
            self.bot.answer_callback(
                callback_id,
                "Unauthorized approval.",
                alert=True,
            )
            return

        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "trade" or parts[1] not in {"approve", "reject"}:
            self.bot.answer_callback(callback_id, "Unknown action.", alert=True)
            return
        action, proposal_id = parts[1], parts[2]
        ok, state = self.store.decide(
            proposal_id,
            approve=(action == "approve"),
            user_id=user_id,
            callback_id=callback_id,
        )
        if not ok:
            self.bot.answer_callback(callback_id, state, alert=True)
            return
        try:
            self.bot.answer_callback(callback_id, state)
        except Exception:
            pass  # The authorized decision was persisted; acknowledgement is advisory.
        if state == "REJECTED":
            self.bot.send(f"DEMO — Proposal {proposal_id} rejected. No order submitted.")
            return
        self.execute_approved(proposal_id)

    def _authorized_message(self, message: dict[str, Any]) -> bool:
        user_id = int((message.get("from") or {}).get("id") or 0)
        chat = message.get("chat") or {}
        return (
            user_id == self.telegram_cfg.user_id
            and int(chat.get("id") or 0) == self.telegram_cfg.chat_id
            and str(chat.get("type") or "") == "private"
        )

    def handle_message(self, message: dict[str, Any]) -> None:
        if not self._authorized_message(message):
            return
        self._refresh_config()
        parts = str(message.get("text") or "").strip().split()
        if not parts:
            return
        text = parts[0].lower()
        if text == "/status":
            health = demo_exchange.auth_health()
            pending = len(self.store.pending())
            self.bot.send(
                f"DEMO — trading approval service healthy. "
                f"Pending proposals: {pending}. Clock skew: {health['clock_skew_ms']} ms."
            )
        elif text == "/pending":
            rows = self.store.pending()
            if not rows:
                self.bot.send("DEMO — No pending trade proposals.")
            else:
                body = "\n".join(
                    f"{r['proposal_id']} — {r['status']} — {r['payload']['symbol']}"
                    for r in rows[:20]
                )
                self.bot.send("DEMO — Pending proposals:\n" + body)
        elif text == "/positions":
            rows = demo_exchange.positions()
            active = [x for x in rows if abs(float(x["position_amt"])) > 0]
            self.bot.send(
                "DEMO — Positions:\n"
                + (json.dumps(active, indent=2)[:3500] if active else "No open positions.")
            )
        elif text == "/pause":
            self.trading_cfg.paused = True
            write_config(self.trading_cfg)
            self.bot.send(
                "DEMO — New trade proposals paused. Existing exchange protection remains in place."
            )
        elif text == "/resume":
            self.trading_cfg.paused = False
            write_config(self.trading_cfg)
            self.bot.send("DEMO — New trade proposals resumed.")

    def poll_once(self) -> int:
        self.store.expire_due()
        offset = self.store.get_offset()
        updates = self.bot.updates(offset + 1 if offset else 0)
        for update in updates:
            update_id = int(update["update_id"])
            try:
                if "callback_query" in update:
                    self.handle_callback(update["callback_query"])
                elif "message" in update:
                    self.handle_message(update["message"])
            finally:
                self.store.set_offset(update_id)
        self.reconcile_executed()
        self.restore_manual_leverage()
        return len(updates)

    def propose_scan(self) -> list[str]:
        self._refresh_config()
        if self.trading_cfg.paused:
            return []
        # Retry delivery of the SAME unexpired approval, not a new trade.
        for row in self.store.pending():
            if row["status"] == "PENDING" and not row.get("telegram_message_id"):
                try:
                    self._deliver(row, exposure=self._demo_state(row["payload"]["symbol"])[1],
                                  margin_mode=row["payload"].get("margin_mode", "ISOLATED"))
                except Exception as exc:
                    self.engine.journal.event("TELEGRAM_DELIVERY_RETRY", {"error_type": type(exc).__name__})
        existing = {
            r["payload"]["symbol"]
            for r in self.store.pending()
            if r["status"] in {"PENDING", "APPROVED", "EXECUTING"}
        }
        created: list[str] = []
        for symbol in self.trading_cfg.permitted_symbols:
            if symbol in existing:
                continue
            try:
                row = self.create_and_send(symbol)
                if row and row["status"] == "PENDING" and row.get("telegram_message_id"):
                    created.append(row["proposal_id"])
            except Exception as exc:
                # One symbol's failure must not starve Telegram approvals or
                # the other symbol. Record failure without credential-bearing URLs.
                self.engine.journal.event("TELEGRAM_SCAN_ERROR", {"error_type": type(exc).__name__}, symbol)
        return created
