# Codex Jarvis

A Codex-native adaptation of Jared Rhodenizer's open-source Jarvis stack.

> **Status:** early alpha. Linux-first while the Codex voice bridge is being ported and tested.

## What works in the current alpha

- OpenAI Codex CLI is the agent brain.
- It uses the user's existing Codex CLI authentication, including ChatGPT-plan login.
- Backtalk's local faster-whisper speech recognition is retained.
- Backtalk's local Kokoro speech output is retained.
- The existing Backtalk signal bus drives Jared's AI Visualizer.
- Codex thread IDs are saved and resumed across voice turns.
- No OpenAI API key is required for this core path.

The first bridge uses `codex exec --json`. Because that interface emits the completed agent message rather than token-by-token assistant text, first-audio latency is not yet as low as Jared's Claude Agent SDK implementation. The next transport milestone is Codex app-server streaming.

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
- push-to-talk key: Home
- local voice: `bm_lewis`
- STT model: `small.en`
- face: circuit board
- Codex thread resume: enabled
- Codex actions: safe auto-review/workspace mode rather than disabling sandboxing

## Roadmap

1. **Codex brain** — current alpha using `codex exec --json`.
2. **Streaming transport** — Codex app-server for lower first-audio latency and native approval/tool events.
3. **Memory** — adapt the AI Memory Vault boot instructions from `CLAUDE.md` to `AGENTS.md`.
4. **Hands** — wire Barehands into the same signal/state path.
5. **Realtime option** — optional lower-cost realtime voice provider, while keeping local voice as the default.
6. **Installer UX** — one-question-at-a-time setup similar to Jared's original fullstack installer.

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
