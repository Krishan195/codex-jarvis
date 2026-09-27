# Codex Jarvis

A Codex-native adaptation of Jared Rhodenizer's open-source Jarvis stack.

> **Status:** working Linux alpha. Voice + face are functional; memory, hands, packaging, and polish are still being completed.

## What works in the current alpha

- OpenAI Codex CLI is the agent brain.
- It uses the user's existing Codex CLI authentication, including ChatGPT-plan login.
- Backtalk's local faster-whisper speech recognition is retained.
- Local Kokoro speech output is retained, with a `kokoro-onnx` FP16 CPU path preferred for lower latency on older Intel CPUs and the original PyTorch backend kept as fallback.
- The existing Backtalk signal bus drives Jared's AI Visualizer.
- Codex thread IDs are saved and resumed across voice turns.
- No OpenAI API key is required for this core path.

The current bridge keeps a persistent Codex app-server session alive and streams agent-message deltas into the voice pipeline. Replies are chunked at natural sentence/clause boundaries, then prefetched through a Kokoro ONNX FP16 CPU backend so synthesis can overlap playback. The original PyTorch Kokoro path remains available as a fallback.

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
3. **Hands** — wire Barehands into the same signal/state path.
4. **Permissions** — expose Codex approval events cleanly in voice sessions.
5. **Wayland input** — move from the focused-browser key bridge to an optional desktop-native hotkey path.
6. **Installer/update UX** — reliable upgrade path, self-test, diagnostics, and desktop launchers.
7. **Tests + packaging** — regression tests for voice, config migrations, and upstream compatibility.

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
