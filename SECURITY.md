# Security model

Codex Jarvis is designed so long-lived credentials do not live in source code,
the Memory vault, `.env` files, or logs.

## Credential storage

On Linux, Jarvis uses the desktop Secret Service via `secret-tool`
(libsecret). OAuth client configuration and refresh tokens are stored there.

Use:

```bash
jarvis-core secret-set NAME
jarvis-core secret-delete NAME
```

The value is entered with hidden terminal input. Jarvis Memory may record the
credential's *name/location*, but never the value.

For normal websites that do not offer OAuth/API access, prefer a dedicated
browser profile that the user signs into manually. Do not give Jarvis the
account password when an authenticated browser session is sufficient.

## Google Workspace

Jarvis requests only the scopes needed for the current design:

- Gmail read
- Gmail send
- Calendar read
- Calendar event create/update

OAuth grants the application capability, while Jarvis's local approval broker
adds an additional policy gate for consequential writes.

Read-only briefing operations may run unattended. Sending an email or creating
a calendar event requires a fresh one-time approval request.

## Approval broker

External writes use this flow:

1. Jarvis prepares an action.
2. `jarvis-core request-email` or `request-event` creates a local request.
3. The user sees the exact action summary and request ID.
4. The user explicitly approves or rejects it.
5. Approval expires and is single-use.
6. Execution consumes the approval *before* the external side effect.

Requests are stored under:

```text
~/.local/share/codex-jarvis/approvals/
```

The audit trail is:

```text
~/.local/share/codex-jarvis/audit.jsonl
```

Neither location should contain passwords, API keys, OAuth refresh tokens, or
session cookies.

## Google OAuth setup

Create a Google Cloud OAuth **Desktop app** client for your own account, enable
the Gmail API and Google Calendar API, and download the client JSON.

Then run:

```bash
jarvis-core google-auth --client-json ~/Downloads/client_secret_....json
```

Jarvis reads that file once, stores the client configuration and resulting
refresh token in Secret Service, and enables the daily briefing timer. The
downloaded JSON is not required afterward; remove it from Downloads when you
are satisfied the connection works.

Never commit a client-secret JSON file. The repository ignores common
credential-export filenames as an extra guard.

## Daily briefing

After Google OAuth completes:

```bash
jarvis-core briefing
systemctl --user status jarvis-briefing.timer
```

The installed timer defaults to 08:00 local time and is not enabled until
Google authorization succeeds.
