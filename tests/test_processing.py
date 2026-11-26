"""Tests of reading cleanup and statistics building."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import processing as p

RawReading = p.RawReading
Reading = p.Reading


def raw(text: str, value: float, status: int = 8) -> RawReading:
    return RawReading(local_time=datetime.fromisoformat(text), value=value, status=status)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_to_utc_winter_and_summer():
    assert p.to_utc(datetime(2026, 11, 24, 1, 0)) == utc("2026-11-24T00:00:00")
    assert p.to_utc(datetime(2026, 9, 12, 1, 0)) == utc("2026-09-11T23:00:00")


def test_bogus_installation_points_are_dropped():
    # As seen on the live account: 0.014 m³ (S=170), then 467.196 (S=138).
    readings = p.clean_readings(
        [
            raw("2026-09-11T10:00:00", 0.014, 170),
            raw("2026-09-11T11:00:00", 467.196, 138),
            raw("2026-09-11T12:00:00", 467.2),
            raw("2026-09-11T13:00:00", 467.21),
        ]
    )
    assert [r.value for r in readings] == [467.2, 467.21]


def test_flagged_point_after_a_good_one_is_kept():
    # The merged DST hour has S=168 but a valid value.
    readings = p.clean_readings(
        [
            raw("2026-10-25T01:00:00", 479.109),
            raw("2026-10-25T02:00:00", 479.109, 168),
            raw("2026-10-25T03:00:00", 479.109),
        ]
    )
    assert len(readings) == 3
    # 02:00 is read as the first (CEST) occurrence: 00:00 UTC.
    assert readings[1].time == utc("2026-10-25T00:00:00")
    assert readings[2].time == utc("2026-10-25T02:00:00")


def test_spring_forward_duplicate_is_merged():
    # 02:00 does not exist on 2027-03-28; it maps onto the same UTC hour as 03:00.
    readings = p.clean_readings(
        [
            raw("2027-03-28T01:00:00", 500.0),
            raw("2027-03-28T02:00:00", 500.1),
            raw("2027-03-28T03:00:00", 500.2),
        ]
    )
    times = [r.time for r in readings]
    assert len(times) == len(set(times))
    assert readings[-1].value == 500.2


def test_drops_and_glitches_are_ignored():
    readings = p.clean_readings(
        [
            raw("2026-11-24T00:00:00", 486.742),
            raw("2026-11-24T01:00:00", 486.0),  # goes down
            raw("2026-11-24T02:00:00", 999.0),  # impossible jump
            raw("2026-11-24T03:00:00", 486.75),
        ]
    )
    assert [r.value for r in readings] == [486.742, 486.75]


def test_long_gap_is_not_a_glitch():
    readings = p.clean_readings(
        [raw("2026-11-01T00:00:00", 480.0), raw("2026-11-11T00:00:00", 485.0)]
    )
    assert len(readings) == 2


def test_overlapping_windows_and_previous():
    previous = Reading(time=utc("2026-11-24T00:00:00"), value=486.742)
    readings = p.clean_readings(
        [
            raw("2026-11-24T01:00:00", 486.742),  # == previous time (UTC 00:00)
            raw("2026-11-24T02:00:00", 486.748),
            raw("2026-11-24T02:00:00", 486.748),
        ],
        previous,
    )
    assert [r.time for r in readings] == [utc("2026-11-24T01:00:00")]


def test_build_statistics_first_import():
    readings = [
        Reading(utc("2026-11-24T00:00:00"), 486.742),
        Reading(utc("2026-11-24T01:00:00"), 486.748),
        Reading(utc("2026-11-24T03:00:00"), 486.760),  # gap at 02:00
    ]
    stats = p.build_statistics(readings, None, None, None)
    assert [s.start for s in stats] == [
        utc("2026-11-23T23:00:00"),
        utc("2026-11-24T00:00:00"),
        utc("2026-11-24T02:00:00"),
    ]
    assert [s.sum for s in stats] == [0.0, 0.006, 0.018]
    assert stats[-1].state == 486.760


def test_build_statistics_continues_stored_sum():
    readings = [
        Reading(utc("2026-11-24T00:00:00"), 486.742),  # already stored
        Reading(utc("2026-11-24T01:00:00"), 486.748),
    ]
    stats = p.build_statistics(
        readings,
        last_start=utc("2026-11-23T23:00:00"),
        last_state=486.742,
        last_sum=19.5,
    )
    assert len(stats) == 1
    assert stats[0].start == utc("2026-11-24T00:00:00")
    assert stats[0].sum == 19.506


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=p.PORTAL_TZ).astimezone(UTC)


def test_consumption_yesterday():
    readings = [
        Reading(local(2026, 11, 24, 0), 486.478),
        Reading(local(2026, 11, 24, 12), 486.6),
        Reading(local(2026, 11, 25, 0), 486.742),
        Reading(local(2026, 11, 25, 5), 486.747),
    ]
    now = datetime(2026, 11, 25, 10, 16, tzinfo=p.PORTAL_TZ)
    assert p.consumption_yesterday(readings, now) == 0.264


def test_consumption_yesterday_without_history():
    readings = [Reading(local(2026, 11, 25, 5), 486.747)]
    now = datetime(2026, 11, 25, 10, tzinfo=p.PORTAL_TZ)
    assert p.consumption_yesterday(readings, now) is None
    assert p.consumption_yesterday([], now) is None


def test_consumption_yesterday_across_dst():
    # 2026-10-25 has 25 hours; "yesterday" on the 26th starts at local midnight.
    readings = [
        Reading(local(2026, 10, 25, 0), 479.0),
        Reading(local(2026, 10, 25, 23), 479.1),
        Reading(local(2026, 10, 26, 0), 479.3),
    ]
    now = datetime(2026, 10, 26, 8, tzinfo=p.PORTAL_TZ)
    assert p.consumption_yesterday(readings, now) == 0.3


def test_consumption_yesterday_waits_for_the_morning_batch():
    readings = [
        Reading(local(2026, 11, 24, 0), 486.478),
        Reading(local(2026, 11, 25, 0), 486.742),
    ]
    # 03:00 on the 26th: the batch with the 25th's evening and midnight has not arrived.
    assert p.consumption_yesterday(readings, datetime(2026, 11, 26, 3, tzinfo=p.PORTAL_TZ)) is None


def test_consumption_last_24h():
    # One batch a day: the window is 05:00 to 05:00, whatever the time now.
    readings = [
        Reading(local(2026, 11, 25, 4) + timedelta(hours=h), 487.0 + h / 100) for h in range(26)
    ]
    assert p.consumption_last_24h(readings) == 0.24


def test_consumption_last_24h_too_short_or_gap():
    readings = [Reading(local(2026, 11, 26, h), 487.0 + h / 100) for h in range(6)]
    assert p.consumption_last_24h(readings) is None
    assert p.consumption_last_24h([]) is None
    # Nearest earlier reading 30 h back: the window would be too long.
    gap = [Reading(local(2026, 11, 24, 23), 486.0), Reading(local(2026, 11, 26, 5), 487.0)]
    assert p.consumption_last_24h(gap) is None


def test_consumption_last_24h_across_dst():
    # The portal merges the two 02:00 hours of 2026-10-25, so the reading at
    # 01:00 UTC (the second 02:00 local) is missing. When it is the window's
    # base, the reading an hour earlier is used.
    readings = [
        Reading(utc("2026-10-25T00:00:00"), 478.0),  # first 02:00 local
        Reading(utc("2026-10-25T02:00:00"), 478.1),  # 03:00 local (CET)
        Reading(utc("2026-10-26T01:00:00"), 478.6),
    ]
    assert p.consumption_last_24h(readings) == 0.6


def test_build_statistics_without_stored_state():
    readings = [
        Reading(utc("2026-11-24T01:00:00"), 486.748),
        Reading(utc("2026-11-24T02:00:00"), 486.758),
    ]
    stats = p.build_statistics(readings, utc("2026-11-23T23:00:00"), None, 19.5)
    assert [(s.start, s.sum) for s in stats] == [(utc("2026-11-24T01:00:00"), 19.51)]


def test_dropped_readings_are_logged_once(caplog):
    caplog.set_level("DEBUG")
    raws = [raw("2026-11-24T00:00:00", 486.0)] + [
        raw(f"2026-11-24T{h:02d}:00:00", 0.5 + h / 100) for h in range(1, 10)
    ]
    p.clean_readings(raws)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Ignored 9" in warnings[0].getMessage()
    caplog.clear()
    p.clean_readings(raws, log_dropped=False)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
