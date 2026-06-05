# Copilot Instructions

## Project Overview

kvwu-calendar-sync is a one-way sync from Microsoft 365 Outlook → Google Calendar.
It runs as a cron job on a Raspberry Pi, polling every 5 minutes.

## Architecture

- Single-file script: `sync.py`
- Config: `config.toml` (from `config.example.toml`)
- State tracking: `state.json` (fingerprint-based diffing, auto-cleanup)
- Auth: MSAL device code (Microsoft), OAuth console flow (Google)
- Deps managed by uv (`pyproject.toml` + `uv.lock`)

## Key Design Decisions

- **Only accepted events sync** (declined/tentative filtered out)
- **`always_sync_subjects`** bypasses the status filter (used for on-call schedules)
- **Dedicated Google Calendar required** — code rejects `"primary"` to avoid cluttering personal calendar
- **Headless-first** — designed for Pi with no browser (device code + console OAuth)
- **Idempotent** — safe to re-run; fingerprint diffing means no duplicate pushes
- **File lock** prevents overlapping cron runs
- **Atomic state writes** via temp file + os.replace

## Code Style

- Python 3.11+ (uses stdlib `tomllib`)
- No classes — functional style with module-level functions
- Color names are lowercase strings mapped to Google Calendar IDs internally
- Config values are lowercase (color names, status strings)

## What NOT to Do

- Don't add webhooks — polling is intentional (simpler, works behind NAT on Pi)
- Don't sync back from Google → Outlook (one-way only)
- Don't use `"primary"` as google_calendar_id
- Don't add heavy frameworks — this is a single-file script by design
