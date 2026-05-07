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
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_KIND_PRESENCE,
    DEVICE_KIND_TANK,
    DOMAIN,
    MANUFACTURER,
    MODEL_PRESENCE,
    MODEL_TANK,
    hub_model_for_product,
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

# AmbiSense presence sensor — one set of entities per presence device.
# `nearest_cm` returns -1 from firmware when vacant; SmartGharPresenceSensor
# normalises that to None so HA shows "Unknown" instead of a fake "-1 cm".
PRESENCE_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="nearest_cm",
        translation_key="presence_nearest",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:human",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="target_count",
        translation_key="presence_target_count",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple",
    ),
    SensorEntityDescription(
        key="seconds_since_seen",
        translation_key="presence_seconds_since_seen",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="rssi_dbm",
        translation_key="presence_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
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
        # Cumulative consumption sensor — drives HA's Energy dashboard.
        entities.append(SmartGharTankConsumption(coordinator, dev["id"]))

    # Presence sensors — one set per AmbiSense unit (kind="presence").
    # The occupancy binary_sensor lives in binary_sensor.py; these are
    # the numeric companions (distance, target count, last-seen).
    for dev in coordinator.devices:
        if dev.get("kind") != DEVICE_KIND_PRESENCE:
            continue
        for desc in PRESENCE_SENSORS:
            entities.append(SmartGharPresenceSensor(coordinator, desc, dev["id"]))

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
            model=hub_model_for_product(info.get("product")),
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


class SmartGharPresenceSensor(_SmartGharBase):
    """Sensor attached to one AmbiSense presence device.

    Each AmbiSense unit advertises itself as a hub with one virtual
    sub-device of kind="presence" (id=0). This class produces the
    numeric companions to the binary occupancy sensor: nearest cm,
    target count, time-since-last-detection, and the radar's RSSI.

    nearest_cm reads -1 from firmware when vacant; we surface that as
    None so HA renders "Unknown" instead of "-1 cm". Stationary state
    rides as an attribute on the binary occupancy sensor (in
    binary_sensor.py) since it's tightly coupled to "occupied" anyway.
    """

    def __init__(
        self,
        coordinator: SmartGharCoordinator,
        description: SensorEntityDescription,
        presence_id: int,
    ) -> None:
        super().__init__(coordinator, description)
        self._presence_id = presence_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_presence_{presence_id}_{description.key}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._presence_id) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.hub_id}_presence_{self._presence_id}")},
            via_device=(DOMAIN, self.coordinator.hub_id),
            manufacturer=MANUFACTURER,
            model=MODEL_PRESENCE,
            name=dev.get("name") or "Presence Sensor",
        )

    @property
    def native_value(self) -> Any:
        dev = self.coordinator.device_by_id(self._presence_id)
        if not dev:
            return None
        v = (dev.get("state") or {}).get(self.entity_description.key)
        # Firmware uses -1 as the sentinel for "no target detected" on
        # nearest_cm; surface that as None instead of leaking -1 into
        # graphs and statistics.
        if self.entity_description.key == "nearest_cm" and v == -1:
            return None
        return v

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.device_by_id(self._presence_id) is not None
        )


class SmartGharTankConsumption(CoordinatorEntity[SmartGharCoordinator], RestoreSensor):
    """Cumulative water consumption (litres) — drives HA's Energy dashboard.

    Algorithm: on each coordinator tick, compare current level to last seen.
    If level dropped beyond a noise floor (0.5%), add the drained volume to
    the running total. Fills (level rising) reset the baseline without
    incrementing — we count consumption, not just any level change.

    `device_class: water` + `state_class: total_increasing` makes HA
    auto-pick this up as a water source under Settings → Energy.

    Persists across HA restarts via RestoreSensor so the running total
    survives HA reboots without losing history. Capacity changes are
    forward-looking — past consumption stays as it was when recorded.
    """

    NOISE_FLOOR_PCT = 0.5

    _attr_has_entity_name = True
    _attr_translation_key = "tank_consumption"
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_consumption"
        )
        self._total_l: float = 0.0
        self._baseline_pct: float | None = None

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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore running total from before HA restart.
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total_l = float(last.native_value)
            except (TypeError, ValueError):
                self._total_l = 0.0
        # Seed the baseline from the current device state so the first
        # post-restart tick doesn't fire a spurious "fill" or count noise.
        dev = self.coordinator.device_by_id(self._tank_id)
        if dev:
            level = (dev.get("state") or {}).get("level_pct")
            if level is not None:
                self._baseline_pct = float(level)

    @callback
    def _handle_coordinator_update(self) -> None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            super()._handle_coordinator_update()
            return

        level = (dev.get("state") or {}).get("level_pct")
        capacity_l = (dev.get("config") or {}).get("capacity_l")
        if level is None or capacity_l is None:
            super()._handle_coordinator_update()
            return

        if self._baseline_pct is None:
            # First observation — seed baseline, don't count anything yet.
            self._baseline_pct = float(level)
            super()._handle_coordinator_update()
            return

        delta_pct = float(self._baseline_pct) - float(level)

        if delta_pct >= self.NOISE_FLOOR_PCT:
            # Consumption event — accumulate and reset baseline.
            consumed_l = float(capacity_l) * delta_pct / 100.0
            self._total_l += consumed_l
            self._baseline_pct = float(level)
        elif float(level) > float(self._baseline_pct):
            # Fill event — reset baseline without incrementing.
            self._baseline_pct = float(level)
        # else: sub-noise-floor change. Don't update baseline so cumulative
        # small drains add up correctly across multiple ticks.

        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total_l, 1)

    @property
    def available(self) -> bool:
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
