"""Google Workspace integration for Jarvis.

OAuth tokens and the OAuth client configuration live only in the OS Secret
Service. Read operations can run unattended. Send/create operations are called
only after the approval broker consumes a one-time approval.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
import base64
import json
from typing import Any

from . import secrets

CLIENT_SECRET_KEY = "google-oauth-client"
TOKEN_SECRET_KEY = "google-oauth-token"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


class GoogleNotConnected(RuntimeError):
    pass


def authorize(client_json: Path) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    raw = client_json.read_text(encoding="utf-8")
    config = json.loads(raw)
    secrets.set(CLIENT_SECRET_KEY, raw)

    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        authorization_prompt_message=(
            "Open this URL in your browser to authorize Jarvis:\n{url}"
        ),
        success_message="Jarvis Google authorization completed. You can close this tab.",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    secrets.set(TOKEN_SECRET_KEY, creds.to_json())


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    raw = secrets.get(TOKEN_SECRET_KEY)
    if not raw:
        raise GoogleNotConnected(
            "Google is not connected. Run: jarvis-core google-auth --client-json FILE"
        )
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        secrets.set(TOKEN_SECRET_KEY, creds.to_json())
    if not creds.valid:
        raise GoogleNotConnected("Google credentials are invalid; authorize again.")
    return creds


def _gmail():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=credentials(), cache_discovery=False)


def _calendar():
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=credentials(), cache_discovery=False)


def inbox_summary(max_results: int = 10) -> list[dict[str, str]]:
    svc = _gmail()
    result = svc.users().messages().list(
        userId="me",
        q="is:unread newer_than:1d",
        maxResults=max_results,
    ).execute()
    rows = []
    for item in result.get("messages", []):
        msg = svc.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        rows.append({
            "id": item["id"],
            "from": headers.get("from", "(unknown sender)"),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
        })
    return rows


def today_events() -> list[dict[str, str]]:
    svc = _calendar()
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    result = svc.events().list(
        calendarId="primary",
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    rows = []
    for event in result.get("items", []):
        s = event.get("start", {})
        e = event.get("end", {})
        rows.append({
            "id": event.get("id", ""),
            "summary": event.get("summary", "(untitled)"),
            "start": s.get("dateTime") or s.get("date") or "",
            "end": e.get("dateTime") or e.get("date") or "",
            "location": event.get("location", ""),
        })
    return rows


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _gmail().users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


def create_event(summary: str, start: str, end: str,
                 description: str = "", location: str = "") -> dict[str, Any]:
    event: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    return _calendar().events().insert(
        calendarId="primary", body=event
    ).execute()
