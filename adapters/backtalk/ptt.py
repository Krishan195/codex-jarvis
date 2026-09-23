# Wayland-capable push-to-talk adapter for Backtalk.
# Based on the PTT contract from jaredrhod/backtalk.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Push-to-talk for X11/macOS/Windows plus a Wayland browser bridge.

pynput cannot reliably receive global keyboard events on a normal Wayland
session. On Wayland, the focused ai-visualizer page forwards Home-key
press/release events to a tiny loopback-only HTTP server in this module.

Nothing is exposed off-machine: the bridge binds to 127.0.0.1 only.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pynput import keyboard

PTT_PORT = int(os.environ.get("BACKTALK_PTT_PORT", "8792"))


def resolve_key(name: str):
    name = (name or "home").strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    aliases = {
        "right_alt": "alt_r", "left_alt": "alt_l",
        "right_option": "alt_r", "left_option": "alt_l",
        "right_ctrl": "ctrl_r", "left_ctrl": "ctrl_l",
        "right_cmd": "cmd_r", "left_cmd": "cmd_l",
        "right_shift": "shift_r", "left_shift": "shift_l",
    }
    name = aliases.get(name, name)
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        print(f"[ptt] unknown key {name!r} — falling back to 'home'", flush=True)
        return keyboard.Key.home


class PTTListener:
    RELEASE_GRACE = 0.12

    def __init__(self, key="home"):
        self._key_name = str(key or "home").strip().lower()
        self._held = False
        self._release_t = None
        self._press_evt = threading.Event()
        self._lock = threading.Lock()
        self._wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and os.name == "posix"
        )

        if self._wayland:
            self._start_wayland_bridge()
            print(
                f"[ptt] Wayland mode: hold {self._key_name} while the "
                f"visualizer page is focused",
                flush=True,
            )
        else:
            self._key = resolve_key(key)
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()

    def _start_wayland_bridge(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code=204):
                self.send_response(code)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "content-type")
                self.end_headers()

            def do_OPTIONS(self):
                self._reply()

            def do_POST(self):
                if self.path == "/ptt/down":
                    owner._bridge_down()
                    self._reply()
                elif self.path == "/ptt/up":
                    owner._bridge_up()
                    self._reply()
                else:
                    self._reply(404)

            def log_message(self, fmt, *args):
                return

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", PTT_PORT), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"Wayland PTT bridge could not bind 127.0.0.1:{PTT_PORT}: {exc}"
            ) from exc
        threading.Thread(
            target=self._server.serve_forever,
            name="wayland-ptt",
            daemon=True,
        ).start()

    def _bridge_down(self):
        with self._lock:
            self._release_t = None
            if not self._held:
                self._held = True
                self._press_evt.set()

    def _bridge_up(self):
        with self._lock:
            if self._held:
                self._release_t = time.monotonic()

    def _on_press(self, k):
        if k != self._key:
            return
        self._release_t = None
        if not self._held:
            self._held = True
            self._press_evt.set()

    def _on_release(self, k):
        if k == self._key:
            self._release_t = time.monotonic()

    def _settle(self):
        with self._lock:
            r = self._release_t
            if self._held and r is not None and (
                time.monotonic() - r >= self.RELEASE_GRACE
            ):
                self._held = False
                self._release_t = None

    def wait_press(self):
        while True:
            self._settle()
            if self._press_evt.wait(timeout=self.RELEASE_GRACE):
                self._press_evt.clear()
                return

    def is_held(self) -> bool:
        self._settle()
        return self._held
