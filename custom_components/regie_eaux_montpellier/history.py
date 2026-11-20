"""Parse and aggregate Montpellier meter readings in whole litres."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc


class InvalidReadings(ValueError):
    """The response is not a recognised batch of meter readings."""


@dataclass(frozen=True)
class Reading:
    """A meter index and an optional volume for the interval ending here."""

    measured_at: datetime
    index_litres: int
    volume_litres: int | None

    @property
    def day(self) -> date:
        """Return the date at the meter."""
        return self.measured_at.astimezone(PARIS).date()


def _whole_litres(value: object, scale: int = 1) -> int:
    if type(value) not in (str, int, float, Decimal):
        raise InvalidReadings("Unexpected numeric field")
    try:
        number = Decimal(str(value)) * scale
    except DecimalException:
        raise InvalidReadings("Invalid numeric field") from None
    if not number.is_finite() or not 0 <= number <= 10**15:
        raise InvalidReadings("Invalid numeric field")
    if number != number.to_integral_value():
        raise InvalidReadings("Expected whole litres")
    return int(number)


def _measurement_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidReadings("Missing reading date")
    try:
        iso_date = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(iso_date)
    except ValueError:
        raise InvalidReadings("Invalid reading date") from None
    if parsed.utcoffset() is None:
        raise InvalidReadings("Reading date has no UTC offset")
    return parsed.astimezone(UTC)


def _volume(row: dict) -> int | None:
    litres = row.get("volumeConsoEnLitres")
    cubic_metres = row.get("volumeConsoEnM3")
    if litres is not None:
        return _whole_litres(litres)
    if cubic_metres is not None:
        return _whole_litres(cubic_metres, scale=1000)
    return None


def parse_readings(payload: object, contract: str) -> tuple[Reading, ...]:
    """Validate and normalise the portal response, rejecting mixed contracts."""
    if not isinstance(payload, dict) or not isinstance(rows := payload.get("resultats"), list):
        raise InvalidReadings("Missing results list")
    count = payload.get("nbTotalResultats", len(rows))
    if type(count) is not int or count < len(rows):
        raise InvalidReadings("Invalid results count")
    if count > len(rows):
        raise InvalidReadings("Results are paginated")

    by_time: dict[datetime, Reading] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("numeroContratAbonnement")) != contract:
            raise InvalidReadings("Unexpected contract in results")
        instant = _measurement_time(row.get("dateReleve"))
        reading = Reading(instant, _whole_litres(row.get("valeurIndex")), _volume(row))
        if instant in by_time and by_time[instant] != reading:
            raise InvalidReadings("Conflicting readings at the same time")
        by_time[instant] = reading

    return tuple(by_time[instant] for instant in sorted(by_time))


def merge_readings(
    known: tuple[Reading, ...], returned: tuple[Reading, ...]
) -> tuple[Reading, ...]:
    """Merge rolling readings without discarding previously reported volumes."""
    by_time = {reading.measured_at: reading for reading in known}
    for reading in returned:
        prior = by_time.get(reading.measured_at)
        if prior is not None and reading.volume_litres is None:
            reading = replace(reading, volume_litres=prior.volume_litres)
        by_time[reading.measured_at] = reading
    return tuple(by_time[instant] for instant in sorted(by_time))


def encode_history(readings: tuple[Reading, ...]) -> dict:
    """Serialise only the data required to rebuild historical statistics."""
    return {
        "readings": [
            [r.measured_at.isoformat(), r.index_litres, r.volume_litres] for r in readings
        ]
    }


def decode_history(data: dict) -> tuple[Reading, ...]:
    """Restore stored readings with canonical UTC timestamps."""
    return tuple(
        Reading(_measurement_time(instant), index, volume)
        for instant, index, volume in data["readings"]
    )


@dataclass(frozen=True)
class Summary:
    """Volumes grouped by the Paris dates on which readings were measured."""

    average_litres: float | None
    month_m3: float | None
    year_m3: float | None


def summarize(readings: tuple[Reading, ...], today: date) -> Summary:
    """Calculate totals and a mean per day with a published volume."""
    by_day: dict[date, int] = defaultdict(int)
    for reading in readings:
        if reading.volume_litres is not None:
            by_day[reading.day] += reading.volume_litres

    monthly = [
        litres
        for day, litres in by_day.items()
        if day.year == today.year and day.month == today.month
    ]
    yearly = [litres for day, litres in by_day.items() if day.year == today.year]
    return Summary(
        sum(by_day.values()) / len(by_day) if by_day else None,
        sum(monthly) / 1000 if monthly else None,
        sum(yearly) / 1000 if yearly else None,
    )


@dataclass(frozen=True)
class HourlyTotal:
    """An external-statistics point with a cumulative consumption in litres."""

    start: datetime
    total_litres: int


def hourly_totals(readings: tuple[Reading, ...]) -> list[HourlyTotal]:
    """Put each reported interval at its measurement hour, seeding the prior hour."""
    by_hour: dict[datetime, int] = defaultdict(int)
    for reading in readings:
        if reading.volume_litres is None:
            continue
        hour = reading.measured_at.replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(hour - timedelta(hours=1), 0)
        by_hour[hour] += reading.volume_litres

    result = []
    total = 0
    for hour in sorted(by_hour):
        total += by_hour[hour]
        result.append(HourlyTotal(hour, total))
    return result
