"""Persistent Chrome automation for Jarvis.

A dedicated Chrome profile is launched with loopback-only remote debugging.
The user signs into Google/websites in that profile once; Jarvis reuses those
browser sessions instead of storing Google passwords.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus
import json
import os
import shutil
import socket
import subprocess
import time
from typing import Any

CDP_PORT = 9223
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
AGENT_HOME = Path.home() / "my-agent"
PROFILE_DIR = AGENT_HOME / "BrowserProfile"


class BrowserError(RuntimeError):
    pass


def chrome_binary() -> str:
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    raise BrowserError(
        "Google Chrome/Chromium was not found. Install Chrome or Chromium first."
    )


def running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", CDP_PORT), timeout=0.35):
            return True
    except OSError:
        return False


def daemon() -> None:
    """Exec a dedicated, persistent, user-visible Chrome process."""
    binary = chrome_binary()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "about:blank",
    ]
    os.execv(binary, args)


def ensure_started(timeout: float = 8.0) -> None:
    if running():
        return
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "start", "jarvis-browser.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            [str(Path.home() / ".local" / "bin" / "jarvis-core"),
             "browser-daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running():
            return
        time.sleep(0.2)
    raise BrowserError("Jarvis browser did not become ready.")


def _connect():
    from playwright.sync_api import sync_playwright
    ensure_started()
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
    except Exception:
        pw.stop()
        raise
    return pw, browser


def _page(browser, new: bool = False):
    if not browser.contexts:
        raise BrowserError("Chrome has no browser context.")
    ctx = browser.contexts[0]
    if new or not ctx.pages:
        return ctx.new_page()
    return ctx.pages[-1]


def open_url(url: str, *, new_tab: bool = True) -> dict[str, str]:
    if "://" not in url and not url.startswith("about:"):
        url = "https://" + url
    pw, browser = _connect()
    try:
        page = _page(browser, new=new_tab)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def search(query: str) -> dict[str, str]:
    return open_url(
        "https://www.google.com/search?q=" + quote_plus(query),
        new_tab=True,
    )


def read_page(url: str | None = None, max_chars: int = 12000) -> dict[str, Any]:
    pw, browser = _connect()
    try:
        page = _page(browser, new=bool(url))
        if url:
            if "://" not in url:
                url = "https://" + url
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        title = page.title()
        description = page.locator('meta[name="description"]').get_attribute("content")
        if not description:
            description = page.locator('meta[property="og:description"]').get_attribute("content")
        image = page.locator('meta[property="og:image"]').get_attribute("content")
        text = page.locator("body").inner_text(timeout=10000)
        return {
            "title": title,
            "url": page.url,
            "description": description or "",
            "image": image or "",
            "text": text[:max_chars],
        }
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def login_window(url: str) -> dict[str, str]:
    """Open a site in the persistent profile for a one-time manual login."""
    return open_url(url, new_tab=True)


def _locator(page, selector: str):
    # Selector is intentionally explicit. This keeps credential autofill from
    # guessing against the wrong page fields.
    return page.locator(selector).first


def login_with_credential(
    url: str,
    username: str,
    password: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str | None = None,
) -> dict[str, str]:
    pw, browser = _connect()
    try:
        page = _page(browser, new=True)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _locator(page, username_selector).fill(username)
        _locator(page, password_selector).fill(password)
        if submit_selector:
            _locator(page, submit_selector).click()
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.bring_to_front()
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def click(selector: str) -> dict[str, str]:
    pw, browser = _connect()
    try:
        page = _page(browser)
        _locator(page, selector).click()
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.bring_to_front()
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def fill(selector: str, value: str) -> dict[str, str]:
    pw, browser = _connect()
    try:
        page = _page(browser)
        _locator(page, selector).fill(value)
        page.bring_to_front()
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()
