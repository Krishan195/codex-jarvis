"""Authenticated Binance USD-M Futures Demo Trading client.

This module is deliberately hard-pinned to Binance's demo endpoint. It has no
production/live base URL and reads only demo credentials from Linux Secret
Service. It is suitable for validating real exchange order mechanics with
virtual funds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from jarvis_core import secrets
from .journal import DB_PATH

DEMO_BASE_URL = "https://demo-fapi.binance.com"
API_KEY_SECRET = "binance-demo-api-key"
API_SECRET_SECRET = "binance-demo-api-secret"
_CLIENT_ID_RE = re.compile(r"^[.A-Za-z0-9_:/-]{1,36}$")


class DemoExchangeError(RuntimeError):
    pass


def _demo_credentials() -> tuple[str, str]:
    api_key = secrets.get(API_KEY_SECRET)
    api_secret = secrets.get(API_SECRET_SECRET)
    if not api_key or not api_secret:
        raise DemoExchangeError(
            "Binance Demo credentials are not configured. Store them locally with "
            "'jarvis-core secret-set binance-demo-api-key' and "
            "'jarvis-core secret-set binance-demo-api-secret'."
        )
    return api_key, api_secret


def _decode_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        data = json.loads(body)
        if isinstance(data, dict):
            code = data.get("code")
            msg = data.get("msg")
            if code is not None or msg:
                return f"Binance Demo API error {code}: {msg}"
        return body[:1000]
    except Exception:
        return str(exc)


def _public_request(path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None}
    )
    url = DEMO_BASE_URL + path + (("?" + query) if query else "")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "codex-jarvis-demo/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DemoExchangeError(_decode_http_error(exc)) from exc
    except Exception as exc:
        raise DemoExchangeError(f"Binance Demo request failed: {exc}") from exc


def server_time_ms() -> int:
    data = _public_request("/fapi/v1/time")
    return int(data["serverTime"])


def _sign(params: dict[str, Any], secret: str) -> str:
    encoded = urllib.parse.urlencode(
        [(str(k), str(v)) for k, v in params.items() if v is not None]
    )
    return hmac.new(
        secret.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 12.0,
) -> Any:
    api_key, api_secret = _demo_credentials()
    body = dict(params or {})
    body.setdefault("recvWindow", 5000)
    body["timestamp"] = server_time_ms()
    body["signature"] = _sign(body, api_secret)
    encoded = urllib.parse.urlencode(
        [(str(k), str(v)) for k, v in body.items() if v is not None]
    )

    method = method.upper()
    headers = {
        "User-Agent": "codex-jarvis-demo/1.0",
        "Accept": "application/json",
        "X-MBX-APIKEY": api_key,
    }

    if method in {"POST", "PUT", "DELETE"}:
        req = urllib.request.Request(
            DEMO_BASE_URL + path,
            data=encoded.encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method=method,
        )
    else:
        req = urllib.request.Request(
            DEMO_BASE_URL + path + "?" + encoded,
            headers=headers,
            method=method,
        )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise DemoExchangeError(_decode_http_error(exc)) from exc
    except Exception as exc:
        raise DemoExchangeError(f"Binance Demo signed request failed: {exc}") from exc


def auth_health() -> dict[str, Any]:
    now_local = int(time.time() * 1000)
    now_server = server_time_ms()
    balances = balance()
    return {
        "environment": "BINANCE_FUTURES_DEMO",
        "base_url": DEMO_BASE_URL,
        "clock_skew_ms": now_local - now_server,
        "authenticated": True,
        "assets_returned": len(balances),
        "live_trading_supported_by_this_module": False,
    }


def balance() -> list[dict[str, Any]]:
    rows = _signed_request("GET", "/fapi/v3/balance")
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "asset": row.get("asset"),
                "balance": row.get("balance"),
                "available_balance": row.get("availableBalance"),
                "cross_wallet_balance": row.get("crossWalletBalance"),
            }
        )
    return out


def symbol_config(symbol: str) -> dict[str, Any]:
    """Read per-symbol Futures account configuration.

    Binance moved leverage/margin configuration out of Position Information V3;
    use /fapi/v1/symbolConfig for these fields.
    """
    rows = _signed_request(
        "GET",
        "/fapi/v1/symbolConfig",
        {"symbol": symbol.upper()},
    )
    if isinstance(rows, dict):
        row = rows
    else:
        row = next(
            (x for x in rows if str(x.get("symbol") or "").upper() == symbol.upper()),
            None,
        )
    if not row:
        raise DemoExchangeError(f"No Demo symbol configuration returned for {symbol}.")
    return {
        "symbol": row.get("symbol"),
        "margin_type": str(row.get("marginType") or row.get("margin_type") or "UNKNOWN").upper(),
        "leverage": float(row.get("leverage") or 0),
        "is_auto_add_margin": row.get("isAutoAddMargin"),
        "max_notional_value": row.get("maxNotionalValue"),
    }


def change_leverage(symbol: str, leverage: int) -> dict[str, Any]:
    if not 1 <= int(leverage) <= 125:
        raise ValueError("leverage must be between 1 and 125")
    return _signed_request(
        "POST",
        "/fapi/v1/leverage",
        {"symbol": symbol.upper(), "leverage": int(leverage)},
    )


def change_margin_type(symbol: str, margin_type: str) -> dict[str, Any]:
    margin_type = margin_type.upper()
    if margin_type not in {"ISOLATED", "CROSSED"}:
        raise ValueError("margin_type must be ISOLATED or CROSSED")
    try:
        return _signed_request(
            "POST",
            "/fapi/v1/marginType",
            {"symbol": symbol.upper(), "marginType": margin_type},
        )
    except DemoExchangeError as exc:
        # Binance returns "No need to change margin type" when already correct.
        if "-4046" in str(exc):
            return {
                "symbol": symbol.upper(),
                "marginType": margin_type,
                "already_set": True,
            }
        raise


def position_mode() -> dict[str, Any]:
    return _signed_request("GET", "/fapi/v1/positionSide/dual")


def open_algo_orders(symbol: str) -> list[dict[str, Any]]:
    return list(_signed_request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol.upper()}))


def cancel_protection(symbol: str, *, client_id: str, algo: bool = True) -> dict[str, Any]:
    if not algo:  # Existing proposals used the older regular-order API.
        return cancel_order(symbol, client_id=client_id)
    return _signed_request("DELETE", "/fapi/v1/algoOrder",
                           {"clientAlgoId": _validate_client_id(client_id)})


def change_approved_leverage(proposal_id: str, symbol: str, leverage: int) -> dict[str, Any]:
    """Changing account leverage is included in this exact manual approval."""
    with sqlite3.connect(DB_PATH) as conn:
        raw = conn.execute("SELECT payload_json FROM telegram_trade_proposals WHERE proposal_id=?",
                           (proposal_id,)).fetchone()
    if not raw:
        raise DemoExchangeError("Unknown manual proposal.")
    p = json.loads(raw[0])
    if p.get("source") != "MANUAL" or int(p["leverage"]) != leverage:
        raise DemoExchangeError("Leverage differs from the approved manual proposal.")
    _validate_execution_approval(proposal_id, symbol=symbol,
        side="BUY" if p["direction"] == "LONG" else "SELL", quantity=p["quantity"])
    if any(abs(float(x["position_amt"])) > 0 for x in positions(symbol)) or open_orders(symbol) or open_algo_orders(symbol):
        raise DemoExchangeError("Cannot change leverage on an occupied Demo symbol.")
    return change_leverage(symbol, leverage)


def positions(symbol: str | None = None) -> list[dict[str, Any]]:
    params = {"symbol": symbol.upper()} if symbol else {}
    rows = _signed_request("GET", "/fapi/v3/positionRisk", params)
    out: list[dict[str, Any]] = []
    for row in rows:
        amount = float(row.get("positionAmt") or 0)
        if symbol or amount != 0:
            out.append(
                {
                    "symbol": row.get("symbol"),
                    "position_side": row.get("positionSide"),
                    "position_amt": amount,
                    "entry_price": float(row.get("entryPrice") or 0),
                    "mark_price": float(row.get("markPrice") or 0),
                    "unrealized_profit": float(row.get("unRealizedProfit") or row.get("unrealizedProfit") or 0),
                    "liquidation_price": float(row.get("liquidationPrice") or 0),
                    "leverage": float(row.get("leverage") or 0),
                    "margin_type": row.get("marginType"),
                }
            )
    return out


def open_orders(symbol: str | None = None) -> list[dict[str, Any]]:
    params = {"symbol": symbol.upper()} if symbol else {}
    rows = _signed_request("GET", "/fapi/v1/openOrders", params)
    return list(rows)


def user_trades(
    symbol: str,
    *,
    start_time_ms: int | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "symbol": symbol.upper(),
        "limit": max(1, min(int(limit), 1000)),
    }
    if start_time_ms is not None:
        params["startTime"] = int(start_time_ms)
    rows = _signed_request("GET", "/fapi/v1/userTrades", params)
    return list(rows)



def _validate_client_id(value: str) -> str:
    if not _CLIENT_ID_RE.fullmatch(value):
        raise ValueError(
            "client order id must be 1-36 characters using letters, digits, "
            "dot, underscore, colon, slash or hyphen"
        )
    return value


def make_client_id(symbol: str, suffix: str | None = None) -> str:
    symbol = "".join(ch for ch in symbol.upper() if ch.isalnum())[:10]
    tail = suffix or format(int(time.time() * 1000), "x")
    value = f"jv-demo-{symbol}-{tail}"[:36]
    return _validate_client_id(value)


def query_order(symbol: str, *, client_id: str) -> dict[str, Any]:
    return _signed_request(
        "GET",
        "/fapi/v1/order",
        {
            "symbol": symbol.upper(),
            "origClientOrderId": _validate_client_id(client_id),
        },
    )


def cancel_order(symbol: str, *, client_id: str) -> dict[str, Any]:
    return _signed_request(
        "DELETE",
        "/fapi/v1/order",
        {
            "symbol": symbol.upper(),
            "origClientOrderId": _validate_client_id(client_id),
        },
    )


def _validate_execution_approval(
    proposal_id: str | None,
    *,
    symbol: str,
    side: str,
    quantity: float,
) -> None:
    """Enforce Telegram approval at the exchange-execution boundary.

    New exposure is allowed only for an unexpired DEMO proposal atomically
    claimed into EXECUTING state by the approval service.
    """
    if not proposal_id:
        raise DemoExchangeError(
            "Exposure-increasing Demo orders require a claimed Telegram proposal."
        )
    if not DB_PATH.exists():
        raise DemoExchangeError("Trading approval database is unavailable.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT environment,status,expires_at_ms,payload_json,
                   approved_by_user_id,approved_at_ms
              FROM telegram_trade_proposals
             WHERE proposal_id=?
            """,
            (proposal_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise DemoExchangeError("Trading approval state is unavailable.") from exc
    finally:
        conn.close()

    if not row:
        raise DemoExchangeError("Unknown Telegram trade proposal.")
    if row["environment"] != "DEMO" or row["status"] != "EXECUTING":
        raise DemoExchangeError(
            f"Proposal is not authorized for execution: {row['status']}."
        )
    if not row["approved_by_user_id"] or not row["approved_at_ms"]:
        raise DemoExchangeError("Proposal has no verified Telegram approval.")
    if int(row["expires_at_ms"]) < server_time_ms():
        raise DemoExchangeError("Telegram trade approval expired.")

    payload = json.loads(row["payload_json"])
    expected_side = "BUY" if payload.get("direction") == "LONG" else "SELL"
    if str(payload.get("symbol") or "").upper() != symbol.upper():
        raise DemoExchangeError("Order symbol differs from approved proposal.")
    if expected_side != side.upper():
        raise DemoExchangeError("Order side differs from approved proposal.")
    expected_qty = float(payload.get("quantity") or 0)
    if abs(expected_qty - float(quantity)) > max(1e-12, expected_qty * 1e-9):
        raise DemoExchangeError("Order quantity differs from approved proposal.")


def market_order(
    symbol: str,
    side: str,
    quantity: float,
    *,
    client_id: str | None = None,
    reduce_only: bool = False,
    test_only: bool = False,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    if not test_only and not reduce_only:
        _validate_execution_approval(
            proposal_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
        )

    cid = _validate_client_id(client_id or make_client_id(symbol))
    params: dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side,
        "type": "MARKET",
        "quantity": format(quantity, ".12g"),
        "newClientOrderId": cid,
        "newOrderRespType": "RESULT",
    }
    if reduce_only:
        params["reduceOnly"] = "true"

    path = "/fapi/v1/order/test" if test_only else "/fapi/v1/order"
    try:
        result = _signed_request("POST", path, params)
        if test_only:
            return {
                "environment": "BINANCE_FUTURES_DEMO",
                "test_only": True,
                "accepted": True,
                "client_order_id": cid,
                "response": result,
            }
        return {
            "environment": "BINANCE_FUTURES_DEMO",
            "test_only": False,
            "client_order_id": cid,
            "response": result,
        }
    except DemoExchangeError as exc:
        if test_only:
            raise

        # A network failure after submission can leave the outcome ambiguous.
        # Reconcile by client ID before allowing any caller to consider retrying.
        message = str(exc)
        if "timed out" in message.lower() or "urlopen error" in message.lower():
            try:
                existing = query_order(symbol, client_id=cid)
                return {
                    "environment": "BINANCE_FUTURES_DEMO",
                    "test_only": False,
                    "client_order_id": cid,
                    "reconciled_after_ambiguous_response": True,
                    "response": existing,
                }
            except Exception as query_exc:
                raise DemoExchangeError(
                    "Order result is ambiguous and reconciliation failed. "
                    f"Do not retry automatically. clientOrderId={cid}. "
                    f"submit_error={message}; query_error={query_exc}"
                ) from exc
        raise


def close_symbol_position(symbol: str) -> list[dict[str, Any]]:
    rows = positions(symbol)
    active = [row for row in rows if abs(float(row["position_amt"])) > 0]
    if not active:
        return []

    results: list[dict[str, Any]] = []
    for row in active:
        if row.get("position_side") not in {None, "", "BOTH"}:
            raise DemoExchangeError(
                "Hedge-mode position detected. Automatic demo close is disabled "
                "until positionSide-specific closing is implemented."
            )
        amount = float(row["position_amt"])
        side = "SELL" if amount > 0 else "BUY"
        results.append(
            market_order(
                row["symbol"],
                side,
                abs(amount),
                client_id=make_client_id(row["symbol"], "close-" + format(int(time.time()), "x")),
                reduce_only=True,
                test_only=False,
            )
        )
    return results



def conditional_market_order(
    symbol: str,
    side: str,
    quantity: float,
    *,
    order_type: str,
    trigger_price: float,
    client_id: str,
) -> dict[str, Any]:
    """Place one reduce-only conditional Demo protection order.

    Binance USD-M advertises STOP_MARKET and TAKE_PROFIT_MARKET for these
    symbols. Actual acceptance is still verified from the exchange response.
    """
    side = side.upper()
    order_type = order_type.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if order_type not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
        raise ValueError("unsupported protective order type")
    if quantity <= 0 or trigger_price <= 0:
        raise ValueError("quantity and trigger_price must be positive")
    cid = _validate_client_id(client_id)
    result = _signed_request(
        "POST",
        "/fapi/v1/algoOrder",
        {
            "algoType": "CONDITIONAL",
            "symbol": symbol.upper(),
            "side": side,
            "type": order_type,
            "quantity": format(quantity, ".12g"),
            "triggerPrice": format(trigger_price, ".12g"),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
            "clientAlgoId": cid,
        },
    )
    if not result.get("algoId") or result.get("algoStatus") != "NEW":
        raise DemoExchangeError("Demo protective order was not confirmed as NEW.")
    return result


def protection_ids(symbol: str, proposal_id: str) -> dict[str, str]:
    return {"stop_client_id": make_client_id(symbol, f"s-{proposal_id[:8]}"),
            "target_client_id": make_client_id(symbol, f"t-{proposal_id[:8]}")}


def place_protection(
    symbol: str,
    *,
    direction: str,
    quantity: float,
    stop_loss: float,
    take_profit: float,
    proposal_id: str,
) -> dict[str, Any]:
    """Place stop and target for the actual filled quantity.

    If the second leg fails, cancel the first leg before returning failure so a
    later emergency close cannot leave a stale reduce-only conditional order.
    """
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    exit_side = "SELL" if direction == "LONG" else "BUY"
    ids = protection_ids(symbol, proposal_id)
    stop_id, target_id = ids["stop_client_id"], ids["target_client_id"]
    stop = conditional_market_order(
        symbol,
        exit_side,
        quantity,
        order_type="STOP_MARKET",
        trigger_price=stop_loss,
        client_id=stop_id,
    )
    try:
        target = conditional_market_order(
            symbol,
            exit_side,
            quantity,
            order_type="TAKE_PROFIT_MARKET",
            trigger_price=take_profit,
            client_id=target_id,
        )
    except Exception:
        try:
            cancel_protection(symbol, client_id=stop_id)
        except Exception:
            pass
        raise
    return {
        "stop": stop,
        "target": target,
        "stop_client_id": stop_id,
        "target_client_id": target_id,
        "quantity": quantity,
        "algo": True,
    }
