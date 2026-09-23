#!/usr/bin/env python3
"""Add a browser-side Wayland push-to-talk bridge to ai-visualizer/core.js."""

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
    print("[codex-jarvis] Visualizer Wayland PTT hook already applied.")
    raise SystemExit(0)

hook = r"""

/* CODEX-JARVIS-WAYLAND-PTT
 * Wayland does not expose global key events to pynput. When this page has
 * focus, forward Home-key press/release to Backtalk's loopback PTT bridge.
 */
(() => {
  const endpoint = "http://127.0.0.1:8792/ptt/";
  let down = false;
  const send = (state) => {
    fetch(endpoint + state, { method: "POST", mode: "cors", cache: "no-store" })
      .catch(() => {});
  };
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Home") return;
    e.preventDefault();
    if (!down && !e.repeat) {
      down = true;
      send("down");
    }
  }, { capture: true });
  window.addEventListener("keyup", (e) => {
    if (e.key !== "Home") return;
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
print("[codex-jarvis] Added Wayland Home-key bridge to ai-visualizer.")
