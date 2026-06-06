# kvwu-calendar-sync

Syncs Microsoft 365 Outlook calendar events to Google Calendar with filtering and color-coding.

## Features

- Two event source modes:
  - **ICS feed** (recommended): Uses a published Outlook calendar URL — no Azure app registration needed
  - **Graph API**: Uses Microsoft Graph with device code auth (requires Azure AD app)
- Pushes events to Google Calendar as owned events (editable, color-codable)
- Filters by response status: only syncs **accepted** events by default
- In ICS mode, uses `X-MICROSOFT-CDO-BUSYSTATUS` to determine acceptance (BUSY = accepted)
- Optional `showAs` filter (e.g., only sync "busy" events)
- Subject-based always-sync (for on-call) and skip lists
- Color-codes events based on configurable rules
- Retry with exponential backoff for transient failures
- File lock prevents overlapping cron runs
- Atomic state writes prevent corruption on crash
- Runs headless on Raspberry Pi (device code for MS, localhost redirect for Google)

## Setup

### Prerequisites

- Python 3.11+
- Google account with a dedicated calendar
- One of:
  - Published Outlook calendar ICS URL (ICS mode — simpler), or
  - Azure AD app registration + Microsoft 365 work account (Graph API mode)

### 1. Event Source: ICS Feed (Recommended)

1. Go to **Outlook on the web** → Settings → Calendar → Shared calendars → Publish
2. Select your calendar, set permission to "Can view all details"
3. Copy the **ICS link**
4. Set `ics_url` in `config.toml`
5. Set `user_email` to your work email (for response status detection)

### 1b. Event Source: Graph API (Alternative)

1. Go to [Azure Portal](https://portal.azure.com) → Azure Active Directory → App registrations
2. New registration → Name: "Calendar Sync" → Accounts in any organizational directory (multi-tenant)
3. Under **Authentication** → Add platform → Mobile and desktop → check `https://login.microsoftonline.com/common/oauth2/nativeclient`
4. Under **API Permissions**, add: `Calendars.Read` (delegated)
5. Note your **Client ID**; set `tenant_id` to `common` for multi-tenant
6. Leave `ics_url` blank in config

### 2. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable Google Calendar API
3. Configure OAuth consent screen (External + add yourself as test user, or Internal for Workspace)
4. Create OAuth 2.0 credentials (Desktop app type)
5. Download the JSON file as `credentials.json` into the project directory

### 3. Install

```bash
cd kvwu-calendar-sync
uv sync
```

Or without uv:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r <(uv pip compile pyproject.toml)
```

### 4. Configure & Authenticate

```bash
cp config.example.toml config.toml
# Edit config.toml — set ics_url (or Microsoft creds), google_calendar_id, etc.
```

Run auth (only Google auth needed in ICS mode):
```bash
uv run sync.py --auth
```

On a headless machine (Pi):
```bash
uv run sync.py --auth --headless
```

The Google flow will start a local server on port 8401 — open the printed URL in any browser, authorize, and the redirect completes the flow.

### 5. Run

```bash
uv run sync.py           # normal sync
uv run sync.py --dry-run # preview what would sync
uv run sync.py --verbose # debug output
uv run sync.py --quiet   # only warnings/errors (good for cron)
```

### 6. Cron (Raspberry Pi)

```bash
crontab -e
# Add:
PATH=/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin
*/5 * * * * cd /home/pi/utils/kvwu-calendar-sync && uv run sync.py --quiet >> /tmp/calendar-sync.log 2>&1
```

## Configuration

See `config.example.toml` for all options including:
- Event source (`ics_url` or Graph API credentials)
- Sync window (how far ahead to look)
- Response status filter (accepted, tentative, etc.)
- `showAs` / BUSYSTATUS filter (busy, free, oof, etc.)
- Subject-based always-sync list (bypasses all filters)
- Subject-based skip lists (exact match and pattern match)
- Color mapping rules
- Google Calendar target
- Retry settings (max attempts, backoff)

## ICS Mode Notes

Published Outlook ICS feeds have some limitations vs Graph API:
- **No attendee data** — response status is inferred from `X-MICROSOFT-CDO-BUSYSTATUS`
- **No organizer field** — solo event detection may not work; use `skip_subjects` instead
- **Feed lag** — changes can take 15-30 minutes to appear in the published feed
- **"Canceled:" prefix** — Outlook puts this in the subject instead of setting STATUS:CANCELLED; the script handles this automatically

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `AuthExpiredError: Microsoft token expired` | Re-run `uv run sync.py --auth` |
| `AuthExpiredError: Google token refresh failed` | Re-run `uv run sync.py --auth` |
| `Another sync is already running` | Previous run still active or crashed — delete `.sync.lock` |
| `Config not found` | Copy `config.example.toml` → `config.toml` |
| Events not appearing (ICS mode) | Feed may be lagging; wait 15-30 min or check BUSYSTATUS with `--verbose` |
| Duplicate events after crash | Delete orphans from Google Calendar; state.json tracks known events |
