"""Exercise the catch-up refresh decisions without Home Assistant."""

from datetime import date, datetime, time, timezone
from unittest import TestCase

from custom_components.regie_eaux_montpellier.history import PARIS, Reading
from custom_components.regie_eaux_montpellier.schedule import (
    CATCH_UP_TIMES,
    next_catch_up,
    reading_overdue,
)


def reading_on(day: int, month: int = 11) -> Reading:
    """A midday reading in Paris, as published by the portal."""
    return Reading(datetime(2026, month, day, 12, tzinfo=PARIS), 450_000, 150)


class OverdueTest(TestCase):
    def test_previous_day_reading_is_on_time(self):
        self.assertFalse(reading_overdue(reading_on(27), date(2026, 11, 28)))

    def test_same_day_reading_is_on_time(self):
        self.assertFalse(reading_overdue(reading_on(28), date(2026, 11, 28)))

    def test_older_reading_is_overdue(self):
        self.assertTrue(reading_overdue(reading_on(26), date(2026, 11, 28)))

    def test_no_reading_is_overdue(self):
        self.assertTrue(reading_overdue(None, date(2026, 11, 28)))

    def test_day_follows_paris_time(self):
        # 23:30 UTC on the 26th is already the 27th in Paris.
        late = Reading(datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc), 1, 1)
        self.assertFalse(reading_overdue(late, date(2026, 11, 28)))


class NextCatchUpTest(TestCase):
    def test_first_slot_after_the_morning_refresh(self):
        now = datetime(2026, 11, 28, 5, 15, tzinfo=PARIS)
        self.assertEqual(next_catch_up(now), datetime(2026, 11, 28, 13, 15, tzinfo=PARIS))

    def test_second_slot_after_the_first(self):
        now = datetime(2026, 11, 28, 13, 15, tzinfo=PARIS)
        self.assertEqual(next_catch_up(now), datetime(2026, 11, 28, 19, 15, tzinfo=PARIS))

    def test_no_slot_left_in_the_evening(self):
        self.assertIsNone(next_catch_up(datetime(2026, 11, 28, 19, 15, tzinfo=PARIS)))

    def test_accepts_utc_and_keeps_paris_slots_across_dst(self):
        # 29 March 2026: Paris switches to UTC+2, 13:15 local is 11:15 UTC.
        now = datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc)
        slot = next_catch_up(now)
        self.assertEqual(slot.astimezone(timezone.utc), datetime(2026, 3, 29, 11, 15, tzinfo=timezone.utc))

    def test_slots_are_ordered(self):
        now = datetime(2026, 11, 28, 6, tzinfo=PARIS)
        self.assertEqual(
            next_catch_up(now, (time(20), time(7))), datetime(2026, 11, 28, 7, tzinfo=PARIS)
        )

    def test_default_slots_follow_the_daily_refresh(self):
        self.assertTrue(all(slot > time(5, 15) for slot in CATCH_UP_TIMES))
