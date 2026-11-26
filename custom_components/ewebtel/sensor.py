"""Sensors of the eWebtel integration.

The sensors have no ``state_class`` on purpose: the long-term statistics
come from the hourly import (``ewebtel:<meter>_water``), placed at the hours
the water was used. A ``state_class`` would make the recorder build a second
statistic from these delayed values, at the hours they were fetched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EwebtelConfigEntry, EwebtelCoordinator, MeterData


@dataclass(frozen=True, kw_only=True)
class EwebtelSensorDescription(SensorEntityDescription):
    """Describes an eWebtel sensor."""

    value_fn: Callable[[MeterData], float | datetime | None]


SENSORS: tuple[EwebtelSensorDescription, ...] = (
    EwebtelSensorDescription(
        key="reading",
        translation_key="reading",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda data: data.reading,
    ),
    EwebtelSensorDescription(
        key="reading_time",
        translation_key="reading_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.reading_time,
    ),
    EwebtelSensorDescription(
        key="last_24h",
        translation_key="last_24h",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda data: data.last_24h,
    ),
    EwebtelSensorDescription(
        key="yesterday",
        translation_key="yesterday",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda data: data.yesterday,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwebtelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every meter of the account."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    # 0.1.0 had a "Consumption today" sensor; with one batch a day it only
    # ever showed 00:00-05:00. It is replaced by "Consumption last 24 h".
    registry = er.async_get(hass)
    for node_id in coordinator.data:
        if old := registry.async_get_entity_id("sensor", DOMAIN, f"{node_id}_today"):
            registry.async_remove(old)

    def _add_new_meters() -> None:
        new_ids = [node_id for node_id in coordinator.data if node_id not in known]
        known.update(new_ids)
        async_add_entities(
            EwebtelSensor(coordinator, node_id, description)
            for node_id in new_ids
            for description in SENSORS
        )

    _add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_meters))


class EwebtelSensor(CoordinatorEntity[EwebtelCoordinator], SensorEntity):
    """A value of one water meter."""

    _attr_has_entity_name = True
    entity_description: EwebtelSensorDescription

    def __init__(
        self,
        coordinator: EwebtelCoordinator,
        node_id: int,
        description: EwebtelSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._node_id = node_id
        node = coordinator.data[node_id].node
        self._attr_unique_id = f"{node_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(node_id))},
            name=f"Water meter {node.name}",
            manufacturer=MANUFACTURER,
            model="eWebtel measuring point",
            serial_number=node.meter_sn or None,
            configuration_url=f"https://ewebtel.com/Node/Details/{node_id}",
        )

    @property
    def available(self) -> bool:
        """Return whether the meter is still on the account."""
        return super().available and self._node_id in self.coordinator.data

    @property
    def native_value(self) -> float | datetime | None:
        """Return the sensor value."""
        data = self.coordinator.data.get(self._node_id)
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Point the reading sensor at the statistic for the Energy dashboard."""
        if self.entity_description.key != "reading":
            return None
        data = self.coordinator.data.get(self._node_id)
        return {"statistic_id": data.statistic_id} if data else None
