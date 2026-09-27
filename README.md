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
