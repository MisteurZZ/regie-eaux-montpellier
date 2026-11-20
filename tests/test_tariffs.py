"""Verify dated tariffs and estimated costs without Home Assistant."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from json import loads
from pathlib import Path
from unittest import TestCase

from custom_components.regie_eaux_montpellier.history import PARIS, Reading, merge_readings, parse_readings
from custom_components.regie_eaux_montpellier.tariffs import (
    DEFAULT_PRICE_PER_M3,
    InvalidPrice,
    RetroactiveTariff,
    Tariff,
    decode_tariffs,
    encode_tariffs,
    estimate_costs,
    parse_price,
    set_estimated_tariff,
)

FIXTURE = Path(__file__).parent / "fixtures" / "readings.json"


class TariffTest(TestCase):
    def setUp(self):
        self.readings = parse_readings(loads(FIXTURE.read_text(encoding="utf-8")), "TEST")
        self.initial = Tariff(date(2026, 11, 17), DEFAULT_PRICE_PER_M3)

    def test_default_prices_only_the_actual_volume(self):
        points = estimate_costs(self.readings, (self.initial,))
        self.assertEqual(
            [p.total_eur for p in points],
            list(map(Decimal, ["0", "1.5477462", "1.5477462", "3.0269442", "3.0269442", "4.9499016"])),
        )
        self.assertEqual([p.start.astimezone(PARIS).day for p in points], [17, 17, 18, 18, 19, 19])

    def test_initial_effective_date_limits_the_backfill(self):
        tariff = replace(self.initial, effective_from=date(2026, 11, 19))
        points = estimate_costs(self.readings, (tariff,))
        self.assertEqual([p.start.astimezone(PARIS).day for p in points], [19, 19])
        self.assertEqual([p.total_eur for p in points], [Decimal(0), Decimal("1.9229574")])
        self.assertEqual(estimate_costs(self.readings, ()), [])

    def test_late_and_corrected_readings_keep_the_original_tariff(self):
        first, second, last = self.readings[1:]
        future = Tariff(last.day, Decimal("4"))
        schedule = (self.initial, future)
        initial = (first, last)
        with_gap = estimate_costs(initial, schedule)
        self.assertEqual(with_gap[-1].total_eur, Decimal("3.6797462"))
        updated = merge_readings(initial, (replace(first, volume_litres=430), second))
        corrected = estimate_costs(updated, schedule)
        self.assertEqual(corrected[-1].total_eur, Decimal("5.162552"))
        self.assertEqual(corrected[-1].start, with_gap[-1].start)
        self.assertEqual(estimate_costs(merge_readings(updated, updated), schedule), corrected)

    def test_price_change_starts_on_the_paris_date_not_the_utc_date(self):
        readings = (
            Reading(datetime(2026, 10, 24, 21, 30, tzinfo=timezone.utc), 1000, 1000),
            Reading(datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc), 2000, 1000),
        )
        tariffs = (Tariff(date(2026, 10, 24), Decimal(3)), Tariff(date(2026, 10, 25), Decimal(4)))
        points = estimate_costs(readings, tariffs)
        self.assertEqual([point.total_eur for point in points], [Decimal(0), Decimal(3), Decimal(7)])
        self.assertEqual(points[-1].start.astimezone(PARIS).date(), date(2026, 10, 25))

    def test_repeated_paris_hour_preserves_both_cost_intervals(self):
        readings = (
            Reading(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), 100, 100),
            Reading(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), 300, 200),
        )
        points = estimate_costs(readings, (Tariff(date(2026, 10, 25), Decimal(4)),))
        self.assertEqual([point.total_eur for point in points], list(map(Decimal, ["0", "0.4", "1.2"])))
        self.assertEqual([point.start.astimezone(PARIS).hour for point in points], [1, 2, 2])
        self.assertNotEqual(points[1].start, points[2].start)

    def test_price_changes_preserve_past_rates_and_persist_exactly(self):
        today = date(2026, 11, 20)
        tariffs = set_estimated_tariff((), self.initial, today)
        for day in (date(2026, 11, 18), today):
            with self.subTest(day=day), self.assertRaises(RetroactiveTariff):
                set_estimated_tariff(tariffs, Tariff(day, Decimal("4.1234")), today)
        tomorrow = Tariff(date(2026, 11, 21), Decimal("4.1234"))
        updated = set_estimated_tariff(tariffs, tomorrow, today)
        self.assertEqual(updated, (self.initial, tomorrow))
        self.assertEqual(decode_tariffs(encode_tariffs(updated)), updated)
        self.assertEqual(estimate_costs(self.readings, updated), estimate_costs(self.readings, tariffs))

    def test_future_tariff_can_be_updated_before_it_takes_effect(self):
        today = date(2026, 11, 20)
        future = Tariff(date(2026, 11, 21), Decimal(4))
        tariffs = (self.initial, future)
        amended = replace(future, price_per_m3=Decimal("4.25"))
        self.assertEqual(set_estimated_tariff(tariffs, amended, today), (self.initial, amended))
        self.assertEqual(set_estimated_tariff(tariffs, future, today), tariffs)

    def test_invalid_prices_are_rejected_and_zero_is_supported(self):
        for value in (-1, True, None, "3.60781", float("inf"), float("nan"), "1e999"):
            with self.subTest(value=value), self.assertRaises(InvalidPrice):
                parse_price(value)
        self.assertEqual(parse_price(3.6078), Decimal("3.6078"))
        self.assertEqual(parse_price(0), Decimal(0))
        free = replace(self.initial, price_per_m3=Decimal(0))
        self.assertEqual({p.total_eur for p in estimate_costs(self.readings, (free,))}, {Decimal(0)})
