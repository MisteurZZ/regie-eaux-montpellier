"""Dated price schedules and estimated water costs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException

from .history import PARIS, Reading, hourly_totals

DEFAULT_PRICE_PER_M3 = Decimal("3.6078")


class InvalidPrice(ValueError):
    """The proposed tariff is not a non-negative price to four decimal places."""


class RetroactiveTariff(ValueError):
    """A change to an existing price schedule must take effect in the future."""


@dataclass(frozen=True)
class Tariff:
    """An estimated TTC price in EUR per cubic metre, starting on a Paris date."""

    effective_from: date
    price_per_m3: Decimal


@dataclass(frozen=True)
class HourlyCost:
    """A cumulative cost point in euros at a Recorder hour."""

    start: datetime
    total_eur: Decimal


def parse_price(value: object) -> Decimal:
    """Keep price arithmetic in decimal euros and validate user input."""
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise InvalidPrice
    try:
        price = Decimal(str(value))
        if price.is_finite() and price >= 0 and price == price.quantize(Decimal("0.0001")):
            return price
    except DecimalException:
        pass
    raise InvalidPrice


def decode_tariffs(data: list[dict[str, str]]) -> tuple[Tariff, ...]:
    """Restore the effective dates and lossless decimal prices from options."""
    return tuple(
        Tariff(date.fromisoformat(row["effective_from"]), parse_price(row["price_per_m3"]))
        for row in data
    )


def encode_tariffs(tariffs: tuple[Tariff, ...]) -> list[dict[str, str]]:
    """Serialise the schedule without rounding prices through JSON floats."""
    return [
        {"effective_from": tariff.effective_from.isoformat(), "price_per_m3": str(tariff.price_per_m3)}
        for tariff in tariffs
    ]


def set_estimated_tariff(
    tariffs: tuple[Tariff, ...], tariff: Tariff, today: date
) -> tuple[Tariff, ...]:
    """Record an initial estimate or schedule a future change."""
    if tariffs:
        if tariff.price_per_m3 == tariffs[-1].price_per_m3:
            return tariffs
        if tariff.effective_from <= today:
            raise RetroactiveTariff

    schedule = {entry.effective_from: entry for entry in tariffs}
    schedule[tariff.effective_from] = tariff
    return tuple(schedule[day] for day in sorted(schedule))


def estimate_costs(
    readings: tuple[Reading, ...], tariffs: tuple[Tariff, ...]
) -> list[HourlyCost]:
    """Price each measured interval at its applicable tariff and accumulate EUR."""
    if not tariffs:
        return []

    points = hourly_totals(
        tuple(reading for reading in readings if reading.day >= tariffs[0].effective_from)
    )
    result = []
    tariff_index = 0
    previous_litres = 0
    total = Decimal(0)
    for point in points:
        day = point.start.astimezone(PARIS).date()
        while (
            tariff_index + 1 < len(tariffs)
            and tariffs[tariff_index + 1].effective_from <= day
        ):
            tariff_index += 1

        interval_litres = point.total_litres - previous_litres
        total += tariffs[tariff_index].price_per_m3 * interval_litres / 1000
        result.append(HourlyCost(point.start, total))
        previous_litres = point.total_litres

    return result
