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
