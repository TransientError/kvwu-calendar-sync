"""Tests for filter_events and related filtering logic."""

import pytest
from sync import filter_events, _is_solo_event


def _base_config(**overrides):
    """Build a minimal config dict for filtering."""
    sync = {
        "include_statuses": ["accepted"],
        "always_sync_subjects": ["on-call"],
        "skip_subjects": ["Drive", "Lunch"],
        "skip_subject_patterns": ["MCAPS Start"],
        "skip_solo_events": True,
    }
    sync.update(overrides)
    return {"sync": sync}


def _event(subject="Meeting", response="accepted", show_as="busy",
           is_cancelled=False, is_organizer=False, attendees=None):
    """Build a minimal event dict matching the Graph-like shape."""
    if attendees is None:
        attendees = [{"emailAddress": {"address": "someone@example.com"}}]
    return {
        "subject": subject,
        "responseStatus": {"response": response},
        "showAs": show_as,
        "isCancelled": is_cancelled,
        "isOrganizer": is_organizer,
        "attendees": attendees,
    }


# ─── Basic acceptance filtering ─────────────────────────────────────────────


class TestResponseStatusFilter:
    def test_accepted_passes(self):
        config = _base_config()
        events = [_event(response="accepted")]
        assert len(filter_events(events, config)) == 1

    def test_declined_filtered(self):
        config = _base_config()
        events = [_event(response="declined")]
        assert len(filter_events(events, config)) == 0

    def test_tentative_filtered(self):
        config = _base_config()
        events = [_event(response="tentativelyAccepted")]
        assert len(filter_events(events, config)) == 0

    def test_not_responded_filtered(self):
        config = _base_config()
        events = [_event(response="notResponded")]
        assert len(filter_events(events, config)) == 0

    def test_multiple_statuses_configured(self):
        config = _base_config(include_statuses=["accepted", "tentativelyAccepted"])
        events = [_event(response="tentativelyAccepted")]
        assert len(filter_events(events, config)) == 1


# ─── showAs / BUSYSTATUS filtering ──────────────────────────────────────────


class TestShowAsFilter:
    def test_busy_passes_when_configured(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(show_as="busy")]
        assert len(filter_events(events, config)) == 1

    def test_tentative_filtered_when_busy_only(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(show_as="tentative")]
        assert len(filter_events(events, config)) == 0

    def test_free_filtered_when_busy_only(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(show_as="free")]
        assert len(filter_events(events, config)) == 0

    def test_no_show_as_config_passes_all(self):
        config = _base_config()  # no include_show_as key
        events = [_event(show_as="free"), _event(show_as="tentative")]
        assert len(filter_events(events, config)) == 2

    def test_case_insensitive(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(show_as="BUSY")]
        assert len(filter_events(events, config)) == 1


# ─── always_sync_subjects (bypass) ──────────────────────────────────────────


class TestAlwaysSyncSubjects:
    def test_on_call_bypasses_status_filter(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="Primary on-call schedule", response="notResponded", show_as="tentative")]
        assert len(filter_events(events, config)) == 1

    def test_on_call_bypasses_show_as_filter(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="Backup on-call rotation", show_as="free")]
        assert len(filter_events(events, config)) == 1

    def test_substring_match(self):
        config = _base_config(always_sync_subjects=["standup"])
        events = [_event(subject="Prism Standup", response="notResponded", show_as="tentative")]
        result = filter_events(events, _base_config(
            always_sync_subjects=["standup"], include_show_as=["busy"]
        ))
        assert len(result) == 1

    def test_case_insensitive(self):
        config = _base_config(always_sync_subjects=["ON-CALL"], include_show_as=["busy"])
        events = [_event(subject="primary on-call schedule", show_as="tentative")]
        assert len(filter_events(events, config)) == 1


# ─── Cancelled events ───────────────────────────────────────────────────────


class TestCancelledEvents:
    def test_cancelled_filtered(self):
        config = _base_config()
        events = [_event(is_cancelled=True)]
        assert len(filter_events(events, config)) == 0

    def test_cancelled_filtered_even_with_always_sync(self):
        config = _base_config()
        events = [_event(subject="Primary on-call schedule", is_cancelled=True)]
        assert len(filter_events(events, config)) == 0


# ─── skip_subjects (exact match) ────────────────────────────────────────────


class TestSkipSubjects:
    def test_drive_skipped(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="Drive", show_as="busy")]
        assert len(filter_events(events, config)) == 0

    def test_lunch_skipped(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="Lunch", show_as="busy")]
        assert len(filter_events(events, config)) == 0

    def test_case_insensitive(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="drive", show_as="busy")]
        assert len(filter_events(events, config)) == 0

    def test_partial_match_does_not_skip(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="Drive to office meeting", show_as="busy")]
        assert len(filter_events(events, config)) == 1

    def test_skip_subjects_override_always_sync(self):
        """skip_subjects runs before always_sync check."""
        config = _base_config(
            skip_subjects=["on-call lunch"],
            always_sync_subjects=["on-call"],
            include_show_as=["busy"],
        )
        events = [_event(subject="on-call lunch", show_as="free")]
        assert len(filter_events(events, config)) == 0


# ─── skip_subject_patterns (substring match) ────────────────────────────────


class TestSkipSubjectPatterns:
    def test_pattern_skipped(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="MCAPS Start: Day 1 keynotes", show_as="busy")]
        assert len(filter_events(events, config)) == 0

    def test_case_insensitive(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="mcaps start: day 2", show_as="busy")]
        assert len(filter_events(events, config)) == 0

    def test_non_matching_passes(self):
        config = _base_config(include_show_as=["busy"])
        events = [_event(subject="MCAPS Wrap-up", show_as="busy")]
        assert len(filter_events(events, config)) == 1

    def test_pattern_overrides_always_sync(self):
        """skip_subject_patterns runs before always_sync check."""
        config = _base_config(
            skip_subject_patterns=["canceled"],
            always_sync_subjects=["standup"],
            include_show_as=["busy"],
        )
        events = [_event(subject="Canceled: Prism Standup", show_as="free")]
        assert len(filter_events(events, config)) == 0


# ─── Solo events (Graph API mode only — ICS has no organizer data) ───────────


class TestSoloEvents:
    """Solo detection only works in Graph API mode.
    In ICS mode, use skip_subjects for personal blocks instead."""

    def test_solo_event_with_skip_disabled(self):
        config = _base_config(skip_solo_events=False)
        events = [_event(is_organizer=True, attendees=[])]
        assert len(filter_events(events, config)) == 1

    def test_organizer_with_attendees_not_solo(self):
        config = _base_config()
        events = [_event(is_organizer=True, attendees=[{"emailAddress": {"address": "a@b.com"}}])]
        assert len(filter_events(events, config)) == 1


# ─── _is_solo_event helper ──────────────────────────────────────────────────


class TestIsSoloEvent:
    def test_organizer_no_attendees(self):
        assert _is_solo_event({"isOrganizer": True, "attendees": []}) is True

    def test_organizer_with_attendees(self):
        assert _is_solo_event({"isOrganizer": True, "attendees": [{"emailAddress": {}}]}) is False

    def test_not_organizer(self):
        assert _is_solo_event({"isOrganizer": False, "attendees": []}) is False

    def test_missing_organizer_field(self):
        assert _is_solo_event({"attendees": []}) is False


# ─── Edge cases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_event_list(self):
        config = _base_config()
        assert filter_events([], config) == []

    def test_missing_subject(self):
        config = _base_config()
        event = _event()
        del event["subject"]
        result = filter_events([event], config)
        assert len(result) == 1

    def test_missing_response_status(self):
        config = _base_config()
        event = _event()
        del event["responseStatus"]
        result = filter_events([event], config)
        assert len(result) == 0  # defaults to "none", not in include_statuses

    def test_missing_show_as(self):
        config = _base_config(include_show_as=["busy"])
        event = _event()
        del event["showAs"]
        result = filter_events([event], config)
        assert len(result) == 0  # empty string not in include_show_as


# ─── ICS-mode realistic scenarios ───────────────────────────────────────────


class TestICSModeScenarios:
    """Tests simulating real ICS feed data where attendees are absent
    and showAs (BUSYSTATUS) is the primary acceptance signal."""

    def _ics_event(self, subject="Team Meeting", show_as="busy", is_cancelled=False):
        """ICS events have no attendees, no organizer, response defaults to accepted."""
        return {
            "subject": subject,
            "responseStatus": {"response": "accepted"},  # ICS adapter default
            "showAs": show_as,
            "isCancelled": is_cancelled,
            "isOrganizer": False,  # ICS has no organizer data
            "attendees": [],  # ICS strips attendees
        }

    def test_busy_event_passes(self):
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(show_as="busy")]
        assert len(filter_events(events, config)) == 1

    def test_tentative_event_filtered(self):
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(show_as="tentative")]
        assert len(filter_events(events, config)) == 0

    def test_free_event_filtered(self):
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(show_as="free")]
        assert len(filter_events(events, config)) == 0

    def test_on_call_tentative_bypasses(self):
        """On-call events are TENTATIVE in ICS but should always sync."""
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(subject="Primary on-call schedule", show_as="tentative")]
        assert len(filter_events(events, config)) == 1

    def test_cancelled_prefix_in_subject(self):
        """ICS events with 'Canceled:' subject are marked isCancelled by adapter."""
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(subject="Canceled: Prism Standup", is_cancelled=True)]
        assert len(filter_events(events, config)) == 0

    def test_solo_detection_noop_in_ics(self):
        """ICS events have isOrganizer=False, so solo detection never triggers."""
        config = _base_config(include_show_as=["busy"])
        events = [self._ics_event(show_as="busy")]
        # attendees=[] but isOrganizer=False → not solo
        assert len(filter_events(events, config)) == 1

    def test_mixed_ics_feed(self):
        """Simulate a typical ICS feed with various BUSYSTATUS values."""
        config = _base_config(include_show_as=["busy"])
        events = [
            self._ics_event(subject="Accepted Meeting", show_as="busy"),
            self._ics_event(subject="Tentative Meeting", show_as="tentative"),
            self._ics_event(subject="Declined Meeting", show_as="free"),
            self._ics_event(subject="Backup on-call rotation", show_as="tentative"),
            self._ics_event(subject="Drive", show_as="busy"),
            self._ics_event(subject="MCAPS Start: Day 1", show_as="busy"),
        ]
        result = filter_events(events, config)
        subjects = [e["subject"] for e in result]
        assert "Accepted Meeting" in subjects
        assert "Backup on-call rotation" in subjects
        assert "Tentative Meeting" not in subjects
        assert "Declined Meeting" not in subjects
        assert "Drive" not in subjects
        assert "MCAPS Start: Day 1" not in subjects


# ─── Pattern edge cases ─────────────────────────────────────────────────────


class TestPatternEdgeCases:
    def test_empty_always_sync_pattern_not_universal_match(self):
        """Empty string in always_sync_subjects would match everything — guard against it."""
        config = _base_config(always_sync_subjects=["", "on-call"], include_show_as=["busy"])
        events = [_event(subject="Random meeting", show_as="free")]
        # Empty string IS a substring of everything — this documents the foot-gun
        result = filter_events(events, config)
        assert len(result) == 1  # Bug: empty pattern bypasses all filters

    def test_empty_skip_pattern_skips_everything(self):
        """Empty string in skip_subject_patterns matches everything."""
        config = _base_config(skip_subject_patterns=[""], include_show_as=["busy"])
        events = [_event(subject="Important meeting", show_as="busy")]
        result = filter_events(events, config)
        assert len(result) == 0  # Bug: everything gets skipped

    def test_whitespace_in_subject_exact_match(self):
        """Trailing whitespace in subject won't match exact skip list."""
        config = _base_config(skip_subjects=["drive"], include_show_as=["busy"])
        events = [_event(subject="Drive ", show_as="busy")]
        # "drive " != "drive" — trailing space prevents exact match
        assert len(filter_events(events, config)) == 1
