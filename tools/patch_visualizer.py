#!/usr/bin/env python3
"""Add/upgrade browser-side Wayland push-to-talk support in ai-visualizer/core.js."""

from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_visualizer.py /path/to/ai-visualizer")

root = Path(sys.argv[1]).resolve()
core = root / "core.js"
text = core.read_text(encoding="utf-8")

marker = "CODEX-JARVIS-WAYLAND-PTT"

if marker in text:
    upgraded = text.replace(
        'if (e.key !== "Home") return;',
        'if (!["v", "V", "F8", "Home"].includes(e.key)) return;'
    )
    if upgraded != text:
        core.write_text(upgraded, encoding="utf-8")
        print("[codex-jarvis] Upgraded visualizer PTT: V + F8 + Home supported.")
    else:
        print("[codex-jarvis] Visualizer Wayland PTT hook already current.")
    raise SystemExit(0)

hook = r"""

/* CODEX-JARVIS-WAYLAND-PTT
 * Wayland does not expose global key events to pynput. When this page has
 * focus, forward V/F8/Home press/release to Backtalk's loopback PTT bridge.
 * V is the recommended key on Linux/Wayland.
 */
(() => {
  const endpoint = "http://127.0.0.1:8792/ptt/";
  let down = false;
  const isPTT = (e) => ["v", "V", "F8", "Home"].includes(e.key);
  const send = (state) => {
    fetch(endpoint + state, { method: "POST", mode: "cors", cache: "no-store" })
      .catch(() => {});
  };
  window.addEventListener("keydown", (e) => {
    if (!isPTT(e)) return;
    e.preventDefault();
    if (!down && !e.repeat) {
      down = true;
      send("down");
    }
  }, { capture: true });
  window.addEventListener("keyup", (e) => {
    if (!isPTT(e)) return;
    e.preventDefault();
    if (down) {
      down = false;
      send("up");
    }
  }, { capture: true });
  const release = () => {
    if (down) {
      down = false;
      send("up");
    }
  };
  window.addEventListener("blur", release);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) release();
  });
})();
"""

core.write_text(text + hook, encoding="utf-8")
print("[codex-jarvis] Added Wayland V/F8/Home push-to-talk bridge to ai-visualizer.")
