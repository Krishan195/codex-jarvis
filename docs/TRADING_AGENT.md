# Jarvis Trading Engine

This is the deterministic trading-systems layer for Jarvis. It is intentionally
separate from the low-latency voice brain.

## Current scope

Implemented now:

- Binance Spot/Futures public market data
- strict completed-candle validation
- BTCUSDT and ETHUSDT defaults
- 15-minute signal timeframe
- 1-hour and 4-hour context
- transparent versioned baseline strategy
- deterministic portfolio risk validation
- restart-persistent PAPER positions and audit journal
- autonomous PAPER cycle/loop
- pause/resume, status, performance and close-managed controls
- duplicate-signal protection
- unit tests for candle integrity, risk limits, restart persistence, duplicate
  prevention, WAIT behavior and protection-check failures

Not implemented in this phase:

- real-money order execution
- Binance account credentials
- TESTNET exchange execution
- historical backtester
- partial-fill/order-timeout reconciliation against an exchange
- exchange-hosted protective orders

The missing exchange-execution items belong to a later integration phase and
must not be inferred from PAPER results.

## Separation of responsibility

The AI may ask the engine to scan, explain a proposal, or operate the paper
loop. It does not calculate its own indicators, position size, or bypass risk.

```text
Binance public market data
        |
        v
data integrity checks
        |
        v
trend-pullback-v1
        |
        v
deterministic risk engine
        |
        v
paper broker / protection manager
        |
        v
SQLite journal + performance
        ^
        |
Jarvis voice explanation
```

## Data integrity

Candle-based logic uses completed Binance klines only.

The collector rejects:

- insufficient candle history
- duplicate/out-of-order candle timestamps
- missing candle intervals
- stale latest completed candles
- crossed/empty order books
- stale recent-trade data
- unknown/non-trading symbols for executable paper proposals

Retrieved web/news content is not part of the baseline signal and must never be
treated as instructions.

## Baseline strategy: trend-pullback-v1

The strategy is deliberately simple. It does not combine indicators into an
invented probability or confidence score.

### Long

Every gate must pass:

1. 1h EMA20 > EMA50
2. 4h EMA20 > EMA50
3. 15m EMA20 > EMA50
4. previous completed 15m close <= previous EMA20
5. latest completed 15m close > latest EMA20
6. RSI(14) is within the configured long range (default 52 to 68)
7. ATR(14) percent is within the configured volatility range
8. signal-candle volume / previous 20-candle median volume meets the configured
   minimum
9. spread is below the configured maximum
10. order-book imbalance is not strongly adverse
11. the signal has not expired

Short is the mathematical inverse with its own configured RSI range.

EMA uses an SMA seed followed by the standard alpha
`2 / (period + 1)`. RSI and ATR use Wilder smoothing.

### Entry and exit geometry

Entry type: simulated MARKET.

For a long, reference entry is the current best ask; for a short, current best
bid.

Long stop:

```text
min(signal candle low - stop_atr_buffer * ATR,
    entry - 0.25 * ATR)
```

Short stop is the symmetric maximum above price.

Default target is 2R from entry. Stop and target are rounded to the symbol tick
size before risk sizing.

The signal expires after the configured signal-expiry window (default 15
minutes).

## Position sizing

Risk budget:

```text
allocated_capital * risk_per_trade_percent / 100
```

Estimated per-unit loss includes:

- distance from entry to stop
- configured taker fee at entry
- configured taker fee at stop
- configured adverse entry/exit slippage
- optionally one absolute funding interval for futures

Quantity is rounded down to Binance quantity step size.

The engine then checks:

- permitted symbol
- configured market type
- maximum leverage
- maximum open positions
- daily realized loss limit
- maximum drawdown
- aggregate notional exposure
- available paper equity/margin
- Binance minimum quantity and notional
- supported MARKET order type
- symbol TRADING status

Any failure produces WAIT plus explicit rejection reasons.

## PAPER execution

PAPER fills are deliberately conservative:

- long entry adds configured adverse slippage
- short entry subtracts configured adverse slippage
- exits also receive adverse slippage
- taker fees are charged at both entry and exit
- if stop and take-profit are both touched inside the same one-minute candle,
  the stop is assumed to have happened first because intrabar sequence is
  unknown

The paper loop always attempts to manage existing positions before looking for
new entries. If a protection check cannot run, new entries are blocked for that
cycle and the failure is audited.

## State and duplicate protection

The SQLite journal is stored at:

```text
~/.local/share/codex-jarvis/trading.sqlite3
```

Signals have a unique key built from symbol, strategy version, signal
timestamp and direction. Restarting the process cannot re-insert the same
signal.

Open PAPER positions are persisted and recovered on restart.

## Setup

Install the command without touching the voice stack:

```bash
cd ~/my-agent/codex-jarvis
git pull
bash tools/install_trading_agent.sh
```

Create a paper configuration with an explicit simulated capital amount:

```bash
jarvis-trader init --capital 10000 --market futures
```

This creates:

```text
~/.config/codex-jarvis/trading.json
```

New entries remain disabled after initialization. Review that file first.

Then:

```bash
jarvis-trader health
jarvis-trader scan
jarvis-trader enable-paper
jarvis-trader paper-cycle
```

For a dedicated autonomous PAPER process:

```bash
jarvis-trader paper-loop --seconds 60
```

It is not installed as a background service automatically.

Operational controls:

```bash
jarvis-trader pause
jarvis-trader resume
jarvis-trader positions
jarvis-trader status
jarvis-trader performance
jarvis-trader cancel-pending
jarvis-trader close-managed
jarvis-trader events --limit 50
```

## Testing

Run:

```bash
python3 -m unittest -v tests.test_trading_paper
```

Testnet integration, backtesting and exchange-order failure modes require
separate validation before those modes can be claimed as supported.

## Limitations

PAPER execution is an approximation. A one-minute OHLC bar cannot reveal the
true intrabar order of stop and target touches, market-impact is not modeled,
funding is estimated rather than accrued from historical settlement events, and
public REST snapshots are not a substitute for an exchange user-data stream.

PAPER or testnet results do not establish future profitability.


## Binance Futures Demo integration

The repository includes an authenticated USD-M Futures Demo client for
validating real exchange API mechanics with virtual funds.

The client is hard-pinned to:

```text
https://demo-fapi.binance.com
```

It has no production/live Futures base URL.

Credentials are read only from Linux Secret Service:

```text
binance-demo-api-key
binance-demo-api-secret
```

Supported Demo commands currently cover authentication health, balances,
positions, open orders, signed order-test validation, explicit virtual MARKET
orders, query-by-client-id, cancel, and a conservative close-position helper.

Market submission uses a durable client order ID. If an order submission
encounters an ambiguous network failure, the client queries the order by that
ID before any retry is considered. If reconciliation itself fails, the command
returns an explicit ambiguous-state error rather than retrying blindly.

Autonomous Demo strategy execution is not enabled yet. Before enabling it, the
next exchange-integration phase must add and test exchange-hosted protective
orders, partial-fill handling, startup reconciliation, hedge-mode behavior,
cancel/fill races, and protection-failure policy.


## Telegram approval gate

The exchange-side Demo workflow is:

```text
analyze -> deterministic proposal -> Telegram message -> explicit button
approval -> revalidate -> Binance Demo order -> query actual status ->
protect actual fill -> notify
```

Production/live Binance order execution is not implemented by this build.

### Security model

The Telegram bot token is stored in Linux Secret Service under:

```text
telegram-trading-bot-token
```

The Binance Demo API key and secret remain under:

```text
binance-demo-api-key
binance-demo-api-secret
```

They are not stored in the repository, Markdown Memory, trading config, or
Telegram messages.

Authorized Telegram user/chat IDs are non-secret identifiers stored in:

```text
~/.config/codex-jarvis/trading-telegram.json
```

The file is written mode 0600 where supported.

Only callback buttons received from the exact configured Telegram user ID in
the exact configured private chat are accepted. Message delivery/read state is
not approval.

Approval records are persisted in the trading SQLite database. They are
single-use and default to a 120-second expiry. A unique signal key prevents the
same symbol/strategy/candle/direction from generating duplicate executable
proposals after restarts.

The Demo exchange layer separately verifies that a proposal is in EXECUTING
state, has a verified Telegram approver, is unexpired, and matches the exact
symbol, side, and quantity before an exposure-increasing order can be submitted.

### Revalidation

After approval and before submission, the service checks again:

- signal still qualifies
- original completed-candle signal timestamp is unchanged
- direction, strategy version, quantity, leverage, stop and target are unchanged
- current executable price is inside the approved tolerance
- Demo balance/account state can be read
- configured margin mode and leverage still match the Demo account
- no existing position exists for the symbol
- no pending standard order exists for the symbol
- current Binance symbol filters/risk checks still pass
- proposal approval has not expired

Any difference invalidates the proposal. The service does not resize upward,
change leverage, chase price, or move stop/target silently.

### Fill and protection behavior

The service first reports that Binance accepted an entry, then separately
queries the order. It does not call an accepted-but-unfilled order a completed
trade.

If an order remains non-terminal during the confirmation window, the service
attempts to cancel it and re-queries the order before deciding the actual
filled quantity.

Protection is then placed for the quantity actually filled:

- reduce-only STOP_MARKET using MARK_PRICE
- reduce-only TAKE_PROFIT_MARKET using MARK_PRICE

If one protection leg cannot be established, any already-created sibling
protection is canceled where possible and the disclosed emergency policy is a
reduce-only market close. The user is notified immediately. If that emergency
close also fails, the proposal is marked as a critical protection/emergency
failure rather than silently continuing.

After the position disappears, the loop cancels known leftover sibling
protection orders where possible, queries Demo user trades since execution,
and reports the available realized P&L and commission data.

### Telegram setup

1. Create a private bot with Telegram BotFather.
2. Store its token locally. Never paste it into chat:

```bash
jarvis-core secret-set telegram-trading-bot-token
```

3. Open the bot in Telegram and send `/start`.
4. Discover only the IDs needed for authorization:

```bash
jarvis-trader telegram-discover
```

The command intentionally prints IDs/chat type only, not message bodies.

5. Save the authorized private user/chat IDs:

```bash
jarvis-trader telegram-config \
  --user-id YOUR_TELEGRAM_USER_ID \
  --chat-id YOUR_PRIVATE_CHAT_ID \
  --expiry 120 \
  --price-tolerance-bps 10
```

6. Verify the complete gate:

```bash
jarvis-trader telegram-health
jarvis-trader telegram-pending
```

For Futures Demo, the Binance account's leverage and margin mode for each
permitted symbol must match the local configuration before a proposal can be
sent. Baseline defaults are 2x and ISOLATED.

7. To test a single qualifying symbol when a setup exists:

```bash
jarvis-trader telegram-propose BTCUSDT
```

8. To run the approval service and periodically look for qualifying Demo
proposals:

```bash
jarvis-trader telegram-loop --scan-seconds 60
```

Telegram commands accepted only from the authorized private user/chat:

```text
/status
/pending
/positions
/pause
/resume
```

`/pause` prevents new proposals but does not cancel existing protective
orders.

### Demo-only boundary

The authenticated execution client is hard-pinned to Binance Futures Demo.
A direct `jarvis-trader demo-market` exposure-increasing order is blocked.
The non-trading `demo-order-test` command remains available for signed API
validation, and reduce-only emergency closing remains available.

This phase intentionally does not contain a production/live Binance order
endpoint or a live-mode switch.
