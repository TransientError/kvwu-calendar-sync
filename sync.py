"""
kvwu-calendar-sync: Outlook (M365) → Google Calendar sync.

Polls Microsoft Graph API for calendar events, filters by response status,
and pushes them to Google Calendar with color-coding.
"""

import os
import sys
import json
import hashlib
import logging
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

import tomllib

import httpx
import msal
from filelock import FileLock, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.toml"
STATE_PATH = BASE_DIR / "state.json"
LOCK_PATH = BASE_DIR / ".sync.lock"
MS_TOKEN_PATH = BASE_DIR / "ms_token_cache.json"
GOOGLE_TOKEN_PATH = BASE_DIR / "google_token.json"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Google Calendar color name → ID mapping
GOOGLE_COLORS = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
}


def resolve_color(value: str) -> str:
    """Resolve a color name or numeric ID to a Google Calendar color ID."""
    if value in GOOGLE_COLORS:
        return GOOGLE_COLORS[value]
    if value in GOOGLE_COLORS.values():
        return value
    valid = ", ".join(GOOGLE_COLORS.keys())
    raise ValueError(f"Unknown color '{value}'. Valid options: {valid}")


class AuthExpiredError(Exception):
    """Raised when a token is expired/revoked and interactive re-auth is needed."""


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"synced_events": {}}


def save_state(state: dict):
    """Atomically write state to disk via temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _retry_config(config: dict):
    """Build tenacity retry kwargs from config."""
    retry_cfg = config.get("retry", {})
    max_attempts = retry_cfg.get("max_attempts", 3)
    initial_wait = retry_cfg.get("initial_wait", 2)
    return {
        "stop": stop_after_attempt(max_attempts),
        "wait": wait_exponential(multiplier=initial_wait, min=initial_wait, max=30),
        "retry": retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError, HttpError)),
        "reraise": True,
    }


# ─── Microsoft Graph Auth ────────────────────────────────────────────────────


def get_ms_token(config: dict) -> str:
    """Get a valid Microsoft Graph access token using MSAL device code flow."""
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
        if result and "error" in result:
            raise AuthExpiredError(
                f"Microsoft token expired or revoked: {result.get('error_description', result['error'])}. "
                "Re-run with --auth to re-authenticate."
            )

    # Interactive device code auth
    flow = app.initiate_device_flow(
        scopes=["https://graph.microsoft.com/Calendars.Read"]
    )
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow: {flow}")

    print(f"\n{'='*60}")
    print("Microsoft sign-in required.")
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


def get_google_service(config: dict, headless: bool = False):
    """Get an authenticated Google Calendar service.

    Args:
        headless: If True, use console-based OAuth (no browser needed).
    """
    creds_path = Path(config.get("google", {}).get("credentials_file", "credentials.json"))
    if not creds_path.is_absolute():
        creds_path = BASE_DIR / creds_path

    creds = None
    if GOOGLE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise AuthExpiredError(
                    f"Google token refresh failed: {e}. Re-run with --auth to re-authenticate."
                )
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Missing {creds_path}. Download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), GOOGLE_SCOPES
            )
            if headless:
                creds = flow.run_console()
            else:
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

    retry_kwargs = _retry_config(config)

    @retry(**retry_kwargs)
    def _fetch_page(url, req_params):
        resp = httpx.get(url, headers=headers, params=req_params, timeout=30)
        if resp.status_code == 401:
            raise AuthExpiredError(
                "Microsoft token rejected (401). Re-run with --auth to re-authenticate."
            )
        resp.raise_for_status()
        return resp.json()

    url = f"{GRAPH_BASE}/me/calendarView"
    while url:
        data = _fetch_page(url, params)
        events.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # nextLink includes params

    return events


def filter_events(events: list[dict], config: dict) -> list[dict]:
    """Filter events by response status and optionally by showAs.

    Events matching an 'always_sync' subject pattern bypass the status filter.
    """
    include_statuses = set(config["sync"]["include_statuses"])
    include_show_as = config["sync"].get("include_show_as")
    if include_show_as:
        include_show_as = set(include_show_as)

    always_sync_patterns = [p.lower() for p in config["sync"].get("always_sync_subjects", [])]

    filtered = []
    for event in events:
        if event.get("isCancelled"):
            continue

        subject = event.get("subject", "").lower()
        bypass_status = any(pattern in subject for pattern in always_sync_patterns)

        if not bypass_status:
            status = event.get("responseStatus", {}).get("response", "none")
            if status not in include_statuses:
                continue

        if include_show_as and event.get("showAs", "").lower() not in include_show_as:
            continue
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
                return resolve_color(str(rule["color"]))
        elif "equals" in rule:
            if value == rule["equals"]:
                return resolve_color(str(rule["color"]))

    return resolve_color(str(config.get("colors", {}).get("default", "blueberry")))


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
    retry_kwargs = _retry_config(config)

    @retry(**retry_kwargs)
    def _insert(body):
        return service.events().insert(calendarId=calendar_id, body=body).execute()

    @retry(**retry_kwargs)
    def _update(event_id, body):
        return service.events().update(calendarId=calendar_id, eventId=event_id, body=body).execute()

    @retry(**retry_kwargs)
    def _delete(event_id):
        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as e:
            if e.resp.status == 404 or e.resp.status == 410:
                pass  # Already deleted
            else:
                raise

    for event in events:
        outlook_id = event["id"]
        current_event_ids.add(outlook_id)
        fingerprint = event_fingerprint(event)

        if outlook_id in synced and synced[outlook_id].get("fingerprint") == fingerprint:
            continue

        color = determine_color(event, config)
        google_event = _build_google_event(event, color)

        if outlook_id in synced and synced[outlook_id].get("google_id"):
            try:
                _update(synced[outlook_id]["google_id"], google_event)
                log.info(f"Updated: {event.get('subject', 'No subject')}")
                synced[outlook_id] = {
                    "google_id": synced[outlook_id]["google_id"],
                    "fingerprint": fingerprint,
                }
            except Exception as e:
                log.warning(f"Update failed, recreating: {e}")
                _delete(synced[outlook_id]["google_id"])
                result = _insert(google_event)
                synced[outlook_id] = {"google_id": result["id"], "fingerprint": fingerprint}
        else:
            result = _insert(google_event)
            log.info(f"Created: {event.get('subject', 'No subject')}")
            synced[outlook_id] = {"google_id": result["id"], "fingerprint": fingerprint}

    # Remove events no longer in Outlook
    stale_ids = set(synced.keys()) - current_event_ids
    for outlook_id in stale_ids:
        google_id = synced[outlook_id].get("google_id")
        if google_id:
            _delete(google_id)
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
        google_event["start"] = {"date": start["dateTime"][:10]}
        google_event["end"] = {"date": end["dateTime"][:10]}
    else:
        google_event["start"] = {"dateTime": start["dateTime"], "timeZone": start.get("timeZone", "UTC")}
        google_event["end"] = {"dateTime": end["dateTime"], "timeZone": end.get("timeZone", "UTC")}

    return google_event


# ─── Main ────────────────────────────────────────────────────────────────────


def run_auth(config: dict, headless: bool = False):
    """Run interactive auth flows for both services."""
    log.info("Authenticating with Microsoft...")
    get_ms_token(config)
    log.info("Microsoft auth successful!")

    log.info("Authenticating with Google...")
    get_google_service(config, headless=headless)
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
    parser.add_argument("--headless", action="store_true",
                        help="Use console-based Google OAuth (no browser, for headless Pi)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and filter but don't push to Google")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--quiet", action="store_true", help="Only show warnings and errors")
    args = parser.parse_args()

    # Configure logging level
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not CONFIG_PATH.exists():
        log.error(f"Config not found: {CONFIG_PATH}")
        log.error("Copy config.example.toml to config.toml and fill in your credentials.")
        sys.exit(1)

    config = load_config()

    calendar_id = config.get("sync", {}).get("google_calendar_id", "")
    if not calendar_id or calendar_id == "primary":
        log.error("google_calendar_id is not set (or is 'primary').")
        log.error("Create a dedicated Google Calendar and set its ID in config.toml.")
        log.error("Find it: Google Calendar → Settings → <calendar> → Integrate calendar → Calendar ID")
        sys.exit(1)

    if args.auth:
        run_auth(config, headless=args.headless)
        return

    # Acquire lock to prevent overlapping runs
    lock = FileLock(LOCK_PATH, timeout=10)
    try:
        with lock:
            if args.dry_run:
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
    except Timeout:
        log.warning("Another sync is already running. Skipping.")
        sys.exit(0)
    except AuthExpiredError as e:
        log.error(str(e))
        sys.exit(2)


if __name__ == "__main__":
    main()
