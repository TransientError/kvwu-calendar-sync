# kvwu-calendar-sync

Syncs Microsoft 365 Outlook calendar events to Google Calendar with filtering and color-coding.

## Features

- Polls Outlook calendar via Microsoft Graph API every 5 minutes
- Pushes events to Google Calendar as owned events (editable, color-codable)
- Filters by response status: only syncs **accepted** events by default
- Optional `showAs` filter (e.g., only sync "busy" events)
- Color-codes events based on configurable rules
- Retry with exponential backoff for transient failures
- File lock prevents overlapping cron runs
- Atomic state writes prevent corruption on crash
- Runs headless on Raspberry Pi (device code for MS, console flow for Google)

## Setup

### Prerequisites

- Python 3.9+
- Microsoft 365 work account
- Google account
- Azure AD app registration (for Graph API access)
- Google Cloud project with Calendar API enabled

### 1. Azure App Registration

1. Go to [Azure Portal](https://portal.azure.com) → Azure Active Directory → App registrations
2. New registration → Name: "Calendar Sync" → Accounts in this organizational directory only
3. Under **Authentication** → Add platform → Mobile and desktop → check `https://login.microsoftonline.com/common/oauth2/nativeclient`
4. Under **API Permissions**, add: `Calendars.Read` (delegated)
5. Grant admin consent (or have your admin do it)
6. Note your **Client ID** and **Tenant ID** (no secret needed — uses device code flow)

### 2. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable Google Calendar API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the `credentials.json` file into the project directory

### 3. Install

```bash
cd kvwu-calendar-sync
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
# Edit config.toml with your Client ID and Tenant ID
```

### 4. Authenticate

On a machine with a browser:
```bash
python sync.py --auth
```

On a headless Pi (no browser):
```bash
python sync.py --auth --headless
```

The Microsoft flow will display a device code to enter at https://microsoft.com/devicelogin.
The Google flow (headless) will print a URL — open it on any machine, authorize, and paste the code back.

### 5. Run

```bash
python sync.py           # normal sync
python sync.py --dry-run # preview what would sync
python sync.py --verbose # debug output
python sync.py --quiet   # only warnings/errors (good for cron)
```

### 6. Cron (Raspberry Pi)

```bash
crontab -e
# Add:
*/5 * * * * cd /home/pi/kvwu-calendar-sync && .venv/bin/python sync.py --quiet >> /var/log/calendar-sync.log 2>&1
```

## Configuration

See `config.example.toml` for all options including:
- Sync window (how far ahead to look)
- Response status filter (accepted, tentative, etc.)
- `showAs` filter (busy, free, oof, etc.)
- Color mapping rules
- Google Calendar target
- Retry settings (max attempts, backoff)

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `AuthExpiredError: Microsoft token expired` | Re-run `python sync.py --auth` |
| `AuthExpiredError: Google token refresh failed` | Re-run `python sync.py --auth` |
| `Another sync is already running` | Previous run still active or crashed — delete `.sync.lock` |
| `Config not found` | Copy `config.example.toml` → `config.toml` |
| 401 from Graph API | Check Client ID / Tenant ID; ensure `Calendars.Read` permission is granted |
