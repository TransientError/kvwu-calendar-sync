# kvwu-calendar-sync

Syncs Microsoft 365 Outlook calendar events to Google Calendar with filtering and color-coding.

## Features

- Polls Outlook calendar via Microsoft Graph API every 5 minutes
- Pushes events to Google Calendar as owned events (editable, color-codable)
- Filters by response status: only syncs **accepted** events by default
- Color-codes events based on configurable rules
- Runs as a cron job on Raspberry Pi

## Setup

### Prerequisites

- Python 3.9+
- Microsoft 365 work account
- Google account
- Azure AD app registration (for Graph API access)
- Google Cloud project with Calendar API enabled

### 1. Azure App Registration

1. Go to [Azure Portal](https://portal.azure.com) → Azure Active Directory → App registrations
2. New registration → Name: "Calendar Sync" → Personal use
3. Redirect URI: `http://localhost:8400/callback`
4. Under API Permissions, add: `Calendars.Read` (delegated)
5. Under Certificates & secrets, create a client secret
6. Note your **Client ID**, **Tenant ID**, and **Client Secret**

### 2. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable Google Calendar API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the `credentials.json` file

### 3. Install

```bash
cd kvwu-calendar-sync
pip install -r requirements.txt
cp config.example.toml config.toml
# Edit config.toml with your credentials
```

### 4. Authenticate

```bash
python sync.py --auth
```

This will open a browser for both Microsoft and Google OAuth flows and save refresh tokens locally.

### 5. Run

```bash
python sync.py
```

### 6. Cron (Raspberry Pi)

```bash
crontab -e
# Add:
*/5 * * * * cd /home/pi/kvwu-calendar-sync && python sync.py >> /var/log/calendar-sync.log 2>&1
```

## Configuration

See `config.example.toml` for all options including:
- Sync window (how far ahead to look)
- Response status filter (accepted, tentative, etc.)
- Color mapping rules
- Google Calendar target
