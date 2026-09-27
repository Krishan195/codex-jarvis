"""Daily read-only Jarvis briefing."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess

from .google_workspace import inbox_summary, today_events

CACHE_DIR = Path.home() / ".cache" / "codex-jarvis"


def build() -> str:
    events = today_events()
    mail = inbox_summary()

    now = datetime.now().astimezone()
    lines = [f"Good morning, Boss. Here is your {now:%A} briefing."]

    if events:
        lines.append(f"You have {len(events)} calendar event{'s' if len(events) != 1 else ''} today.")
        for e in events[:5]:
            start = e["start"]
            try:
                dt = datetime.fromisoformat(start)
                when = dt.astimezone().strftime("%-I:%M %p")
            except Exception:
                when = start
            lines.append(f"{when}: {e['summary']}.")
    else:
        lines.append("Your calendar is clear today.")

    if mail:
        lines.append(
            f"You have {len(mail)} unread email{'s' if len(mail) != 1 else ''} from the last day."
        )
        for m in mail[:5]:
            sender = m["from"].split("<")[0].strip().strip('"')
            lines.append(f"From {sender}: {m['subject']}.")
    else:
        lines.append("No unread email from the last day.")

    return "\n".join(lines)


def save(text: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "latest-briefing.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def notify(text: str) -> None:
    subprocess.run(
        ["notify-send", "Jarvis daily brief", text],
        check=False,
        timeout=10,
    )
