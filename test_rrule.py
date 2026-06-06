"""Tests for RRULE expansion logic in ICS mode."""

import pytest
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vRecur, vDatetime, vDate, vDDDTypes
from sync import _expand_rrule_events, _build_occurrence_dict


# --- Helpers ---

def _make_vevent(
    uid="test-uid-1",
    summary="Weekly Standup",
    dtstart=None,
    dtend=None,
    rrule=None,
    exdates=None,
    busystatus="BUSY",
    location="",
    status=None,
    all_day=False,
):
    """Build a VEVENT icalendar component for testing."""
    cal = Calendar()
    ev = Event()
    ev.add("UID", uid)
    ev.add("SUMMARY", summary)

    if dtstart is None:
        if all_day:
            dtstart = date(2026, 6, 1)  # Monday
        else:
            dtstart = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

    if all_day:
        ev.add("DTSTART", dtstart)
        if dtend is None:
            dtend = dtstart + timedelta(days=1)
        ev.add("DTEND", dtend)
    else:
        ev.add("DTSTART", dtstart)
        if dtend is None:
            dtend = dtstart + timedelta(hours=1)
        ev.add("DTEND", dtend)

    if rrule is None:
        rrule = {"FREQ": ["WEEKLY"], "BYDAY": ["MO"]}
    ev.add("RRULE", rrule)

    if exdates:
        for exd in exdates:
            ev.add("EXDATE", exd)

    ev.add("X-MICROSOFT-CDO-BUSYSTATUS", busystatus)

    if location:
        ev.add("LOCATION", location)
    if status:
        ev.add("STATUS", status)

    cal.add_component(ev)
    # Walk to get the VEVENT component (same as real parsing)
    for comp in cal.walk():
        if comp.name == "VEVENT":
            return comp
    raise RuntimeError("No VEVENT found")


USER_EMAIL = "user@example.com"


# --- Window helpers ---

def _window(start_date, end_date):
    """Create UTC-aware window boundaries from dates."""
    return (
        datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc),
        datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc),
    )


# === Test: Weekly recurrence expands correctly ===

class TestWeeklyExpansion:
    """A WEEKLY;BYDAY=MO event starting June 1 2026 (Monday)."""

    def test_expands_mondays_in_four_week_window(self):
        vevent = _make_vevent(rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 28))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        # Should have 4 Mondays: Jun 1, 8, 15, 22 (28 is end of day so Jun 22 is last)
        assert len(events) == 4
        starts = [e["start"]["dateTime"] for e in events]
        assert "2026-06-01T09:00:00" in starts
        assert "2026-06-08T09:00:00" in starts
        assert "2026-06-15T09:00:00" in starts
        assert "2026-06-22T09:00:00" in starts

    def test_no_occurrences_outside_window(self):
        vevent = _make_vevent(rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        # Window is Thu-Fri (no Monday)
        start, end = _window(date(2026, 6, 4), date(2026, 6, 5))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 0

    def test_preserves_timezone_in_output(self):
        vevent = _make_vevent(rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 1
        assert events[0]["start"]["timeZone"] == "America/Los_Angeles"

    def test_duration_preserved(self):
        """30-min event stays 30 min in each occurrence."""
        dtstart = datetime(2026, 6, 1, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        dtend = datetime(2026, 6, 1, 14, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        vevent = _make_vevent(dtstart=dtstart, dtend=dtend, rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["start"]["dateTime"] == "2026-06-01T14:00:00"
        assert events[0]["end"]["dateTime"] == "2026-06-01T14:30:00"


# === Test: EXDATE exclusions ===

class TestExdateExclusions:
    """EXDATE removes specific dates from the recurrence."""

    def test_excluded_date_not_in_results(self):
        excluded = datetime(2026, 6, 8, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        vevent = _make_vevent(
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
            exdates=[excluded],
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 21))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        starts = [e["start"]["dateTime"] for e in events]
        assert "2026-06-08T09:00:00" not in starts
        # Other Mondays still present
        assert "2026-06-01T09:00:00" in starts
        assert "2026-06-15T09:00:00" in starts

    def test_multiple_exdates(self):
        excluded1 = datetime(2026, 6, 8, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        excluded2 = datetime(2026, 6, 15, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        vevent = _make_vevent(
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
            exdates=[excluded1, excluded2],
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 21))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        starts = [e["start"]["dateTime"] for e in events]
        assert "2026-06-08T09:00:00" not in starts
        assert "2026-06-15T09:00:00" not in starts
        assert "2026-06-01T09:00:00" in starts


# === Test: All-day recurring events ===

class TestAllDayRecurrence:
    """All-day events expand correctly."""

    def test_daily_all_day_event(self):
        vevent = _make_vevent(
            summary="Team Holiday",
            all_day=True,
            dtstart=date(2026, 6, 1),
            dtend=date(2026, 6, 2),
            rrule={"FREQ": ["DAILY"], "COUNT": [5]},
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 30))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 5
        assert events[0]["isAllDay"] is True

    def test_all_day_uses_date_format(self):
        vevent = _make_vevent(
            all_day=True,
            dtstart=date(2026, 6, 1),
            dtend=date(2026, 6, 2),
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 1
        # All-day should use date format, not datetime
        assert "T" not in events[0]["start"]["dateTime"]


# === Test: UNTIL boundary ===

class TestUntilBoundary:
    """RRULE with UNTIL stops at the correct date."""

    def test_stops_at_until_date(self):
        vevent = _make_vevent(
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"], "UNTIL": [datetime(2026, 6, 15, 23, 59, 59, tzinfo=timezone.utc)]},
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 30))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        starts = [e["start"]["dateTime"] for e in events]
        assert "2026-06-01T09:00:00" in starts
        assert "2026-06-08T09:00:00" in starts
        assert "2026-06-15T09:00:00" in starts
        # Jun 22 is after UNTIL
        assert "2026-06-22T09:00:00" not in starts

    def test_count_limits_occurrences(self):
        vevent = _make_vevent(
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"], "COUNT": [3]},
        )
        start, end = _window(date(2026, 6, 1), date(2026, 12, 31))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 3


# === Test: Deduplication (event IDs) ===

class TestEventIds:
    """Each occurrence gets a unique, deterministic ID."""

    def test_unique_ids_per_occurrence(self):
        vevent = _make_vevent(rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 21))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 3  # Jun 1, 8, 15
        ids = [e["id"] for e in events]
        assert len(ids) == len(set(ids))

    def test_id_contains_uid_and_timestamp(self):
        vevent = _make_vevent(uid="abc-123", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["id"].startswith("abc-123_")


# === Test: Metadata passthrough ===

class TestMetadataPassthrough:
    """BUSYSTATUS, location, subject, cancelled status are propagated."""

    def test_busystatus_maps_to_show_as(self):
        vevent = _make_vevent(busystatus="TENTATIVE", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["showAs"] == "tentative"

    def test_location_preserved(self):
        vevent = _make_vevent(location="Room 42", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["location"]["displayName"] == "Room 42"

    def test_cancelled_status(self):
        vevent = _make_vevent(status="CANCELLED", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["isCancelled"] is True

    def test_cancelled_prefix_in_subject(self):
        vevent = _make_vevent(summary="Canceled: Old Meeting", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]})
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert events[0]["isCancelled"] is True

    def test_subject_preserved(self):
        vevent = _make_vevent(summary="Prism Quick Standup", rrule={"FREQ": ["WEEKLY"], "BYDAY": ["TU", "WE", "TH"]})
        start, end = _window(date(2026, 6, 2), date(2026, 6, 5))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 3
        for ev in events:
            assert ev["subject"] == "Prism Quick Standup"


# === Test: Edge cases ===

# === Test: DST transitions ===

class TestDSTTransitions:
    """Events crossing DST boundaries maintain correct local time."""

    def test_fall_back_keeps_local_time(self):
        """Nov 1 2026 is when US clocks fall back. 9 AM stays 9 AM."""
        # Start in summer (PDT), recurrence crosses into winter (PST)
        dtstart = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # Mon, PDT
        vevent = _make_vevent(
            dtstart=dtstart,
            dtend=dtstart + timedelta(hours=1),
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
        )
        # Window spans the DST transition (Nov 1 2026)
        start, end = _window(date(2026, 10, 5), date(2026, 11, 16))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        # All occurrences should show 09:00:00 local time
        for ev in events:
            assert ev["start"]["dateTime"].endswith("T09:00:00"), (
                f"Expected 09:00:00 but got {ev['start']['dateTime']}"
            )
            assert ev["end"]["dateTime"].endswith("T10:00:00")
        # Should have 6 Mondays: Oct 5, 12, 19, 26, Nov 2, 9 (maybe 16 depending on end)
        assert len(events) >= 6

    def test_spring_forward_keeps_local_time(self):
        """Mar 8 2026 is when US clocks spring forward. 9 AM stays 9 AM."""
        dtstart = datetime(2026, 2, 23, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # Mon, PST
        vevent = _make_vevent(
            dtstart=dtstart,
            dtend=dtstart + timedelta(hours=1),
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
        )
        start, end = _window(date(2026, 2, 23), date(2026, 3, 23))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        for ev in events:
            assert ev["start"]["dateTime"].endswith("T09:00:00"), (
                f"Expected 09:00:00 but got {ev['start']['dateTime']}"
            )
        assert len(events) >= 4

    def test_timezone_name_consistent_across_dst(self):
        """Timezone field should be IANA name regardless of DST state."""
        dtstart = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        vevent = _make_vevent(
            dtstart=dtstart,
            dtend=dtstart + timedelta(hours=1),
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
        )
        start, end = _window(date(2026, 10, 5), date(2026, 11, 16))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) >= 6
        for ev in events:
            assert ev["start"]["timeZone"] == "America/Los_Angeles"


# === Test: Edge cases ===

class TestEdgeCases:
    """Edge cases: no RRULE, no DTSTART, naive datetimes, empty window."""

    def test_no_rrule_returns_empty(self):
        """Component without RRULE returns empty list."""
        cal = Calendar()
        ev = Event()
        ev.add("UID", "no-rrule")
        ev.add("SUMMARY", "Single Event")
        ev.add("DTSTART", datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
        ev.add("DTEND", datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))
        cal.add_component(ev)
        for comp in cal.walk():
            if comp.name == "VEVENT":
                result = _expand_rrule_events(comp, USER_EMAIL, *_window(date(2026, 6, 1), date(2026, 6, 30)))
                assert result == []
                return

    def test_no_dtstart_returns_empty(self):
        """Component without DTSTART returns empty list."""
        cal = Calendar()
        ev = Event()
        ev.add("UID", "no-start")
        ev.add("SUMMARY", "Broken")
        ev.add("RRULE", {"FREQ": ["WEEKLY"]})
        cal.add_component(ev)
        for comp in cal.walk():
            if comp.name == "VEVENT":
                result = _expand_rrule_events(comp, USER_EMAIL, *_window(date(2026, 6, 1), date(2026, 6, 30)))
                assert result == []
                return

    def test_naive_dtstart_gets_utc(self):
        """Naive datetime is treated as UTC."""
        vevent = _make_vevent(
            dtstart=datetime(2026, 6, 1, 9, 0),  # naive
            dtend=datetime(2026, 6, 1, 10, 0),    # naive
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["MO"]},
        )
        start, end = _window(date(2026, 6, 1), date(2026, 6, 7))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        assert len(events) == 1
        # Should still produce valid output
        assert events[0]["start"]["dateTime"] == "2026-06-01T09:00:00"

    def test_multi_day_weekly_pattern(self):
        """TU,WE,TH pattern expands correctly."""
        dtstart = datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # Tuesday
        vevent = _make_vevent(
            dtstart=dtstart,
            dtend=dtstart + timedelta(minutes=15),
            rrule={"FREQ": ["WEEKLY"], "BYDAY": ["TU", "WE", "TH"]},
        )
        start, end = _window(date(2026, 6, 2), date(2026, 6, 8))
        events = _expand_rrule_events(vevent, USER_EMAIL, start, end)
        # Tue Jun 2, Wed Jun 3, Thu Jun 4
        assert len(events) == 3

    def test_empty_uid_returns_none_from_build(self):
        """_build_occurrence_dict returns None for empty UID."""
        cal = Calendar()
        ev = Event()
        ev.add("SUMMARY", "No UID")
        ev.add("DTSTART", datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
        cal.add_component(ev)
        for comp in cal.walk():
            if comp.name == "VEVENT":
                result = _build_occurrence_dict(
                    comp, USER_EMAIL,
                    datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                    datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
                    False,
                )
                assert result is None
                return
