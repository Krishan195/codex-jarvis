# Codex Jarvis

A Codex-native adaptation of Jared Rhodenizer's open-source Jarvis stack.

> **Status:** personal Linux MVP. Voice, face, persistent memory, and one-action launch are functional. Barehands is intentionally out of scope.

## What works in the current alpha

- OpenAI Codex CLI is the agent brain.
- It uses the user's existing Codex CLI authentication, including ChatGPT-plan login.
- Backtalk's local faster-whisper speech recognition is retained.
- Piper is the default low-latency local voice backend on CPU-only laptops; Kokoro ONNX/PyTorch remain available as quality/fallback backends.
- The existing Backtalk signal bus drives Jared's AI Visualizer.
- Codex thread IDs are saved and resumed across voice turns.
- No OpenAI API key is required for this core path.\n- A persistent Markdown memory vault is bootstrapped into fresh Codex sessions and lives outside the git repositories so upgrades do not overwrite it.\n- The installer creates both a `jarvis` shell command and an Ubuntu `Jarvis.desktop` launcher.

The current bridge keeps a persistent Codex app-server session alive and streams agent-message deltas into the voice pipeline. Replies are chunked at natural sentence/clause boundaries, then prefetched through a fast local Piper backend so synthesis can overlap playback. Kokoro ONNX/PyTorch remain available as alternate/fallback voices.

## Architecture

```text
microphone
   |
   v
local faster-whisper
   |
   v
Codex voice bridge ----> Codex CLI ----> ChatGPT/Codex account
   |                         |
   |                         +---- tools / files / shell
   |
   +---- local Kokoro TTS
   |
   +---- Jared's signal bus ----> ai-visualizer
                              \--> barehands (next phase)

AGENTS.md + markdown memory <---- persistent identity and memory
```

## Linux alpha install

Prerequisites:

- Git
- Python 3.11 or 3.12
- Codex CLI installed and signed in
- microphone and speakers

Check Codex:

```bash
codex
```

Inside Codex, use `/status` and confirm the account is signed in.

Then:

```bash
git clone https://github.com/Krishan195/codex-jarvis.git
cd codex-jarvis
bash install.sh
bash start.sh
```

The installer places Jared's components beside this repository in the same agent-home directory. It does not overwrite an existing non-git folder.

Default alpha configuration:

- agent name: Jarvis
- push-to-talk key: `V` on Wayland (browser-focused visualizer bridge)
- local voice: `bm_lewis`
- STT model: `small.en`
- face: circuit board
- Codex thread resume: enabled
- Codex actions: safe auto-review/workspace mode rather than disabling sandboxing

## Roadmap

1. **Voice latency/polish** — tune clause streaming, Kokoro prefetch, interruption, and timing telemetry.
2. **Memory** — adapt the AI Memory Vault boot instructions from `CLAUDE.md` to `AGENTS.md`.
3. **Memory polish** — improve retrieval/indexing as the vault grows and add optional migration/import tools.
4. **Permissions** — expose Codex approval events cleanly in voice sessions.
5. **Wayland input** — move from the focused-browser key bridge to an optional desktop-native hotkey path.
6. **Installer/update UX** — reliable upgrade path, self-test, diagnostics, and desktop launchers.
7. **Tests + packaging** — regression tests for voice, memory, config migrations, and upstream compatibility.\n\nBarehands is intentionally not part of this build.

## Upstream projects and credit

This project is an adaptation/integration of work by **Jared Rhodenizer**:

- fullstack-agent — https://github.com/jaredrhod/fullstack-agent
- backtalk — https://github.com/jaredrhod/backtalk
- ai-memory-vault — https://github.com/jaredrhod/ai-memory-vault
- ai-visualizer — https://github.com/jaredrhod/ai-visualizer
- barehands — https://github.com/jaredrhod/barehands

See [ATTRIBUTION.md](ATTRIBUTION.md) for licensing details.

Codex itself is an OpenAI product and is not included in this repository.

## License

The integration and AGPL-derived portions of this repository are released under **AGPL-3.0-or-later**. Individual upstream components retain their own copyright and license notices. The AI Memory Vault material is CC BY-SA 4.0 upstream and must retain that license when copied or adapted.

This is an independent community adaptation and is not an official Jared Rhodenizer or OpenAI project.


## Personal-agent core

The personal-agent layer is installed by `install.sh` and exposed through
`jarvis-core`.

It provides:

- OS Secret Service storage for credentials and OAuth refresh tokens
- Google Workspace OAuth for Gmail + Google Calendar
- a read-only daily briefing
- a one-time approval broker for Gmail send and Calendar create actions
- a local audit trail for external action requests and results
- a user-level systemd timer for the morning briefing

Check it with:

```bash
jarvis-core status
```

### Connect Google Workspace

Create a Google Cloud OAuth Desktop client with Gmail API and Google Calendar
API enabled, download its client JSON, then:

```bash
jarvis-core google-auth --client-json ~/Downloads/client_secret_....json
jarvis-core briefing
```

The client configuration and OAuth refresh token are stored in Linux Secret
Service rather than in this repository or the Memory vault.

The daily briefing timer is installed for **08:00 local time** and is enabled
only after Google OAuth succeeds:

```bash
systemctl --user status jarvis-briefing.timer
```

### Approval example

Jarvis can prepare an email without sending it:

```bash
jarvis-core request-email \
  --to person@example.com \
  --subject "Hello" \
  --body "Draft text"
```

That returns a short approval ID. A consequential write happens only after:

```bash
jarvis-core approve APPROVAL_ID
jarvis-core execute APPROVAL_ID
```

Approvals expire, are one-time, and are recorded in a local audit log.

See [SECURITY.md](SECURITY.md) for the credential model.


## Browser automation

Jarvis now has a dedicated persistent Chrome/Chromium profile. Start it with:

```bash
jarvis-core browser-start
```

Useful read-only commands:

```bash
jarvis-core browser-open https://example.com
jarvis-core browser-search "latest iPhone"
jarvis-core browser-read
jarvis-core browser-inspect
```

For sites that need a one-time manual login:

```bash
jarvis-core browser-login-window https://example.com/login
```

Sign in yourself in the Jarvis browser. The session persists under
`~/my-agent/BrowserProfile` and can be reused later.

For ordinary sites where you explicitly want Jarvis to keep a password:

```bash
jarvis-core credential-set example.com --username USERNAME
```

The password is entered with a hidden prompt and stored in Linux Secret Service,
not in Memory, Git, or config files.

Stored-credential login and consequential browser clicks are approval-gated.

## Ubuntu control

Jarvis can inspect the Ubuntu environment directly and can propose
state-changing user/system commands with:

```bash
jarvis-core request-system --command 'COMMAND'
```

The exact command is shown before one-time approval. The project intentionally
does **not** disable Codex sandboxing, configure passwordless sudo, or store a
sudo password.

## Visual answers

Jarvis can pop up a native desktop card with an image, description, and source:

```bash
jarvis-core show \
  --title "iPhone" \
  --description "Current model details..." \
  --image-url "https://..." \
  --source-url "https://..."
```

This is intended for voice requests such as "show me the latest iPhone": Jarvis
can search/read the current source, extract the page image/description, speak a
short answer, and open the visual card.


## Freelance co-pilot

The `jarvis-freelance` command turns Jarvis into a local freelance
opportunity and delivery co-pilot.

It intentionally does **not** auto-submit proposals, spend Connects, message
clients, accept contracts, or change account settings. Discovery, analysis,
drafting, and local project work are automated; platform submission and
commercial commitments remain human actions.

### Capture opportunities from the logged-in Upwork feed

Open the Upwork Find Work/job feed in the dedicated Jarvis browser, then:

```bash
jarvis-freelance scan-browser
jarvis-freelance list
```

The browser scan is read-only and deduplicates jobs into a local SQLite
pipeline.

### Capture Upwork job-alert email

After Google OAuth is configured:

```bash
jarvis-freelance scan-email --days 7
jarvis-freelance review --max-jobs 5
jarvis-freelance digest
```

The installer creates a daily 08:30 user timer that scans the user's own Upwork
job-alert email, asks Codex to review up to five new opportunities, stores
fit reasoning and truthful proposal drafts, and notifies the user about the
shortlist. It is enabled together with the morning briefing after Google OAuth
succeeds.

Run the same workflow manually with:

```bash
jarvis-freelance daily --days 3 --max-jobs 5 --notify
```

### Analyze and prepare a proposal

```bash
jarvis-freelance show 12
jarvis-freelance analyze 12 --score 86 --reason "Strong Linux/AWS fit; clarify migration window."
jarvis-freelance status-set 12 shortlisted
jarvis-freelance proposal-save 12 --file /tmp/proposal.txt
```

Jarvis can perform these steps from the voice session after reading the actual
job. Proposal drafts must remain truthful: no invented certifications,
experience, portfolio items, or outcomes.

Jarvis uses `Memory/02 - Projects/Freelance Profile.md` as the source of
truth for claims it is allowed to make in proposal drafts. Keep that file
factual; studying/in-progress items should never be presented as completed.

### Start delivery work

When a real project has been accepted manually:

```bash
jarvis-freelance workspace 12
```

This creates a local project folder under `~/my-agent/Freelance/` with the
captured brief, fit reasoning, proposal draft, and a delivery checklist so
Jarvis and the user can build, test, document, and prepare handover together.

Structured pipeline state lives under Jarvis's local application data. A
human-readable dashboard is written to:

```text
Memory/05 - Resources/Jobs/Freelance Opportunities.md
```


## Binance market-analysis specialist

`jarvis-binance` is a lightweight, on-demand Binance market-analysis module.
It does not change the normal Luna voice model or add background workers, so
ordinary Jarvis response latency stays as it was.

It uses current public Binance market data and calculates trend/momentum,
EMA 20/50, RSI, MACD, ATR, Bollinger Bands, VWAP, volume participation, taker
buy flow, spread, order-book imbalance, support/resistance, and 24-hour market
context. USD-M futures analysis also attempts to include mark price, funding,
and open interest.

Install/update only this feature without rerunning the full Jarvis installer:

```bash
cd ~/my-agent/codex-jarvis
git pull
bash tools/install_binance_expert.sh
```

Examples:

```bash
jarvis-binance quote BTCUSDT --market spot
jarvis-binance analyze BTCUSDT --market futures --interval 15m
jarvis-binance multi BTCUSDT --market futures --intervals 15m,1h,4h
```

A local simulation ledger is also available:

```bash
jarvis-binance paper-open BTCUSDT --market futures --side long --quantity 0.001 --leverage 5
jarvis-binance positions
jarvis-binance paper-close 1
jarvis-binance stats
```

The built-in module contains no live-account order execution and stores no
Binance API secret. Paper history is kept locally under Jarvis application
data.


## Deterministic Binance PAPER trading engine

For structured trading-system work, use `jarvis-trader`. It is separate from
the voice loop, so the normal Luna/low Jarvis path stays fast.

Current phase implements:

- Binance public market-data validation
- BTCUSDT / ETHUSDT defaults
- completed 15m signals with 1h / 4h context
- versioned `trend-pullback-v1` rules
- deterministic cost-aware position sizing and portfolio limits
- persistent SQLite signals, positions and audit events
- duplicate-signal protection and restart recovery
- autonomous PAPER cycles / loop
- pause, resume, status, performance and managed-close commands

Install only this module:

```bash
cd ~/my-agent/codex-jarvis
git pull
bash tools/install_trading_agent.sh
```

Initialize an explicit paper account:

```bash
jarvis-trader init --capital 10000 --market futures
jarvis-trader health
jarvis-trader scan
```

Initialization does **not** enable new entries. Review
`~/.config/codex-jarvis/trading.json`, then explicitly enable PAPER entries:

```bash
jarvis-trader enable-paper
jarvis-trader paper-cycle
```

A dedicated paper process can run independently:

```bash
jarvis-trader paper-loop --seconds 60
```

Nothing starts that loop automatically.

See [docs/TRADING_AGENT.md](docs/TRADING_AGENT.md) for exact strategy math,
risk rules, paper-fill assumptions, tests and current limitations.


### Binance Futures Demo Trading

For exchange-side testing with virtual funds, `jarvis-trader` also includes
an authenticated Binance USD-M Futures Demo client. The module is hard-pinned
to the Demo host and contains no production Futures trading base URL.

Create Demo Trading API credentials in Binance, then store them locally using
hidden prompts:

```bash
jarvis-core secret-set binance-demo-api-key
jarvis-core secret-set binance-demo-api-secret
```

Do not paste those values into chat, Memory, config files, or Git.

Verify authentication:

```bash
jarvis-trader demo-health
jarvis-trader demo-balance
jarvis-trader demo-positions
```

Validate the signed order path without creating a Demo order:

```bash
jarvis-trader demo-order-test BTCUSDT BUY 0.001
```

The Demo client also supports explicit virtual market orders, order lookup,
cancel, and managed close commands. These operate only against Binance Demo
Trading. Autonomous Demo strategy execution is intentionally not enabled until
exchange-side protective-order handling and reconciliation are completed and
tested.

The production/live Binance Futures trading endpoint is not implemented in this
repository.


## Manual voice requests and risk review

Jarvis can discuss a user-directed **Demo** trade while its independent
Telegram scanner continues running. It explains the requested margin versus
total exposure, estimated loss at the stop, net target, leverage scenarios,
and whether the baseline strategy agrees. It can recommend waiting or lower
exposure, explain why, and respect the user's decision within configured limits.

Install the commands and refreshed voice instructions:

```bash
git pull --ff-only
bash tools/install_manual_trading.sh
```

Restart the voice session and the existing Telegram loop to load the update.
The installer backs up a replaced voice brain, preserves voice configuration,
and does not start a service or change trading limits. The laptop must be awake
and online for the loop to run.

Manual submissions are disabled by default. For an explicitly chosen maximum
of **10 USDT margin per manual trade and 10x leverage**, enable them with:

```bash
jarvis-trader manual-config --enable --max-margin 10 --max-leverage 10
jarvis-trader manual-review BTCUSDT LONG --margin 10 --leverage 10
```

This produces a **review**, not a Telegram message or an order. A voice example:

> Jarvis, review a Demo BTC long using 10 USDT margin at 10x. Explain the risk
> and what you would recommend before sending it for approval.

Ambiguous amounts must be clarified. `--margin 10` at 10x requests up to 100
USDT exposure; `--notional 10` requests up to 10 USDT exposure. Exchange step
sizes can round this down; minimum-notional rules can block the request. The
engine never increases the amount to meet an exchange minimum. Fees are extra.

After discussing the review, an explicit "send it for approval" maps to:

```bash
jarvis-trader manual-propose REVIEW_ID
```

Replace `REVIEW_ID` with the returned ID. The review expires after five minutes;
the Telegram approval normally expires after 120 seconds (or earlier when the
review expires). Only the authorized Telegram button can approve execution.
The existing `jarvis-trader telegram-loop --scan-seconds 60` must be running
to receive it. The CLI prevents two updated pollers from running concurrently.

Manual requests can waive strategy entry filters, but not account, exposure,
daily loss, drawdown, liquidity or exchange limits. Manual limits are separate
from the scanner's strategy leverage. No setting is raised just to make a trade
pass. Use `manual-config --disable` to stop new manual proposals; protection of
already-filled positions remains in place.

See [manual trade details](docs/TRADING_AGENT.md#manual-demo-trade-review) for
stop/target suggestions, temporary leverage changes, and limitations.

## Telegram-gated Demo trading

New Binance Futures Demo exposure can now be gated by a private Telegram
approval. The flow is:

```text
qualifying deterministic setup
        |
        v
Telegram proposal with Approve / Reject
        |
        v
authorized private user + chat only
        |
        v
revalidate exact trade
        |
        v
Binance Futures Demo order
        |
        v
query actual fill -> protective stop/target -> notifications
```

Approval defaults to 120 seconds and is single-use. Duplicate callbacks,
expired proposals, a changed signal, quantity/leverage/stop/target changes,
price movement beyond the configured tolerance, pending orders, an existing
position, or failed risk/data checks prevent submission.

Store the Telegram bot token locally:

```bash
jarvis-core secret-set telegram-trading-bot-token
```

Then send `/start` to the bot and discover the private IDs:

```bash
jarvis-trader telegram-discover
```

Configure authorization:

```bash
jarvis-trader telegram-config \
  --user-id USER_ID \
  --chat-id CHAT_ID \
  --expiry 120 \
  --price-tolerance-bps 10
```

Check configuration and Demo account settings:

```bash
jarvis-trader telegram-health
```

Run the approval service:

```bash
jarvis-trader telegram-loop --scan-seconds 60
```

For a loop that survives closing the terminal and restarts after failures,
install the optional user service, then explicitly start it:

```bash
python3 tools/install_trading_service.py
systemctl --user enable --now jarvis-trading.service
```

Use one loop only. If a foreground loop is already running, stop it before
starting the service. The laptop must remain awake and online; the user service
starts with your user session and needs the unlocked Secret Service keyring.
The installer does not start trading, change limits, or change the voice model.
Starting the service enables automatic qualifying proposals; every new Demo
order still requires your private Telegram Approve button.

If proposals do not arrive:

```bash
jarvis-trader telegram-health
jarvis-trader telegram-scan
systemctl --user status jarvis-trading.service
journalctl --user -u jarvis-trading.service -n 40 --no-pager
```

`telegram-health` checks the actual poller lock, bot/private-chat access,
webhook conflicts, Demo account settings and latest scan reasons.
`telegram-scan` evaluates current Demo conditions without sending a proposal or
placing an order. A `WAIT` result is not a delivery failure: its failed strategy
conditions must clear before an automatic proposal is eligible. The PAPER
`enabled` setting is separate from this Demo approval workflow.

Automatic proposals now use Demo prices and exchange filters, matching their
execution environment. `/status` in your authorized Telegram chat includes
the latest automatic scan reasons. Use `systemctl --user stop jarvis-trading`
to stop the service; disabling it also removes automatic startup.

The bot accepts `/status`, `/pending`, `/positions`, `/pause`, and
`/resume` only from the configured user in the configured private chat.

Approval cards identify direction with 🟢 LONG or 🔴 SHORT and group entry,
leverage, take-profit, stop-loss, estimated net risk/reward, margin, and position
size. They retain expiry, price tolerance, risk warnings and the exact approval
scope. The current engine has one full-position target (TP1); it does not invent
TP2/TP3 or a confidence percentage. A strategy confirmation count is a rules
score, not a measured win probability. Approve/Reject buttons retain the same
single-use authorization checks.

The authenticated order client remains hard-pinned to Binance Futures Demo.
Production/live order execution is not implemented in this repository.
