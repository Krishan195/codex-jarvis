# Codex Jarvis

A Codex-native adaptation of Jared Rhodenizer's open-source Jarvis stack.

> **Status:** early alpha. Linux-first while the Codex voice bridge is being ported and tested.

## Goal

Keep the experience of the original stack — persistent memory, local speech recognition and speech, the live visualizer, optional gesture controls, and an agent that can use tools — while replacing the Claude-specific brain with **OpenAI Codex**.

The default path is designed to work with a normal Codex CLI login, including a ChatGPT plan login. No OpenAI API key is required for the core Codex brain.

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
                              \--> barehands (later/optional)

AGENTS.md + markdown memory <---- persistent identity and memory
```

## Phase 1

The first milestone ports the existing local Backtalk audio path to Codex:

- reuse Backtalk's local microphone, Whisper, Kokoro, and signal-bus pieces
- replace `ClaudeAgentSDK` with a Codex CLI bridge
- keep a Codex thread alive across voice turns with `codex exec resume`
- preserve the visualizer signal contract
- keep Codex permissions/sandboxing in charge of real computer actions

The first bridge uses `codex exec --json`. A later phase can move to Codex app-server for lower latency and richer live events.

## Prerequisites

Linux alpha:

- Git
- Python 3.11 or 3.12
- Codex CLI already installed and signed in
- microphone and speakers

Check Codex first:

```bash
codex
```

Then use `/status` and confirm your account is signed in.

## Install

The installer is being added in this alpha. Once Phase 1 lands:

```bash
git clone https://github.com/Krishan195/codex-jarvis.git
cd codex-jarvis
./install.sh
./start.sh
```

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
