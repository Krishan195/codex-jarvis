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
