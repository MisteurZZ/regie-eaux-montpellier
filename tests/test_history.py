"""Exercise chronology and historical totals without Home Assistant."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from json import loads
from pathlib import Path
from unittest import TestCase

from custom_components.regie_eaux_montpellier.history import (
    PARIS,
    InvalidReadings,
    decode_history,
    encode_history,
    hourly_totals,
    merge_readings,
    parse_readings,
    summarize,
)

FIXTURE = Path(__file__).parent / "fixtures" / "readings.json"


def example():
    return parse_readings(loads(FIXTURE.read_text(encoding="utf-8")), "TEST")


class HistoryTest(TestCase):
    def test_observed_example(self):
        readings = example()
        self.assertEqual([r.day.day for r in readings], [16, 17, 18, 19])
        self.assertEqual(readings[-1].index_litres / 1000, 484.838)
        self.assertIsNone(readings[0].volume_litres)
        self.assertEqual([r.volume_litres for r in readings[1:]], [429, 410, 533])
        snapshot = summarize(readings, date(2026, 11, 20))
        self.assertAlmostEqual(snapshot.average_litres, 457.333333333)
        self.assertEqual(snapshot.month_m3, 1.372)
        self.assertEqual(snapshot.year_m3, 1.372)
        self.assertEqual(
            readings[1].measured_at - readings[0].measured_at, timedelta(hours=18)
        )

    def test_initial_index_alone_does_not_generate_statistics(self):
        baseline = example()[:1]
        self.assertEqual(hourly_totals(baseline), [])
        self.assertIsNone(summarize(baseline, date(2026, 11, 20)).month_m3)

    def test_increment_is_assigned_to_meter_day(self):
        result = hourly_totals(example())
        self.assertEqual([p.total_litres for p in result], [0, 429, 429, 839, 839, 1372])
        self.assertEqual(
            [p.start.astimezone(PARIS).day for p in result], [17, 17, 18, 18, 19, 19]
        )
        self.assertEqual([p.start.astimezone(PARIS).hour for p in result], [11, 12] * 3)

    def test_rolling_response_and_restart_are_idempotent(self):
        readings = example()
        restored = decode_history(encode_history(readings))
        self.assertEqual(merge_readings(restored, readings[-2:]), readings)
        self.assertEqual(merge_readings(restored, ()), readings)
        self.assertEqual(hourly_totals(restored), hourly_totals(readings))

    def test_backfill_and_correction_recalculate_subsequent_totals(self):
        readings = example()
        missing = (readings[0], readings[1], readings[3])
        self.assertEqual(hourly_totals(missing)[-1].total_litres, 962)
        corrected = replace(readings[1], volume_litres=400)
        merged = merge_readings(missing, (corrected, readings[2]))
        self.assertEqual([p.total_litres for p in hourly_totals(merged)], [0, 400, 400, 810, 810, 1343])
        self.assertEqual(hourly_totals(missing)[-1].start, hourly_totals(merged)[-1].start)

    def test_volume_omitted_in_a_later_response_does_not_erase_history(self):
        readings = example()
        partial = replace(readings[-1], volume_litres=None)
        self.assertEqual(merge_readings(readings, (partial,)), readings)

    def test_no_synthetic_consumption_on_a_missing_day(self):
        readings = example()
        result = hourly_totals((readings[0], readings[1], readings[3]))
        self.assertEqual({p.start.astimezone(PARIS).day for p in result}, {17, 19})
        self.assertEqual(result[-1].total_litres, 962)

    def test_dst_fall_distinguishes_repeated_hours(self):
        rows = [
            {
                "dateReleve": f"2026-10-25T02:30:00{offset}",
                "numeroContratAbonnement": "TEST",
                "valeurIndex": 1000 + volume,
                "volumeConsoEnLitres": volume,
            }
            for offset, volume in [("+01:00", 20), ("+02:00", 10)]
        ]
        readings = parse_readings({"resultats": rows}, "TEST")
        points = hourly_totals(readings)
        self.assertEqual([p.total_litres for p in points], [0, 10, 30])
        self.assertEqual([p.start.hour for p in points], [23, 0, 1])
        self.assertEqual([p.start.astimezone(PARIS).fold for p in points[-2:]], [0, 1])
        self.assertEqual(summarize(readings, date(2026, 10, 25)).average_litres, 30)

    def test_dst_spring_and_paris_year_boundary(self):
        row = {"numeroContratAbonnement": "TEST", "valeurIndex": 1000, "volumeConsoEnLitres": 1}
        spring = parse_readings(
            {"resultats": [
                {**row, "dateReleve": "2026-03-28T12:00:00+01:00"},
                {**row, "dateReleve": "2026-03-29T12:00:00+02:00"},
            ]},
            "TEST",
        )
        self.assertEqual(spring[1].measured_at - spring[0].measured_at, timedelta(hours=23))
        self.assertEqual([s.day.day for s in spring], [28, 29])
        [new_year] = parse_readings(
            {"resultats": [{**row, "dateReleve": "2026-12-31T23:15:00Z"}]}, "TEST"
        )
        self.assertEqual(new_year.day, date(2027, 1, 1))
        self.assertEqual(summarize((new_year,), date(2027, 1, 1)).month_m3, 0.001)
        self.assertIsNone(summarize((new_year,), date(2026, 12, 31)).year_m3)

    def test_zero_and_m3_fallback_are_distinct_from_missing(self):
        payload = loads(FIXTURE.read_text(encoding="utf-8"))
        first, second, *_ = payload["resultats"]
        del first["volumeConsoEnLitres"]
        second["volumeConsoEnLitres"] = 0
        readings = parse_readings(payload, "TEST")
        self.assertEqual(readings[-1].volume_litres, 533)
        self.assertEqual(readings[-2].volume_litres, 0)

    def test_rejects_conflicting_duplicate_and_paginated_batch(self):
        payload = loads(FIXTURE.read_text(encoding="utf-8"))
        row = payload["resultats"][0]
        self.assertEqual(len(parse_readings({"resultats": [row, row]}, "TEST")), 1)
        with self.assertRaises(InvalidReadings):
            parse_readings({"resultats": [row, {**row, "valeurIndex": 123}]}, "TEST")
        with self.assertRaises(InvalidReadings):
            parse_readings({"resultats": [row], "nbTotalResultats": 2}, "TEST")

    def test_invalid_values_are_not_imported(self):
        row = loads(FIXTURE.read_text(encoding="utf-8"))["resultats"][0]
        for key, value in (
            ("dateReleve", datetime.now().isoformat()),
            ("numeroContratAbonnement", "OTHER"),
            ("valeurIndex", float("nan")),
            ("valeurIndex", 484838.25),
            ("volumeConsoEnLitres", -1),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(InvalidReadings):
                parse_readings({"resultats": [{**row, key: value}]}, "TEST")
