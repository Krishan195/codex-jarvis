"""Command-line interface for Jarvis core services."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import shutil
import subprocess
import sys

from . import approvals
from . import browser as browserctl
from . import credentials as credential_store
from . import secrets
from . import showcase
from . import system_access
from .briefing import build as build_briefing, save as save_briefing, notify
from .google_workspace import (
    GoogleNotConnected,
    authorize,
    create_event,
    send_email,
)


def _print_request(req: dict) -> None:
    print(f"Approval required: {req['id']}")
    print(req["summary"])
    print(f"Expires: {req['expires_at']}")
    print(f"Approve: jarvis-core approve {req['id']}")
    print(f"Reject:  jarvis-core reject {req['id']}")


def cmd_status(_args) -> int:
    print(f"Secret service: {'ready' if secrets.available() else 'missing'}")
    try:
        from .google_workspace import credentials
        credentials()
        print("Google Workspace: connected")
    except Exception as exc:
        print(f"Google Workspace: not ready ({exc})")
    print(f"Browser daemon: {'ready' if browserctl.running() else 'stopped'}")
    try:
        print(f"Chrome/Chromium: {browserctl.chrome_binary()}")
    except Exception as exc:
        print(f"Chrome/Chromium: not ready ({exc})")
    print(f"Pending approvals: {len(approvals.pending())}")
    return 0


def cmd_google_auth(args) -> int:
    authorize(Path(args.client_json).expanduser())
    print("Google Workspace connected securely.")
    print("OAuth client configuration and refresh token are in Secret Service, not the repo.")
    _enable_brief_timer()
    return 0


def _enable_brief_timer() -> None:
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "jarvis-briefing.timer"],
            check=False,
        )


def cmd_briefing(args) -> int:
    text = build_briefing()
    save_briefing(text)
    print(text)
    if args.notify:
        notify(text)
    return 0


def cmd_secret_set(args) -> int:
    secrets.set_interactive(args.name)
    print(f"Stored {args.name} in the OS credential vault.")
    return 0


def cmd_secret_delete(args) -> int:
    ok = secrets.delete(args.name)
    print("Deleted." if ok else "Secret was not found.")
    return 0


def cmd_credential_set(args) -> int:
    host = credential_store.set_credential(args.site, args.username)
    print(f"Stored website credential for {host} in Secret Service.")
    print("The password was not written to Memory, config, logs, or Git.")
    return 0


def cmd_credential_list(_args) -> int:
    rows = credential_store.list_credentials()
    if not rows:
        print("No website credentials are indexed.")
        return 0
    for row in rows:
        print(f"{row['site']}  username={row['username']}")
    return 0


def cmd_credential_delete(args) -> int:
    ok = credential_store.delete_credential(args.site)
    print("Deleted." if ok else "Credential metadata was not found.")
    return 0


def cmd_browser_daemon(_args) -> int:
    browserctl.daemon()
    return 0


def cmd_browser_start(_args) -> int:
    browserctl.ensure_started()
    print("Jarvis browser is ready.")
    print(f"Persistent profile: {browserctl.PROFILE_DIR}")
    return 0


def cmd_browser_stop(_args) -> int:
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "stop", "jarvis-browser.service"],
            check=False,
        )
    print("Jarvis browser stop requested.")
    return 0


def cmd_browser_open(args) -> int:
    print(json.dumps(browserctl.open_url(args.url), indent=2, ensure_ascii=False))
    return 0


def cmd_browser_search(args) -> int:
    print(json.dumps(browserctl.search(args.query), indent=2, ensure_ascii=False))
    return 0


def cmd_browser_read(args) -> int:
    data = browserctl.read_page(args.url, max_chars=args.max_chars)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_browser_inspect(args) -> int:
    data = browserctl.inspect_interactive(args.max_items)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_browser_login_window(args) -> int:
    data = browserctl.login_window(args.url)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("Complete login manually in the Jarvis browser. The dedicated profile keeps the session.")
    return 0


def cmd_show(args) -> int:
    showcase.spawn(args.title, args.description, args.image_url, args.source_url)
    print("Visual card opened.")
    return 0


def cmd_request_email(args) -> int:
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
    if not body:
        raise SystemExit("Email body is required.")
    req = approvals.propose(
        "gmail.send",
        f"Send email to {args.to} with subject {args.subject!r}",
        {"to": args.to, "subject": args.subject, "body": body},
    )
    _print_request(req)
    return 0


def cmd_request_event(args) -> int:
    req = approvals.propose(
        "calendar.create",
        f"Create calendar event {args.summary!r} from {args.start} to {args.end}",
        {
            "summary": args.summary,
            "start": args.start,
            "end": args.end,
            "description": args.description or "",
            "location": args.location or "",
        },
    )
    _print_request(req)
    return 0


def cmd_request_browser_login(args) -> int:
    # The password itself is never copied into the approval request.
    req = approvals.propose(
        "browser.login",
        f"Sign in to {args.site} at {args.url} using its stored credential",
        {
            "site": args.site,
            "url": args.url,
            "username_selector": args.username_selector,
            "password_selector": args.password_selector,
            "submit_selector": args.submit_selector or "",
        },
    )
    _print_request(req)
    return 0


def cmd_request_browser_click(args) -> int:
    req = approvals.propose(
        "browser.click",
        f"Click browser element {args.selector!r} on the active page",
        {"selector": args.selector},
    )
    _print_request(req)
    return 0


def cmd_request_system(args) -> int:
    # Show the exact command in the approval summary. No hidden command payload.
    req = approvals.propose(
        "system.exec",
        f"Run Ubuntu command exactly as shown: {args.command}",
        {"command": args.command},
    )
    _print_request(req)
    return 0


def cmd_approve(args) -> int:
    req = approvals.approve(args.request_id)
    print(f"Approved {req['id']}: {req['summary']}")
    print(f"Execute: jarvis-core execute {req['id']}")
    return 0


def cmd_reject(args) -> int:
    req = approvals.reject(args.request_id)
    print(f"Rejected {req['id']}: {req['summary']}")
    return 0


def cmd_pending(_args) -> int:
    rows = approvals.pending()
    if not rows:
        print("No pending approvals.")
        return 0
    for req in rows:
        print(f"{req['id']}  {req['summary']}  expires={req['expires_at']}")
    return 0


def cmd_execute(args) -> int:
    req = approvals.claim(args.request_id)
    try:
        if req["action"] == "gmail.send":
            p = req["payload"]
            result = send_email(p["to"], p["subject"], p["body"])
            detail = f"Gmail message id {result.get('id', '(unknown)')}"

        elif req["action"] == "calendar.create":
            p = req["payload"]
            result = create_event(
                p["summary"], p["start"], p["end"],
                p.get("description", ""), p.get("location", ""),
            )
            detail = f"Calendar event id {result.get('id', '(unknown)')}"

        elif req["action"] == "browser.login":
            p = req["payload"]
            username, password = credential_store.get_credential(p["site"])
            result = browserctl.login_with_credential(
                p["url"],
                username,
                password,
                p["username_selector"],
                p["password_selector"],
                p.get("submit_selector") or None,
            )
            detail = f"Browser login action completed at {result.get('url', '')}"

        elif req["action"] == "browser.click":
            p = req["payload"]
            result = browserctl.click(p["selector"])
            detail = f"Browser click completed at {result.get('url', '')}"

        elif req["action"] == "system.exec":
            p = req["payload"]
            result = system_access.run(p["command"])
            detail = (
                f"Ubuntu command rc={result['returncode']}\n"
                f"stdout:\n{result['stdout']}\n"
                f"stderr:\n{result['stderr']}"
            )
            if result["returncode"] != 0:
                raise RuntimeError(detail)
            print(detail)

        else:
            raise RuntimeError(f"Unsupported action: {req['action']}")
    except Exception as exc:
        approvals.mark_result(req["id"], False, str(exc))
        raise

    approvals.mark_result(req["id"], True, detail)
    if req["action"] != "system.exec":
        print(f"Executed {req['id']}: {detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis-core")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("google-auth")
    s.add_argument("--client-json", required=True)
    s.set_defaults(func=cmd_google_auth)

    s = sub.add_parser("briefing")
    s.add_argument("--notify", action="store_true")
    s.set_defaults(func=cmd_briefing)

    s = sub.add_parser("secret-set")
    s.add_argument("name")
    s.set_defaults(func=cmd_secret_set)

    s = sub.add_parser("secret-delete")
    s.add_argument("name")
    s.set_defaults(func=cmd_secret_delete)

    s = sub.add_parser("credential-set")
    s.add_argument("site")
    s.add_argument("--username", required=True)
    s.set_defaults(func=cmd_credential_set)

    s = sub.add_parser("credential-list")
    s.set_defaults(func=cmd_credential_list)

    s = sub.add_parser("credential-delete")
    s.add_argument("site")
    s.set_defaults(func=cmd_credential_delete)

    s = sub.add_parser("browser-daemon", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_browser_daemon)

    s = sub.add_parser("browser-start")
    s.set_defaults(func=cmd_browser_start)

    s = sub.add_parser("browser-stop")
    s.set_defaults(func=cmd_browser_stop)

    s = sub.add_parser("browser-open")
    s.add_argument("url")
    s.set_defaults(func=cmd_browser_open)

    s = sub.add_parser("browser-search")
    s.add_argument("query")
    s.set_defaults(func=cmd_browser_search)

    s = sub.add_parser("browser-read")
    s.add_argument("url", nargs="?")
    s.add_argument("--max-chars", type=int, default=12000)
    s.set_defaults(func=cmd_browser_read)

    s = sub.add_parser("browser-inspect")
    s.add_argument("--max-items", type=int, default=80)
    s.set_defaults(func=cmd_browser_inspect)

    s = sub.add_parser("browser-login-window")
    s.add_argument("url")
    s.set_defaults(func=cmd_browser_login_window)

    s = sub.add_parser("show")
    s.add_argument("--title", required=True)
    s.add_argument("--description", required=True)
    s.add_argument("--image-url", default="")
    s.add_argument("--source-url", default="")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("request-email")
    s.add_argument("--to", required=True)
    s.add_argument("--subject", required=True)
    body = s.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file")
    s.set_defaults(func=cmd_request_email)

    s = sub.add_parser("request-event")
    s.add_argument("--summary", required=True)
    s.add_argument("--start", required=True, help="ISO-8601 date/time with timezone")
    s.add_argument("--end", required=True, help="ISO-8601 date/time with timezone")
    s.add_argument("--description")
    s.add_argument("--location")
    s.set_defaults(func=cmd_request_event)

    s = sub.add_parser("request-browser-login")
    s.add_argument("--site", required=True)
    s.add_argument("--url", required=True)
    s.add_argument("--username-selector", required=True)
    s.add_argument("--password-selector", required=True)
    s.add_argument("--submit-selector")
    s.set_defaults(func=cmd_request_browser_login)

    s = sub.add_parser("request-browser-click")
    s.add_argument("--selector", required=True)
    s.set_defaults(func=cmd_request_browser_click)

    s = sub.add_parser("request-system")
    s.add_argument("--command", required=True)
    s.set_defaults(func=cmd_request_system)

    s = sub.add_parser("approve")
    s.add_argument("request_id")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("reject")
    s.add_argument("request_id")
    s.set_defaults(func=cmd_reject)

    s = sub.add_parser("pending")
    s.set_defaults(func=cmd_pending)

    s = sub.add_parser("execute")
    s.add_argument("request_id")
    s.set_defaults(func=cmd_execute)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args) or 0)
    except GoogleNotConnected as exc:
        print(f"Jarvis Google connection required: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Jarvis core error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
