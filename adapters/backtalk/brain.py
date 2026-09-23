# Codex Jarvis: persistent Codex app-server brain for Backtalk
# Adapted from jaredrhod/backtalk brain contract.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Low-latency Codex-backed voice brain.

This adapter keeps one Codex app-server process alive for the entire voice
session and streams agent-message deltas as they arrive. That removes the
per-turn `codex exec` process startup cost used by the first alpha.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import AsyncIterator

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

from backtalk.config import CFG, DISCIPLINE
from backtalk.vlog import log

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
SESSION_FILE = Path(CFG["signals_dir"]) / ".codex_thread"


class CodexError(RuntimeError):
    pass


class WarmBrain:
    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        configured = model or CFG.get("codex_model") or ""
        if str(configured).startswith("claude-"):
            configured = ""
        self.model = str(configured)
        self.effort = str(CFG.get("codex_effort") or "low")
        self._resume_id = resume_id
        self._codex: AsyncCodex | None = None
        self._thread = None
        self._active_turn = None
        self.session = {
            "turns": 0,
            "out_tokens": 0,
            "in_tokens": 0,
            "cost": 0.0,
        }

    async def _new_thread(self, resume_id: str | None = None):
        assert self._codex is not None
        common = dict(
            cwd=str(Path(CFG["agent_dir"]).expanduser()),
            developer_instructions=DISCIPLINE,
            config={"model_reasoning_effort": self.effort},
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.auto_review,
        )
        if self.model:
            common["model"] = self.model

        if resume_id:
            try:
                self._thread = await self._codex.thread_resume(
                    resume_id, include_turns=False, **common
                )
                log(f"[brain] resumed Codex thread {resume_id[:8]}")
                return
            except Exception as exc:
                log(f"[brain] resume failed ({str(exc)[:100]}), starting fresh")

        try:
            self._thread = await self._codex.thread_start(**common)
        except Exception as exc:
            # A ChatGPT/Codex plan can expose a different model set than the
            # public API catalog. If the requested fast voice model is not
            # enabled for this account, fall back to Codex's own default
            # instead of breaking the voice line.
            if self.model:
                log(
                    f"[brain] model {self.model!r} unavailable "
                    f"({str(exc)[:100]}), falling back to Codex default"
                )
                self.model = ""
                common.pop("model", None)
                self._thread = await self._codex.thread_start(**common)
            else:
                raise
        self._remember_thread()

    def _remember_thread(self):
        if not self._thread:
            return
        tid = getattr(self._thread, "id", None)
        if not tid:
            return
        try:
            SESSION_FILE.write_text(str(tid), encoding="utf-8")
        except OSError:
            pass

    async def start(self):
        codex_bin = shutil.which("codex")
        if not codex_bin:
            raise CodexError("Codex CLI was not found on PATH.")

        resume = self._resume_id
        if not resume and CFG.get("resume_last_session"):
            try:
                resume = SESSION_FILE.read_text(encoding="utf-8").strip() or None
            except OSError:
                resume = None

        cfg = CodexConfig(
            codex_bin=codex_bin,
            cwd=str(Path(CFG["agent_dir"]).expanduser()),
        )
        self._codex = AsyncCodex(config=cfg)
        await self._codex.__aenter__()
        await self._new_thread(resume)

    async def stop(self):
        if self._active_turn is not None:
            try:
                await self._active_turn.interrupt()
            except Exception:
                pass
            self._active_turn = None
        if self._codex is not None:
            await self._codex.close()
            self._codex = None

    async def interrupt(self):
        if self._active_turn is not None:
            try:
                await self._active_turn.interrupt()
            except Exception:
                pass

    async def reset_turn(self, timeout: float = 8.0):
        await self.interrupt()
        self._active_turn = None

    async def set_permission_mode(self, backtalk_mode: str):
        # Codex SDK auto-review/workspace sandbox owns approval behavior in alpha.
        return None

    async def context_usage(self):
        return None

    async def command(self, cmd: str) -> str:
        c = cmd.strip().lower()
        if c in {"/clear", "clear"}:
            self._thread = None
            try:
                SESSION_FILE.unlink()
            except OSError:
                pass
            await self._new_thread(None)
            return "Started a fresh Codex conversation."
        if c.startswith("/compact") and self._thread is not None:
            await self._thread.compact()
            return "Compaction started."
        if c.startswith("/model"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if candidate.startswith("claude-"):
                    return "That is a Claude model, not a Codex model."
                self.model = candidate
                return f"Model set to {candidate} for following turns."
        if c.startswith("/effort"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 2:
                self.effort = parts[1].strip().lower()
                return f"Reasoning effort set to {self.effort}."
        return "That voice-console command is not supported yet."

    def _prompt(self, utterance: str) -> str:
        return utterance.strip()

    def _tally_usage(self, event):
        try:
            turn = getattr(event.payload, "turn", None)
            usage = getattr(turn, "usage", None) if turn else None
            if usage is None:
                return
            total = getattr(usage, "total", usage)
            self.session["in_tokens"] += int(
                getattr(total, "input_tokens", 0) or 0
            )
            self.session["out_tokens"] += int(
                getattr(total, "output_tokens", 0) or 0
            )
        except Exception:
            pass

    async def ask_stream(self, utterance: str) -> AsyncIterator[str]:
        if self._thread is None:
            raise CodexError("Codex thread is not initialized.")

        turn = await self._thread.turn(
            self._prompt(utterance),
            model=self.model or None,
            effort=self.effort,
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.auto_review,
        )
        self._active_turn = turn

        buf = ""
        completed_text = ""
        saw_delta = False
        try:
            async for event in turn.stream():
                method = getattr(event, "method", "")

                if method == "item/agentMessage/delta":
                    delta = getattr(event.payload, "delta", "") or ""
                    if not delta:
                        continue
                    saw_delta = True
                    buf += delta
                    while True:
                        m = _SENTENCE_END.search(buf)
                        if not m:
                            break
                        sentence = buf[:m.end()].strip()
                        buf = buf[m.end():]
                        if sentence:
                            yield sentence

                elif method == "item/completed":
                    try:
                        root = event.payload.item.root
                        if getattr(root, "type", None) == "agentMessage":
                            completed_text = getattr(root, "text", "") or completed_text
                    except Exception:
                        pass

                elif method == "turn/completed":
                    self._tally_usage(event)

            if saw_delta:
                if buf.strip():
                    yield buf.strip()
            elif completed_text.strip():
                yield completed_text.strip()

            self.session["turns"] += 1
            self._remember_thread()
        finally:
            self._active_turn = None
