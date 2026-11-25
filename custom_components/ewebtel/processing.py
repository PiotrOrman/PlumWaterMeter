"""Turn raw eWebtel readings into clean hourly readings and statistics.

Facts about the portal data this module relies on (checked on a live
account, November 2026):

* The hourly series with ``CalculationMethod = 0`` holds the absolute meter
  reading in m³. ``D`` is the moment of the reading in portal local time
  (Europe/Warsaw) without an offset; the reading at ``D`` closes the hour
  that started at ``D - 1 h``.
* Almost every point has status ``S = 8``. Other values flag special points:
  the first points after installation (``170``, ``138``; the very first one
  held a bogus 0.014 m³) and the repeated hour of the October DST change
  (``168``; the portal merges both 02:00 hours into one point).
* Gaps are possible ("data discontinuity" in the portal). Because the
  reading is absolute, a gap only spreads its consumption over the next
  hour; the total stays right.

This module does not import Home Assistant, so it can be tested on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

PORTAL_TZ = ZoneInfo("Europe/Warsaw")
STATUS_OK = 8
# A household meter cannot pass more than this per hour. A flagged point
# that would need more than this to connect to the previous reading is
# treated as bogus.
MAX_PLAUSIBLE_FLOW_M3_PER_HOUR = 10.0


@dataclass(frozen=True, slots=True)
class RawReading:
    """A point as the portal returns it."""

    local_time: datetime
    value: float
    status: int


@dataclass(frozen=True, slots=True)
class Reading:
    """A validated meter reading at a UTC moment."""

    time: datetime
    value: float


@dataclass(frozen=True, slots=True)
class HourlyStatistic:
    """One hour of long-term statistics (start, reading at its end, total)."""

    start: datetime
    state: float
    sum: float


def to_utc(local_time: datetime, tz: tzinfo = PORTAL_TZ) -> datetime:
    """Convert a naive portal time to UTC (first occurrence on DST overlap)."""
    if local_time.tzinfo is not None:
        return local_time.astimezone(UTC)
    return local_time.replace(tzinfo=tz, fold=0).astimezone(UTC)


def clean_readings(
    raw: list[RawReading],
    previous: Reading | None = None,
    tz: tzinfo = PORTAL_TZ,
    *,
    log_dropped: bool = True,
) -> list[Reading]:
    """Return sorted, de-duplicated, plausible readings after ``previous``.

    * Points are converted to UTC and sorted; on equal times the last one wins.
    * Points at or before ``previous.time`` are dropped.
    * A reading lower than the one before it is dropped (the meter only
      counts up; a drop is a glitch, not a refund).
    * A point that would need more than ``MAX_PLAUSIBLE_FLOW_M3_PER_HOUR``
      to connect to the previous reading is dropped as a glitch.
    * A point with a non-OK status and no earlier reading is dropped. This
      removes the bogus installation point.

    A meter swap (the reading starts again near zero) therefore stops new
    readings: delete the statistic and set the integration up again.

    Dropped drops and jumps are logged as one warning per call
    (``log_dropped=False`` makes it a debug message).
    """
    by_time: dict[datetime, RawReading] = {}
    for point in raw:
        by_time[to_utc(point.local_time, tz)] = point

    accepted: list[Reading] = []
    dropped: list[str] = []
    last = previous
    for moment in sorted(by_time):
        point = by_time[moment]
        if last is not None and moment <= last.time:
            continue
        if last is not None and point.value < last.value:
            dropped.append(f"{moment.isoformat()} down {last.value:.3f}->{point.value:.3f}")
            continue
        if point.status != STATUS_OK and last is None:
            _LOGGER.debug("Skipping flagged first point %s", point)
            continue
        if last is not None:
            hours = max((moment - last.time).total_seconds() / 3600, 1.0)
            if (point.value - last.value) / hours > MAX_PLAUSIBLE_FLOW_M3_PER_HOUR:
                dropped.append(f"{moment.isoformat()} jump {last.value:.3f}->{point.value:.3f}")
                continue
        reading = Reading(time=moment, value=point.value)
        accepted.append(reading)
        last = reading
    if dropped:
        _LOGGER.log(
            logging.WARNING if log_dropped else logging.DEBUG,
            "Ignored %s implausible meter readings (first: %s, last: %s)",
            len(dropped),
            dropped[0],
            dropped[-1],
        )
    return accepted


def build_statistics(
    readings: list[Reading],
    last_start: datetime | None,
    last_state: float | None,
    last_sum: float | None,
) -> list[HourlyStatistic]:
    """Build hourly statistics that continue the stored ones.

    ``readings`` must come from :func:`clean_readings`. The reading at time
    ``t`` closes the hour that starts at ``t - 1 h``. ``sum`` is the total
    since the first imported reading, so it grows by the reading difference.
    Hours at or before ``last_start`` are already stored and are skipped.
    """
    statistics: list[HourlyStatistic] = []
    state = last_state
    total = last_sum if last_sum is not None else 0.0
    for reading in readings:
        start = (reading.time - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if last_start is not None and start <= last_start:
            continue
        if state is None and last_start is not None:
            # The stored row has no state to continue from: use this reading
            # as the base instead of counting from an unknown value.
            state = reading.value
            continue
        if state is not None:
            total += max(reading.value - state, 0.0)
        state = reading.value
        statistics.append(HourlyStatistic(start=start, state=reading.value, sum=round(total, 6)))
    return statistics


def reading_at(readings: list[Reading], moment: datetime) -> Reading | None:
    """Return the last reading at or before ``moment``."""
    found: Reading | None = None
    for reading in readings:
        if reading.time > moment:
            break
        found = reading
    return found


def daily_consumption(
    readings: list[Reading], now: datetime, tz: tzinfo = PORTAL_TZ
) -> tuple[float | None, float | None]:
    """Return (today, yesterday) consumption in m³ in the portal's time zone.

    Today is the latest reading minus the reading at local midnight.
    Yesterday is the midnight reading minus the one a day before. Both are
    ``None`` until a reading at or after midnight has arrived (the meter
    reports hours late, so yesterday would be partial), and when the
    readings do not reach back far enough.
    """
    local_now = now.astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Wall-clock arithmetic on an aware datetime keeps the tz, so this is
    # local midnight a day earlier even across a DST change.
    midnight_utc = midnight.astimezone(UTC)
    day_before_utc = (midnight - timedelta(days=1)).astimezone(UTC)

    at_midnight = reading_at(readings, midnight_utc)
    at_day_before = reading_at(readings, day_before_utc)
    latest = readings[-1] if readings else None
    if latest is None or latest.time < midnight_utc:
        return None, None

    today = None
    if latest is not None and at_midnight is not None:
        today = round(latest.value - at_midnight.value, 3)
    yesterday = None
    if at_midnight is not None and at_day_before is not None:
        yesterday = round(at_midnight.value - at_day_before.value, 3)
    return today, yesterday
