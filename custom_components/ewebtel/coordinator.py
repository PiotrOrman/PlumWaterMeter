"""Data update coordinator of the eWebtel integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import VolumeConverter

from .api import EwebtelAuthError, EwebtelClient, EwebtelError, Node
from .const import DOMAIN, HISTORY_DAYS, RECENT_DAYS, UPDATE_INTERVAL
from .processing import (
    PORTAL_TZ,
    Reading,
    build_statistics,
    clean_readings,
    daily_consumption,
)

_LOGGER = logging.getLogger(__name__)

type EwebtelConfigEntry = ConfigEntry[EwebtelCoordinator]


@dataclass(frozen=True, slots=True)
class MeterData:
    """What the sensors of one meter show."""

    node: Node
    statistic_id: str
    reading: float | None
    reading_time: datetime | None
    today: float | None
    yesterday: float | None


def statistic_id_for(node: Node) -> str:
    """Return the external statistic id of a meter."""
    key = slugify(node.meter_sn or str(node.node_id))
    return f"{DOMAIN}:{key}_water"


def _to_portal_local(moment: datetime) -> datetime:
    """Return a naive portal-local time for a request window."""
    return moment.astimezone(PORTAL_TZ).replace(tzinfo=None)


class EwebtelCoordinator(DataUpdateCoordinator[dict[int, MeterData]]):
    """Fetch readings, import hourly statistics and keep sensor values."""

    config_entry: EwebtelConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: EwebtelConfigEntry, client: EwebtelClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, MeterData]:
        try:
            nodes = await self.client.async_get_nodes()
            result: dict[int, MeterData] = {}
            for node in nodes:
                result[node.node_id] = await self._async_update_node(node)
        except EwebtelAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EwebtelError as err:
            raise UpdateFailed(str(err)) from err
        return result

    async def _async_update_node(self, node: Node) -> MeterData:
        # Read the chart model every time: it carries the device id and series
        # parameters, which change when the utility swaps the radio module.
        model = await self.client.async_get_graph_model(node.node_id)
        statistic_id = statistic_id_for(node)

        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, False, {"state", "sum"}
        )
        now = dt_util.utcnow()
        last_start: datetime | None = None
        last_state: float | None = None
        last_sum: float | None = None
        previous: Reading | None = None
        if rows := last_stats.get(statistic_id):
            row = rows[0]
            last_start = dt_util.utc_from_timestamp(row["start"])
            last_state = row.get("state")
            last_sum = row.get("sum")
            if last_state is not None:
                previous = Reading(time=last_start + timedelta(hours=1), value=last_state)
            fetch_from = min(last_start, now - timedelta(days=RECENT_DAYS))
        else:
            _LOGGER.info("Importing the history of meter %s", node.meter_sn or node.node_id)
            fetch_from = now - timedelta(days=HISTORY_DAYS)

        raw = await self.client.async_get_readings(
            model, _to_portal_local(fetch_from), _to_portal_local(now) + timedelta(hours=1)
        )

        new_stats = build_statistics(
            clean_readings(raw, previous), last_start, last_state, last_sum
        )
        if new_stats:
            _LOGGER.debug("Adding %s hourly statistics to %s", len(new_stats), statistic_id)
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=f"eWebtel {node.name} water",
                    source=DOMAIN,
                    statistic_id=statistic_id,
                    unit_class=VolumeConverter.UNIT_CLASS,
                    unit_of_measurement=UnitOfVolume.CUBIC_METERS,
                ),
                [
                    StatisticData(start=stat.start, state=stat.state, sum=stat.sum)
                    for stat in new_stats
                ],
            )

        # Same data again, for the sensors; drops were already logged above.
        recent = clean_readings(raw, log_dropped=False)
        today, yesterday = daily_consumption(recent, now)
        latest = recent[-1] if recent else previous
        return MeterData(
            node=node,
            statistic_id=statistic_id,
            reading=latest.value if latest else None,
            reading_time=latest.time if latest else None,
            today=today,
            yesterday=yesterday,
        )
