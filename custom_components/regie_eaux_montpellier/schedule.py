"""Decide when to look again for a reading the portal has not published yet."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from .history import PARIS, Reading

# Readings are measured around midday and are usually published by the next
# morning. When the 05:15 refresh finds nothing for the previous day, look again
# later the same day instead of waiting another 24 hours.
CATCH_UP_TIMES = (time(13, 15), time(19, 15))


def reading_overdue(latest: Reading | None, today: date) -> bool:
    """Return whether the reading measured on the previous Paris day is missing."""
    return latest is None or latest.day < today - timedelta(days=1)


def next_catch_up(now: datetime, slots: Sequence[time] = CATCH_UP_TIMES) -> datetime | None:
    """Return the next catch-up slot later today in Paris time, if any is left."""
    local = now.astimezone(PARIS)
    for slot in sorted(slots):
        candidate = datetime.combine(local.date(), slot, PARIS)
        if candidate > local:
            return candidate
    return None
