"""Sensor entities for SmartGhar.

v0.1.0 entities (read-only):
  Per hub:  uptime, wifi_rssi, firmware_version
  Per tank: level, voltage, rssi, connection_state

Bidirectional entities (lights, buttons, number, text) ship in v0.3.0 once
firmware Phase 1.2 exposes the write endpoints.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_KIND_TANK,
    DOMAIN,
    MANUFACTURER,
    MODEL_HUB,
    MODEL_TANK,
)
from .coordinator import SmartGharCoordinator

_LOGGER = logging.getLogger(__name__)


# ─── Sensor descriptions ──────────────────────────────────────────────────────

HUB_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="uptime_s",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,  # nerdy; hidden by default
    ),
    SensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="fw_version",
        translation_key="firmware_version",
        entity_registry_enabled_default=False,
    ),
)

TANK_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="level_pct",
        translation_key="tank_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="voltage",
        translation_key="tank_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="rssi",
        translation_key="tank_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:signal",
    ),
    SensorEntityDescription(
        key="conn_state",
        translation_key="tank_state",
        icon="mdi:lan-connect",
    ),
)


# Computed sensors — derived from device state/config in the integration,
# not pulled from a hub field. These are what visual cards (fluid-level,
# mushroom, etc.) typically render alongside the level percentage.
COMPUTED_TANK_SENSORS = ("water_volume_l",)


# ─── Setup ────────────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartGhar sensors from a config entry."""
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # Hub-level sensors (one set per hub).
    for desc in HUB_SENSORS:
        entities.append(SmartGharHubSensor(coordinator, desc))

    # Tank sensors — one set per attached tank.
    for dev in coordinator.devices:
        if dev.get("kind") != DEVICE_KIND_TANK:
            continue
        for desc in TANK_SENSORS:
            entities.append(SmartGharTankSensor(coordinator, desc, dev["id"]))
        # Computed tank sensors — derived from level + capacity.
        entities.append(SmartGharTankWaterVolume(coordinator, dev["id"]))

    async_add_entities(entities)


# ─── Entity classes ───────────────────────────────────────────────────────────


class _SmartGharBase(CoordinatorEntity[SmartGharCoordinator], SensorEntity):
    """Common base — owns hub_id-prefixed unique_id and device registry hook."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartGharCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description


class SmartGharHubSensor(_SmartGharBase):
    """Sensor attached to the hub itself."""

    def __init__(
        self,
        coordinator: SmartGharCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        info = self.coordinator.info
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.hub_id)},
            manufacturer=MANUFACTURER,
            model=MODEL_HUB,
            name=info.get("hub_name") or f"SmartGhar Hub ({self.coordinator.hub_id[:6]})",
            sw_version=info.get("fw_version"),
            configuration_url=f"http://{self.coordinator.client.host}/",
        )

    @property
    def native_value(self) -> Any:
        return self.coordinator.info.get(self.entity_description.key)


class SmartGharTankSensor(_SmartGharBase):
    """Sensor attached to one tank (sub-device of the hub)."""

    def __init__(
        self,
        coordinator: SmartGharCoordinator,
        description: SensorEntityDescription,
        tank_id: int,
    ) -> None:
        super().__init__(coordinator, description)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_{description.key}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._tank_id) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.hub_id}_tank_{self._tank_id}")},
            via_device=(DOMAIN, self.coordinator.hub_id),
            manufacturer=MANUFACTURER,
            model=MODEL_TANK,
            name=dev.get("name") or f"Tank {self._tank_id}",
        )

    @property
    def native_value(self) -> Any:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        return (dev.get("state") or {}).get(self.entity_description.key)

    @property
    def available(self) -> bool:
        # Mark unavailable if the tank dropped off the registry between polls
        # (e.g., user removed it via PWA).
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None


class SmartGharTankWaterVolume(CoordinatorEntity[SmartGharCoordinator], SensorEntity):
    """Computed: current water volume in litres = capacity_l × level_pct / 100.

    Useful for cards that show "X / Y litres" or fluid-level visualisations
    that need a volume value alongside percentage. Stays in sync with both
    capacity edits and live level changes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "tank_water_volume"
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_water_volume"
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._tank_id) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.hub_id}_tank_{self._tank_id}")},
            via_device=(DOMAIN, self.coordinator.hub_id),
            manufacturer=MANUFACTURER,
            model=MODEL_TANK,
            name=dev.get("name") or f"Tank {self._tank_id}",
        )

    @property
    def native_value(self) -> float | None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        level_pct = (dev.get("state") or {}).get("level_pct")
        capacity_l = (dev.get("config") or {}).get("capacity_l")
        if level_pct is None or capacity_l is None:
            return None
        return round(float(capacity_l) * float(level_pct) / 100.0, 1)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None
