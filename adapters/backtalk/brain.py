# Codex Jarvis: Codex-backed replacement for backtalk.brain
# Adaptation started 2026-09.
#
# This file is intended to replace the Claude-Agent-SDK brain used by
# jaredrhod/backtalk while preserving the WarmBrain interface expected by
# Backtalk's voice loop.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Codex-backed voice brain.

Alpha transport: one `codex exec --json` process per turn, resuming the same
Codex thread after the first turn. This keeps conversation state and uses the
user's existing Codex CLI authentication (including ChatGPT-plan login).

The transport intentionally keeps Codex sandboxed. The first alpha does not
attempt to emulate Claude SDK's live spoken permission callback. A later
app-server transport will provide lower latency and richer approval events.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import AsyncIterator

from backtalk.config import CFG, DISCIPLINE
from backtalk.vlog import log

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
SESSION_FILE = Path(CFG["signals_dir"]) / ".codex_thread"


class CodexError(RuntimeError):
    pass


class WarmBrain:
    """Compatibility layer for Backtalk's existing voice loop."""

    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        configured_model = model or CFG.get("model") or ""
        # Upstream Backtalk defaults to Claude model ids. When this adapter is
        # installed into an unchanged Backtalk config, never pass those ids to
        # Codex; let the user's Codex CLI use its own configured/default model.
        self.model = "" if str(configured_model).startswith("claude-") else str(configured_model)
        self._can_use_tool = can_use_tool  # reserved for app-server transport
        self.session = {
            "turns": 0,
            "out_tokens": 0,
            "in_tokens": 0,
            "cost": 0.0,
        }
        self._thread_id = resume_id
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self):
        if not shutil.which("codex"):
            raise CodexError(
                "Codex CLI was not found on PATH. Install Codex and sign in first."
            )
        if not self._thread_id and CFG.get("resume_last_session"):
            try:
                saved = SESSION_FILE.read_text(encoding="utf-8").strip()
                if saved:
                    self._thread_id = saved
                    log(f"[brain] resuming Codex thread {saved[:8]}")
            except OSError:
                pass

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 2)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def interrupt(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    async def reset_turn(self, timeout: float = 8.0):
        await self.interrupt()

    async def set_permission_mode(self, backtalk_mode: str):
        # Alpha deliberately retains Codex's safe workspace/auto-review policy.
        # app-server transport will expose native approval events later.
        return None

    async def context_usage(self):
        return None

    async def command(self, cmd: str) -> str:
        """Minimal compatibility for Backtalk voice-console commands."""
        c = cmd.strip().lower()
        if c in {"/clear", "clear"}:
            self._thread_id = None
            try:
                SESSION_FILE.unlink()
            except OSError:
                pass
            return "Started a fresh Codex conversation."
        if c.startswith("/model"):
            return "Model switching from the voice console is not wired yet."
        if c.startswith("/effort"):
            return "Reasoning-effort switching from the voice console is not wired yet."
        if c.startswith("/compact"):
            return "Codex manages conversation context automatically in this alpha."
        return "That voice-console command is not supported yet."

    def _base_args(self) -> list[str]:
        args = [
            "codex", "exec",
            "--json",
            "--cd", str(Path(CFG["agent_dir"]).expanduser()),
            "--approve-for-me",
        ]
        if self.model:
            args += ["--model", self.model]
        for extra in CFG.get("extra_dirs", []) or []:
            args += ["--add-dir", str(Path(extra).expanduser())]
        return args

    def _prompt(self, utterance: str) -> str:
        return (
            DISCIPLINE
            + "\n\nThe user's spoken message is:\n"
            + utterance.strip()
        )

    async def _run_turn(self, utterance: str) -> str:
        args = self._base_args()
        prompt = self._prompt(utterance)
        if self._thread_id:
            args += ["resume", self._thread_id, prompt]
        else:
            args += [prompt]

        log("[brain] starting Codex turn")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(CFG["agent_dir"]).expanduser()),
        )

        answer = ""
        thread_id = self._thread_id
        turn_usage = None

        assert self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            et = event.get("type")
            if et == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            elif et == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    answer = item.get("text") or answer
            elif et == "turn.completed":
                turn_usage = event.get("usage") or {}
            elif et in {"turn.failed", "error"}:
                err = event.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else None
                msg = msg or event.get("message") or "Codex turn failed"
                raise CodexError(msg)

        rc = await self._proc.wait()
        stderr = ""
        if self._proc.stderr is not None:
            stderr = (await self._proc.stderr.read()).decode(
                "utf-8", errors="replace"
            ).strip()
        self._proc = None

        if rc != 0:
            raise CodexError(stderr or f"codex exited with status {rc}")
        if not answer.strip():
            raise CodexError(stderr or "Codex returned no assistant message")

        if thread_id:
            self._thread_id = thread_id
            if CFG.get("resume_last_session"):
                try:
                    SESSION_FILE.write_text(thread_id, encoding="utf-8")
                except OSError:
                    pass

        self.session["turns"] += 1
        if turn_usage:
            self.session["in_tokens"] += int(turn_usage.get("input_tokens") or 0)
            self.session["out_tokens"] += int(turn_usage.get("output_tokens") or 0)

        return answer.strip()

    async def ask_stream(self, utterance: str) -> AsyncIterator[str]:
        """Yield sentence-sized chunks to Backtalk's existing TTS queue.

        Codex exec JSON currently exposes the final agent message as an item,
        so this alpha begins TTS after the Codex turn completes. The next
        transport milestone is app-server streaming for lower first-audio
        latency.
        """
        text = await self._run_turn(utterance)
        buf = text
        while True:
            m = _SENTENCE_END.search(buf)
            if not m:
                break
            sentence = buf[:m.end()].strip()
            buf = buf[m.end():]
            if sentence:
                yield sentence
        if buf.strip():
            yield buf.strip()
