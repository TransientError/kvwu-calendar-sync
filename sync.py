"""
kvwu-calendar-sync: Outlook (M365) → Google Calendar sync.

Polls Microsoft Graph API for calendar events, filters by response status,
and pushes them to Google Calendar with color-coding.
"""

import sys
import json
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import httpx
import msal
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.toml"
STATE_PATH = BASE_DIR / "state.json"
MS_TOKEN_PATH = BASE_DIR / "ms_token_cache.json"
GOOGLE_TOKEN_PATH = BASE_DIR / "google_token.json"
GOOGLE_CREDS_PATH = BASE_DIR / "credentials.json"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"synced_events": {}}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ─── Microsoft Graph Auth ────────────────────────────────────────────────────


def get_ms_token(config: dict) -> str:
    """Get a valid Microsoft Graph access token using MSAL with device code or cached refresh token."""
    cache = msal.SerializableTokenCache()
    if MS_TOKEN_PATH.exists():
        cache.deserialize(MS_TOKEN_PATH.read_text())

    app = msal.PublicClientApplication(
        client_id=config["microsoft"]["client_id"],
        authority=f"https://login.microsoftonline.com/{config['microsoft']['tenant_id']}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(
            scopes=["https://graph.microsoft.com/Calendars.Read"],
            account=accounts[0],
        )
        if result and "access_token" in result:
            _save_ms_cache(cache)
            return result["access_token"]

    # Interactive auth needed
    flow = app.initiate_device_flow(
        scopes=["https://graph.microsoft.com/Calendars.Read"]
    )
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow: {flow}")

    print(f"\n{'='*60}")
    print(f"Microsoft sign-in required.")
    print(f"Go to: {flow['verification_uri']}")
    print(f"Enter code: {flow['user_code']}")
    print(f"{'='*60}\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    _save_ms_cache(cache)
    return result["access_token"]


def _save_ms_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        MS_TOKEN_PATH.write_text(cache.serialize())


# ─── Google Calendar Auth ────────────────────────────────────────────────────


def get_google_service(config: dict):
    """Get an authenticated Google Calendar service."""
    creds = None

    if GOOGLE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GOOGLE_CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"Missing {GOOGLE_CREDS_PATH}. Download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(GOOGLE_CREDS_PATH), GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=8401)

        GOOGLE_TOKEN_PATH.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


# ─── Outlook Event Fetching ──────────────────────────────────────────────────


def fetch_outlook_events(token: str, config: dict) -> list[dict]:
    """Fetch calendar events from Outlook within the lookahead window."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=config["sync"]["lookahead_days"])

    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end.isoformat(),
        "$select": "id,subject,start,end,isAllDay,isCancelled,responseStatus,location,showAs",
        "$top": 200,
        "$orderby": "start/dateTime",
    }

    headers = {"Authorization": f"Bearer {token}"}
    events = []

    url = f"{GRAPH_BASE}/me/calendarView"
    while url:
        resp = httpx.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        events.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # nextLink includes params

    return events


def filter_events(events: list[dict], config: dict) -> list[dict]:
    """Filter events by response status."""
    include = set(config["sync"]["include_statuses"])
    filtered = []
    for event in events:
        if event.get("isCancelled"):
            continue
        status = event.get("responseStatus", {}).get("response", "none")
        if status in include:
            filtered.append(event)
    return filtered


# ─── Color Mapping ───────────────────────────────────────────────────────────


def determine_color(event: dict, config: dict) -> str:
    """Determine Google Calendar color ID for an event based on rules."""
    rules = config.get("colors", {}).get("rules", [])
    for rule in rules:
        field = rule.get("field", "")
        value = event.get(field, "")

        if "contains" in rule:
            if isinstance(value, str) and rule["contains"].lower() in value.lower():
                return str(rule["color"])
        elif "equals" in rule:
            if value == rule["equals"]:
                return str(rule["color"])

    return str(config.get("colors", {}).get("default", "9"))


# ─── Google Calendar Sync ────────────────────────────────────────────────────


def event_fingerprint(event: dict) -> str:
    """Create a hash of event properties to detect changes."""
    key_parts = [
        event.get("id", ""),
        event.get("subject", ""),
        json.dumps(event.get("start", {})),
        json.dumps(event.get("end", {})),
        str(event.get("isAllDay", False)),
        event.get("location", {}).get("displayName", "") if isinstance(event.get("location"), dict) else "",
    ]
    return hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]


def sync_to_google(events: list[dict], config: dict, service, state: dict) -> dict:
    """Sync filtered Outlook events to Google Calendar."""
    calendar_id = config["sync"]["google_calendar_id"]
    synced = state.get("synced_events", {})
    current_event_ids = set()

    for event in events:
        outlook_id = event["id"]
        current_event_ids.add(outlook_id)
        fingerprint = event_fingerprint(event)

        # Check if already synced and unchanged
        if outlook_id in synced and synced[outlook_id].get("fingerprint") == fingerprint:
            continue

        color = determine_color(event, config)
        google_event = _build_google_event(event, color)

        if outlook_id in synced and synced[outlook_id].get("google_id"):
            # Update existing
            try:
                service.events().update(
                    calendarId=calendar_id,
                    eventId=synced[outlook_id]["google_id"],
                    body=google_event,
                ).execute()
                log.info(f"Updated: {event.get('subject', 'No subject')}")
            except Exception as e:
                log.warning(f"Update failed, recreating: {e}")
                _delete_google_event(service, calendar_id, synced[outlook_id]["google_id"])
                google_id = _create_google_event(service, calendar_id, google_event)
                synced[outlook_id] = {"google_id": google_id, "fingerprint": fingerprint}
                continue
        else:
            # Create new
            google_id = _create_google_event(service, calendar_id, google_event)
            log.info(f"Created: {event.get('subject', 'No subject')}")
            synced[outlook_id] = {"google_id": google_id, "fingerprint": fingerprint}
            continue

        synced[outlook_id] = {
            "google_id": synced[outlook_id]["google_id"],
            "fingerprint": fingerprint,
        }

    # Remove events no longer in Outlook
    stale_ids = set(synced.keys()) - current_event_ids
    for outlook_id in stale_ids:
        google_id = synced[outlook_id].get("google_id")
        if google_id:
            _delete_google_event(service, calendar_id, google_id)
            log.info(f"Deleted stale event: {google_id}")
        del synced[outlook_id]

    state["synced_events"] = synced
    return state


def _build_google_event(event: dict, color: str) -> dict:
    """Convert an Outlook event to a Google Calendar event body."""
    subject = event.get("subject", "Work - Busy")
    location = ""
    if isinstance(event.get("location"), dict):
        location = event["location"].get("displayName", "")

    start = event["start"]
    end = event["end"]

    google_event = {
        "summary": subject,
        "colorId": color,
        "description": "[Synced from Outlook]",
    }

    if location:
        google_event["location"] = location

    if event.get("isAllDay"):
        # All-day events use date, not dateTime
        google_event["start"] = {"date": start["dateTime"][:10]}
        google_event["end"] = {"date": end["dateTime"][:10]}
    else:
        google_event["start"] = {"dateTime": start["dateTime"], "timeZone": start.get("timeZone", "UTC")}
        google_event["end"] = {"dateTime": end["dateTime"], "timeZone": end.get("timeZone", "UTC")}

    return google_event


def _create_google_event(service, calendar_id: str, body: dict) -> str:
    result = service.events().insert(calendarId=calendar_id, body=body).execute()
    return result["id"]


def _delete_google_event(service, calendar_id: str, event_id: str):
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except Exception:
        pass  # Already deleted or not found


# ─── Main ────────────────────────────────────────────────────────────────────


def run_auth(config: dict):
    """Run interactive auth flows for both services."""
    log.info("Authenticating with Microsoft...")
    get_ms_token(config)
    log.info("Microsoft auth successful!")

    log.info("Authenticating with Google...")
    get_google_service(config)
    log.info("Google auth successful!")

    log.info("All tokens saved. You can now run sync via cron.")


def run_sync(config: dict):
    """Run a single sync cycle."""
    log.info("Starting sync...")

    token = get_ms_token(config)
    events = fetch_outlook_events(token, config)
    log.info(f"Fetched {len(events)} events from Outlook")

    filtered = filter_events(events, config)
    log.info(f"After filtering: {len(filtered)} events")

    service = get_google_service(config)
    state = load_state()
    state = sync_to_google(filtered, config, service, state)
    save_state(state)

    log.info("Sync complete!")


def main():
    parser = argparse.ArgumentParser(description="Sync Outlook calendar to Google Calendar")
    parser.add_argument("--auth", action="store_true", help="Run interactive authentication")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and filter but don't push to Google")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        log.error(f"Config not found: {CONFIG_PATH}")
        log.error("Copy config.example.toml to config.toml and fill in your credentials.")
        sys.exit(1)

    config = load_config()

    if args.auth:
        run_auth(config)
    elif args.dry_run:
        token = get_ms_token(config)
        events = fetch_outlook_events(token, config)
        filtered = filter_events(events, config)
        log.info(f"Would sync {len(filtered)} events:")
        for e in filtered:
            color = determine_color(e, config)
            log.info(f"  [{color}] {e.get('subject', 'No subject')} "
                     f"({e['start']['dateTime'][:16]})")
    else:
        run_sync(config)


if __name__ == "__main__":
    main()
