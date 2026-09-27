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
STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
CURRENT_PAGE_FILE = STATE_DIR / "browser-current.json"


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
    try:
        os.chmod(PROFILE_DIR, 0o700)
    except OSError:
        pass
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


def _remember_page(page) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"url": page.url}
    CURRENT_PAGE_FILE.write_text(
        json.dumps(data, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(CURRENT_PAGE_FILE, 0o600)
    except OSError:
        pass


def _page(browser, new: bool = False):
    if not browser.contexts:
        raise BrowserError("Chrome has no browser context.")
    ctx = browser.contexts[0]
    if new or not ctx.pages:
        return ctx.new_page()

    # connect_over_cdp does not guarantee that ctx.pages[-1] is the tab
    # Jarvis most recently brought to the front. Prefer the URL we explicitly
    # remembered, then fall back to the newest non-blank tab.
    try:
        state = json.loads(CURRENT_PAGE_FILE.read_text(encoding="utf-8"))
        wanted = state.get("url", "")
        if wanted:
            for page in reversed(ctx.pages):
                if page.url == wanted:
                    return page
    except Exception:
        pass

    for page in reversed(ctx.pages):
        if page.url and page.url != "about:blank":
            return page
    return ctx.pages[-1]


def open_url(url: str, *, new_tab: bool = True) -> dict[str, str]:
    if "://" not in url and not url.startswith("about:"):
        url = "https://" + url
    pw, browser = _connect()
    try:
        page = _page(browser, new=new_tab)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        _remember_page(page)
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def search(query: str) -> dict[str, str]:
    return open_url(
        "https://www.google.com/search?q=" + quote_plus(query),
        new_tab=True,
    )


def _meta_content(page, selector: str) -> str:
    loc = page.locator(selector)
    if loc.count() < 1:
        return ""
    try:
        return loc.first.get_attribute("content", timeout=1000) or ""
    except Exception:
        return ""


def _image_candidates(page, max_items: int = 16) -> list[dict[str, Any]]:
    """Return useful visible image candidates without dumping the whole DOM."""
    rows: list[dict[str, Any]] = []
    images = page.locator("img")
    count = min(images.count(), 120)
    for i in range(count):
        img = images.nth(i)
        try:
            data = img.evaluate(
                """e => {
                    const r = e.getBoundingClientRect();
                    const style = getComputedStyle(e);
                    return {
                        src: e.currentSrc || e.src || "",
                        alt: e.alt || "",
                        width: Math.round(r.width || e.naturalWidth || 0),
                        height: Math.round(r.height || e.naturalHeight || 0),
                        visible: style.display !== "none" &&
                                 style.visibility !== "hidden" &&
                                 r.width > 80 && r.height > 60
                    };
                }"""
            )
        except Exception:
            continue
        src = (data.get("src") or "").strip()
        if not src or not data.get("visible"):
            continue
        if src.startswith("data:"):
            continue
        rows.append({
            "src": src,
            "alt": (data.get("alt") or "").strip()[:180],
            "width": int(data.get("width") or 0),
            "height": int(data.get("height") or 0),
        })

    # Prefer large images, but keep DOM order as a secondary signal.
    rows.sort(key=lambda r: r["width"] * r["height"], reverse=True)
    dedup: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row["src"] in seen:
            continue
        seen.add(row["src"])
        dedup.append(row)
        if len(dedup) >= max_items:
            break
    return dedup


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
        description = _meta_content(page, 'meta[name="description"]')
        if not description:
            description = _meta_content(page, 'meta[property="og:description"]')
        image = _meta_content(page, 'meta[property="og:image"]')
        text = page.locator("body").inner_text(timeout=10000)
        _remember_page(page)
        return {
            "title": title,
            "url": page.url,
            "description": description or "",
            "image": image or "",
            "images": _image_candidates(page),
            "text": text[:max_chars],
        }
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()


def inspect_interactive(max_items: int = 80) -> list[dict[str, str]]:
    """Return a compact map of clickable/form controls on the active page."""
    pw, browser = _connect()
    try:
        page = _page(browser)
        items = page.locator(
            "a, button, input, textarea, select, [role=button], [role=link]"
        )
        count = min(items.count(), max_items)
        rows: list[dict[str, str]] = []
        for i in range(count):
            el = items.nth(i)
            try:
                rows.append({
                    "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                    "text": (el.inner_text(timeout=500) or "").strip()[:160],
                    "id": el.get_attribute("id") or "",
                    "name": el.get_attribute("name") or "",
                    "type": el.get_attribute("type") or "",
                    "placeholder": el.get_attribute("placeholder") or "",
                    "aria_label": el.get_attribute("aria-label") or "",
                    "href": el.get_attribute("href") or "",
                })
            except Exception:
                continue
        _remember_page(page)
        return rows
    finally:
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
        _remember_page(page)
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
        _remember_page(page)
        return {"title": page.title(), "url": page.url}
    finally:
        # Disconnect Playwright without terminating the persistent Chrome daemon.
        pw.stop()
