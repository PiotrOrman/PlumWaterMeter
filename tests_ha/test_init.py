"""Setup, statistics import and sensor tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ewebtel.api import EwebtelAuthError, EwebtelConnectionError, Node
from custom_components.ewebtel.const import DOMAIN
from custom_components.ewebtel.processing import RawReading

NODE = Node(node_id=42, name="12345", meter_sn="SN1", address="Street 1")
STATISTIC_ID = "ewebtel:sn1_water"
CLIENT = "custom_components.ewebtel.api.EwebtelClient"


def hourly(start: datetime, hours: int, first_value: float, step: float) -> list[RawReading]:
    """Hourly local readings, as the portal returns them."""
    return [
        RawReading(
            local_time=start + timedelta(hours=h), value=round(first_value + h * step, 3), status=8
        )
        for h in range(hours)
    ]


def entity_id(hass: HomeAssistant, key: str) -> str:
    registry = er.async_get(hass)
    found = registry.async_get_entity_id("sensor", DOMAIN, f"{NODE.node_id}_{key}")
    assert found
    return found


async def get_stats(hass: HomeAssistant) -> list[dict]:
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        {STATISTIC_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    return result.get(STATISTIC_ID, [])


async def test_setup_imports_history_and_continues(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-25T09:16:00+00:00")  # 10:16 in Warsaw
    # Installation points first (flagged), then 30 good hours:
    # 2026-11-24 00:00 .. 2026-11-25 05:00 local, +0.01 m³ per hour.
    readings = [
        RawReading(datetime(2026, 11, 23, 22), 0.014, 170),
        RawReading(datetime(2026, 11, 23, 23), 485.9, 138),
        *hourly(datetime(2026, 11, 24, 0), 30, 486.0, 0.01),
    ]
    get_readings = AsyncMock(side_effect=lambda *args: list(readings))

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u@example.com",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    with (
        patch(f"{CLIENT}.async_get_nodes", AsyncMock(return_value=[NODE])),
        patch(
            f"{CLIENT}.async_get_graph_model", AsyncMock(return_value={"GraphSeries": [{}]})
        ) as get_model,
        patch(f"{CLIENT}.async_get_readings", get_readings),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

        assert entry.state is ConfigEntryState.LOADED
        # First import reaches back for the whole history.
        first_start = get_readings.call_args_list[0].args[1]
        assert first_start < datetime(2024, 1, 1)

        stats = await get_stats(hass)
        assert len(stats) == 30
        assert datetime.fromtimestamp(stats[0]["start"], UTC) == datetime(
            2026, 11, 23, 22, tzinfo=UTC
        )
        assert stats[0]["sum"] == 0.0
        assert stats[-1]["state"] == 486.29
        assert stats[-1]["sum"] == 0.29

        assert hass.states.get(entity_id(hass, "reading")).state == "486.29"
        assert (
            hass.states.get(entity_id(hass, "reading")).attributes["statistic_id"] == STATISTIC_ID
        )
        assert hass.states.get(entity_id(hass, "last_24h")).state == "0.24"
        assert hass.states.get(entity_id(hass, "yesterday")).state == "0.24"
        assert hass.states.get(entity_id(hass, "reading_time")).state == "2026-11-25T04:00:00+00:00"
        assert "state_class" not in hass.states.get(entity_id(hass, "reading")).attributes

        # The next batch: three more hours; only these are added.
        readings.extend(hourly(datetime(2026, 11, 25, 6), 3, 486.3, 0.02))
        freezer.tick(timedelta(hours=1))
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

        # Incremental fetch re-reads three days, so "last 24 h" has its base.
        second_start = get_readings.call_args_list[1].args[1]
        assert second_start >= datetime(2026, 11, 22)

        assert get_model.call_count == 2
        stats = await get_stats(hass)
        assert len(stats) == 33
        assert stats[-1]["state"] == 486.34
        assert round(stats[-1]["sum"], 6) == 0.34
        assert hass.states.get(entity_id(hass, "reading")).state == "486.34"


async def test_auth_failure_starts_reauth(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u@example.com",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.async_get_nodes", AsyncMock(side_effect=EwebtelAuthError("no"))):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


def make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u@example.com",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    return entry


def portal(readings: list[RawReading]):
    return (
        patch(f"{CLIENT}.async_get_nodes", AsyncMock(return_value=[NODE])),
        patch(f"{CLIENT}.async_get_graph_model", AsyncMock(return_value={"GraphSeries": [{}]})),
        patch(f"{CLIENT}.async_get_readings", AsyncMock(side_effect=lambda *a: list(readings))),
    )


async def test_old_today_sensor_is_removed(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-26T09:16:00+00:00")
    entry = make_entry(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{NODE.node_id}_today", config_entry=entry
    )
    readings = hourly(datetime(2026, 11, 25, 5), 25, 487.0, 0.01)
    nodes, model, data = portal(readings)
    with nodes, model, data:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert registry.async_get(old.entity_id) is None
    assert hass.states.get(entity_id(hass, "last_24h")).state == "0.24"


async def test_stale_data_raises_and_clears_a_repair_issue(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")  # the harness default is US/Pacific
    issues = ir.async_get(hass)
    issue_id = f"stale_data_{NODE.node_id}"
    # The newest reading is from 05:00 on the 25th; now is 13:16 on the 26th (32 h later).
    freezer.move_to("2026-11-26T12:16:00+00:00")
    readings = hourly(datetime(2026, 11, 24, 5), 25, 486.0, 0.01)
    entry = make_entry(hass)
    nodes, model, data = portal(readings)
    with nodes, model, data:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        issue = issues.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == "stale_data"
        assert issue.translation_placeholders["meter"] == "12345"
        assert issue.translation_placeholders["last_reading"] == "2026-11-25 05:00"

        # The next morning's batch arrives: the issue goes away.
        readings.extend(hourly(datetime(2026, 11, 25, 6), 24, 486.25, 0.01))
        freezer.tick(timedelta(hours=1))
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert issues.async_get_issue(DOMAIN, issue_id) is None

        # Silent again for more than 30 h, then the entry is unloaded.
        freezer.tick(timedelta(hours=40))
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert issues.async_get_issue(DOMAIN, issue_id) is not None
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_fresh_data_raises_no_issue(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-26T09:16:00+00:00")
    entry = make_entry(hass)
    nodes, model, data = portal(hourly(datetime(2026, 11, 25, 5), 25, 487.0, 0.01))
    with nodes, model, data:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stale_data_{NODE.node_id}") is None


async def test_portal_outage_also_raises_the_issue(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-26T09:16:00+00:00")
    entry = make_entry(hass)
    issue_id = f"stale_data_{NODE.node_id}"
    nodes, model, data = portal(hourly(datetime(2026, 11, 25, 5), 25, 487.0, 0.01))
    with nodes, model, data:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    freezer.tick(timedelta(hours=30))
    with patch(f"{CLIENT}.async_get_nodes", AsyncMock(side_effect=EwebtelConnectionError("down"))):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert not entry.runtime_data.last_update_success
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
