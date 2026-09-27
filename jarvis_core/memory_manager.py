"""Local Memory Manager 2.0 for Jarvis.

Builds a private searchable index over the Markdown vault, reports hygiene
issues, records confirmed memories, and archives notes without deleting them.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from typing import Any

AGENT_HOME = Path.home() / "my-agent"
VAULT = AGENT_HOME / "Memory"
DB_PATH = VAULT / ".jarvis-memory-index.sqlite3"


def _connect() -> sqlite3.Connection:
    VAULT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            path TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT '',
            mtime REAL NOT NULL,
            sha256 TEXT NOT NULL
        )
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
            USING fts5(path UNINDEXED, title, body)
            """
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _title(path: Path, text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _markdown_files() -> list[Path]:
    return sorted(
        p for p in VAULT.rglob("*.md")
        if p.is_file() and ".git" not in p.parts
    )


def refresh() -> dict[str, Any]:
    conn = _connect()
    seen: set[str] = set()
    indexed = 0
    for path in _markdown_files():
        rel = str(path.relative_to(VAULT))
        seen.add(rel)
        try:
            raw = path.read_text(encoding="utf-8")
            stat = path.stat()
        except OSError:
            continue
        meta = _frontmatter(raw)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO notes(path,title,body,status,project,type,mtime,sha256)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              title=excluded.title,
              body=excluded.body,
              status=excluded.status,
              project=excluded.project,
              type=excluded.type,
              mtime=excluded.mtime,
              sha256=excluded.sha256
            """,
            (
                rel,
                _title(path, raw),
                raw,
                meta.get("status", ""),
                meta.get("project", ""),
                meta.get("type", ""),
                stat.st_mtime,
                digest,
            ),
        )
        indexed += 1

    existing = {
        row[0] for row in conn.execute("SELECT path FROM notes").fetchall()
    }
    for stale in existing - seen:
        conn.execute("DELETE FROM notes WHERE path=?", (stale,))

    # Rebuild the contentless FTS table when available.
    try:
        conn.execute("DELETE FROM notes_fts")
        conn.executemany(
            "INSERT INTO notes_fts(path,title,body) VALUES(?,?,?)",
            [
                (r["path"], r["title"], r["body"])
                for r in conn.execute("SELECT path,title,body FROM notes")
            ],
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    return {"indexed": indexed, "database": str(DB_PATH)}


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    refresh()
    conn = _connect()
    rows: list[sqlite3.Row]
    try:
        # Quote punctuation-heavy tokens so ordinary queries do not break FTS.
        terms = [t for t in re.findall(r"[\w.-]+", query) if t]
        fts = " AND ".join(f'"{t}"' for t in terms) or '""'
        rows = conn.execute(
            """
            SELECT n.path,n.title,n.status,n.project,n.type,
                   snippet(notes_fts, 2, '[', ']', ' … ', 22) AS snippet
              FROM notes_fts
              JOIN notes n ON n.path=notes_fts.path
             WHERE notes_fts MATCH ?
             LIMIT ?
            """,
            (fts, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT path,title,status,project,type,
                   substr(body,1,600) AS snippet
              FROM notes
             WHERE title LIKE ? OR body LIKE ?
             ORDER BY mtime DESC
             LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def health() -> dict[str, Any]:
    refresh()
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    missing_meta = conn.execute(
        "SELECT path FROM notes WHERE status='' OR type=''"
    ).fetchall()
    duplicates = conn.execute(
        """
        SELECT sha256, COUNT(*) AS n, GROUP_CONCAT(path, ' | ') AS paths
          FROM notes
         GROUP BY sha256
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    large: list[dict[str, Any]] = []
    for path in _markdown_files():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 100_000:
            large.append({"path": str(path.relative_to(VAULT)), "bytes": size})
    return {
        "notes": total,
        "missing_metadata": [r["path"] for r in missing_meta],
        "exact_duplicates": [
            {"count": r["n"], "paths": r["paths"]} for r in duplicates
        ],
        "large_notes": large,
    }


def remember(text: str, category: str = "general") -> Path:
    if not text.strip():
        raise ValueError("Memory text is empty.")
    inbox = VAULT / "00 - Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "memory"
    path = inbox / f"{now:%Y%m%d-%H%M%S}-{slug}.md"
    path.write_text(
        f"""---
status: active
project: personal
type: memory
source: user-confirmed
confidence: high
category: {category}
created: {now.isoformat()}
---
# Confirmed memory

{text.strip()}
""",
        encoding="utf-8",
    )
    refresh()
    return path


def archive(rel_path: str) -> Path:
    src = (VAULT / rel_path).resolve()
    if VAULT.resolve() not in src.parents:
        raise ValueError("Memory path must stay inside the vault.")
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(rel_path)
    archive_dir = VAULT / "99 - Archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / src.name
    if dst.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = archive_dir / f"{src.stem}-{stamp}{src.suffix}"
    shutil.move(str(src), str(dst))
    refresh()
    return dst


def cmd_refresh(_args) -> int:
    print(json.dumps(refresh(), indent=2))
    return 0


def cmd_search(args) -> int:
    rows = search(args.query, args.limit)
    if not rows:
        print("No matching memory.")
        return 0
    for r in rows:
        print(f"{r['path']} :: {r['title']}")
        snippet = " ".join(str(r.get("snippet") or "").split())
        if snippet:
            print("  " + snippet[:500])
    return 0


def cmd_health(_args) -> int:
    print(json.dumps(health(), indent=2, ensure_ascii=False))
    return 0


def cmd_remember(args) -> int:
    print(remember(args.text, args.category))
    return 0


def cmd_archive(args) -> int:
    print(archive(args.path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis-memory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("refresh")
    s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("health")
    s.set_defaults(func=cmd_health)

    s = sub.add_parser("remember")
    s.add_argument("--text", required=True)
    s.add_argument("--category", default="general")
    s.set_defaults(func=cmd_remember)

    s = sub.add_parser("archive")
    s.add_argument("path")
    s.set_defaults(func=cmd_archive)

    return p


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"Jarvis memory error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
