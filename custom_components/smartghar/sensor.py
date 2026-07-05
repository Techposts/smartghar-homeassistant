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
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_KIND_PRESENCE,
    DEVICE_KIND_TANK,
    DOMAIN,
    MODEL_PRESENCE,
    MODEL_TANK,
)
from homeassistant.util import dt as dt_util

from .const import CONF_CONNECTION, CONNECTION_USB
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info, switch_device_info
from .serial_entity import SmartGharSerialNodeEntity, async_add_serial_nodes

_LOGGER = logging.getLogger(__name__)

# A tank reading older than this (and not actively online) is "stale": the TX
# hasn't reported recently, so the level shown is a last-known value, not live.
# 20 min comfortably exceeds the default 5-min TX wake interval plus a couple of
# missed cycles, so a healthy slow-reporting tank isn't flagged.
TANK_STALE_AFTER_S = 20 * 60


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
    # Power source: "mains" (TX on external 5V / USB, no battery — INA219 reports
    # the battery_pct=-1 sentinel, or a monitor-less mains SKU) vs "solar"
    # (battery/solar rig). Lets HA show the source and avoids treating a mains
    # tank's absent battery as a flat one. Hub exposes it flat at state.power_source.
    SensorEntityDescription(
        key="power_source",
        translation_key="tank_power_source",
        icon="mdi:power-plug",
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


# Smart Switch telemetry sensors. The (description, source_field, scale) tuples
# map a raw /api/switches integer onto its SI unit: load_ma → A (×0.001),
# power_w → W (×1), temp_c10 → °C (×0.1).
SWITCH_SENSORS: tuple[tuple[SensorEntityDescription, str, float], ...] = (
    (
        SensorEntityDescription(
            key="current",
            translation_key="switch_current",
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            icon="mdi:current-ac",
        ),
        "load_ma",
        0.001,
    ),
    (
        SensorEntityDescription(
            key="power",
            translation_key="switch_power",
            native_unit_of_measurement=UnitOfPower.WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
            icon="mdi:flash",
        ),
        "power_w",
        1.0,
    ),
    (
        SensorEntityDescription(
            key="temperature",
            translation_key="switch_temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:thermometer",
        ),
        "temp_c10",
        0.1,
    ),
)


# ─── Setup ────────────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartGhar sensors from a config entry."""
    if entry.data.get(CONF_CONNECTION) == CONNECTION_USB:
        _async_setup_serial_sensors(hass, entry, async_add_entities)
        return

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

    # Entity service: smartghar.reset_consumption — zero a tank's cumulative
    # consumption total (target the consumption sensor entity). Registered once
    # per platform setup; idempotent.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "reset_consumption", {}, "async_reset_consumption"
    )

    # Presence sensors — one set per AmbiSense unit (kind="presence").
    # The occupancy binary_sensor lives in binary_sensor.py; these are
    # the numeric companions (distance, target count, last-seen).
    for dev in coordinator.devices:
        if dev.get("kind") != DEVICE_KIND_PRESENCE:
            continue
        for desc in PRESENCE_SENSORS:
            entities.append(SmartGharPresenceSensor(coordinator, desc, dev["id"]))

    # Smart Switch telemetry — current, power, temperature per switch.
    for sw in coordinator.switches:
        if "address" not in sw:
            continue
        for desc, field, scale in SWITCH_SENSORS:
            entities.append(
                SmartGharSwitchSensor(coordinator, desc, sw["address"], field, scale)
            )

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
        return hub_device_info(self.coordinator)

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
        dev = self.coordinator.device_by_id(self._tank_id) or {
            "kind": "tank", "id": self._tank_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_TANK,
            fallback_name=f"Tank {self._tank_id}",
        )

    @property
    def native_value(self) -> Any:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        return (dev.get("state") or {}).get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Only the primary level reading carries freshness metadata. The hub's
        # `state.ts` is the epoch second the TX last reported; surface its age and
        # a `stale` flag so dashboards/automations can tell a live reading from a
        # last-known one WITHOUT the value being blanked (we keep showing the last
        # level, per the product's "show last value + when" behaviour). conn_state
        # offline/lost is treated as stale regardless of age.
        if self.entity_description.key != "level_pct":
            return None
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        state = dev.get("state") or {}
        ts = state.get("ts")
        conn = state.get("conn_state")
        attrs: dict[str, Any] = {}
        age_s: int | None = None
        if isinstance(ts, (int, float)) and ts > 0:
            age_s = max(0, int(dt_util.utcnow().timestamp() - ts))
            attrs["last_reading"] = dt_util.utc_from_timestamp(ts).isoformat()
            attrs["reading_age_s"] = age_s
        stale = conn in ("offline", "lost") or (
            age_s is not None and age_s > TANK_STALE_AFTER_S
        )
        attrs["stale"] = stale
        return attrs

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
        dev = self.coordinator.device_by_id(self._presence_id) or {
            "kind": "presence", "id": self._presence_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_PRESENCE,
            fallback_name="Presence Sensor",
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
        dev = self.coordinator.device_by_id(self._tank_id) or {
            "kind": "tank", "id": self._tank_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_TANK,
            fallback_name=f"Tank {self._tank_id}",
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

    async def async_reset_consumption(self) -> None:
        """Zero the running total. Exposed as the `smartghar.reset_consumption`
        entity service so a poisoned total (e.g. phantom drains accumulated by an
        older firmware before the restart guard landed) can be cleared without
        removing the entity. Re-seeds the baseline from the current level so the
        reset itself isn't counted as a drain."""
        self._total_l = 0.0
        dev = self.coordinator.device_by_id(self._tank_id)
        level = (dev.get("state") or {}).get("level_pct") if dev else None
        self._baseline_pct = float(level) if level is not None else None
        self.async_write_ha_state()
        _LOGGER.info("Reset water consumption total for tank %s", self._tank_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            super()._handle_coordinator_update()
            return

        state = dev.get("state") or {}
        level = state.get("level_pct")
        capacity_l = (dev.get("config") or {}).get("capacity_l")
        if level is None or capacity_l is None:
            super()._handle_coordinator_update()
            return

        # Hub-restart guard: a hub that just rebooted reports the tank as
        # 'waiting' (no TANK packet this boot) and pre-3.x firmware sends
        # level_pct=0 alongside it. Counting that as a drain added a phantom
        # full-tank consumption per hub restart (observed: ~1080 L per reboot
        # on a 2000 L tank). Keep the baseline untouched — when the TX reports
        # again, real usage across the outage is still counted against the
        # pre-restart baseline. Unknown/missing conn_state (older firmware)
        # keeps the legacy behaviour.
        conn = state.get("conn_state")
        if conn is not None and conn not in ("online", "stale"):
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
        dev = self.coordinator.device_by_id(self._tank_id) or {
            "kind": "tank", "id": self._tank_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_TANK,
            fallback_name=f"Tank {self._tank_id}",
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


class SmartGharSwitchSensor(
    CoordinatorEntity[SmartGharCoordinator], SensorEntity
):
    """One telemetry sensor (current / power / temperature) on a Smart Switch.

    Reads a raw integer field from /api/switches and scales it to SI units.
    Unavailable while the switch is "waiting" (paired but no telemetry yet).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartGharCoordinator,
        description: SensorEntityDescription,
        addr: int,
        field: str,
        scale: float,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._addr = addr
        self._field = field
        self._scale = scale
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_switch_{addr}_{description.key}"
        )

    @property
    def _sw(self) -> dict[str, Any] | None:
        return self.coordinator.switch_by_addr(self._addr)

    @property
    def device_info(self) -> DeviceInfo:
        sw = self._sw or {}
        return switch_device_info(self.coordinator, self._addr, sw.get("name"))

    @property
    def available(self) -> bool:
        sw = self._sw
        return super().available and sw is not None and sw.get("state") != "waiting"

    @property
    def native_value(self) -> float | None:
        sw = self._sw
        if not sw or self._field not in sw:
            return None
        raw = sw.get(self._field)
        if raw is None:
            return None
        return round(float(raw) * self._scale, 2)


# ─── USB-CDC coordinator ("HA stick") sensors ────────────────────────────────
# One measure per entity, read straight from the node's pushed sensors[] map
# (proto v1 — docs/coordinator-serial-protocol.md in the firmware repo).

SERIAL_MEASURES: dict[str, list[SensorEntityDescription]] = {
    "tank": [
        SensorEntityDescription(key="level", name="Level",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT),
        SensorEntityDescription(key="volume", name="Water volume",
            native_unit_of_measurement=UnitOfVolume.LITERS,
            device_class=SensorDeviceClass.VOLUME_STORAGE,
            state_class=SensorStateClass.MEASUREMENT),
        SensorEntityDescription(key="distance", name="Sensor distance",
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            device_class=SensorDeviceClass.DISTANCE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False),
    ],
    "switch": [
        SensorEntityDescription(key="current", name="Load current",
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT),
        SensorEntityDescription(key="power", name="Load power",
            native_unit_of_measurement=UnitOfPower.WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT),
        SensorEntityDescription(key="temperature", name="Board temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC),
    ],
}

SERIAL_COMMON: list[SensorEntityDescription] = [
    SensorEntityDescription(key="battery", name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="rssi", name="Signal strength",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False),
]


class SmartGharSerialSensor(SmartGharSerialNodeEntity, SensorEntity):
    """A single measure from a serial-coordinator node."""

    def __init__(self, coordinator, node_id: int,
                 description: SensorEntityDescription) -> None:
        super().__init__(coordinator, node_id)
        self.entity_description = description
        hub_id = coordinator.hub.get("device_id", "unknown")
        self._attr_unique_id = f"{hub_id}-node{node_id}-{description.key}"

    @property
    def native_value(self):
        node = self.node
        if node is None:
            return None
        key = self.entity_description.key
        if key == "battery":
            # -1 = mains-powered (fleet convention) — no meaningful battery %.
            return None if node.battery_pct < 0 else node.battery_pct
        if key == "rssi":
            return node.rssi
        return self.sensor_value(key)


def _async_setup_serial_sensors(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    def factory(node):
        descs = list(SERIAL_MEASURES.get(node.device_type, []))
        descs += [d for d in SERIAL_COMMON
                  if not (d.key == "battery" and node.battery_pct < 0)]
        return [SmartGharSerialSensor(coordinator, node.node_id, d) for d in descs]

    async_add_serial_nodes(coordinator, async_add_entities, factory)
