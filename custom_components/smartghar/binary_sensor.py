"""Binary sensor entities — OTA availability + presence occupancy."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_KIND_PRESENCE, DEVICE_KIND_TANK, DOMAIN, MODEL_PRESENCE, MODEL_TANK
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [SmartGharHubOtaAvailable(coordinator)]
    # Presence sub-devices — one occupancy sensor per AmbiSense unit.
    # AmbiSense's standalone-hub model presents itself as a hub with
    # one virtual sub-device of kind="presence" (id=0); future products
    # could attach multiple presence devices to one hub the same way
    # TankSync attaches multiple tanks.
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_PRESENCE:
            entities.append(SmartGharPresenceOccupancy(coordinator, dev["id"]))
        elif dev.get("kind") == DEVICE_KIND_TANK:
            # Sensor-health binary sensors (rx-v2.8.0+ exposes sensor_error in
            # /api/v1/devices state; rx-v2.8.3+ adds sensor_stuck). Older
            # firmware returns absent fields which collapse to None / Off.
            entities.append(SmartGharTankSensorError(coordinator, dev["id"]))
            entities.append(SmartGharTankSensorStuck(coordinator, dev["id"]))
    async_add_entities(entities)


class SmartGharHubOtaAvailable(
    CoordinatorEntity[SmartGharCoordinator], BinarySensorEntity
):
    """True when the hub has detected a newer firmware on the OTA channel."""

    _attr_has_entity_name = True
    _attr_translation_key = "ota_available"
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-arrow-down-outline"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_ota_available"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    @property
    def is_on(self) -> bool:
        ota = self.coordinator.info.get("ota") or {}
        return bool(ota.get("available"))

    @property
    def extra_state_attributes(self) -> dict | None:
        ota = self.coordinator.info.get("ota") or {}
        if not ota:
            return None
        return {
            "current": ota.get("current"),
            "available": ota.get("available"),
            "channel": ota.get("channel"),
        }


class SmartGharPresenceOccupancy(
    CoordinatorEntity[SmartGharCoordinator], BinarySensorEntity
):
    """Occupancy state of one AmbiSense presence sensor.

    Stationary-vs-moving + target count + nearest distance are exposed
    as `extra_state_attributes` so HA automations can react to "occupied
    AND moving" or "occupied AND stationary for >5min" without needing
    separate binary sensors. The dedicated distance + target-count
    sensors live in `sensor.py` for users who want to graph them.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "occupancy"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self, coordinator: SmartGharCoordinator, presence_id: int
    ) -> None:
        super().__init__(coordinator)
        self._presence_id = presence_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_presence_{presence_id}_occupancy"
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
    def is_on(self) -> bool | None:
        dev = self.coordinator.device_by_id(self._presence_id)
        if not dev:
            return None
        return (dev.get("state") or {}).get("occupied")

    @property
    def extra_state_attributes(self) -> dict | None:
        dev = self.coordinator.device_by_id(self._presence_id)
        if not dev:
            return None
        st = dev.get("state") or {}
        return {
            "stationary": st.get("stationary"),
            "target_count": st.get("target_count"),
            "nearest_cm": st.get("nearest_cm"),
            "seconds_since_seen": st.get("seconds_since_seen"),
        }

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.device_by_id(self._presence_id) is not None
        )


# ─── Per-tank sensor-health binary sensors (rx-v2.8.0+ / rx-v2.8.3+) ──────────


class _TankBinaryBase(CoordinatorEntity[SmartGharCoordinator], BinarySensorEntity):
    """Common base for binary sensors that read `state.<flag>` from a tank.

    The hub exposes `sensor_error` (since rx-v2.8.0) and `sensor_stuck` (since
    rx-v2.8.3) as boolean fields on each device's state object in
    /api/v1/devices. The PWA renders these as warning chips; HA gets the
    same signal as a `binary_sensor.problem` per tank.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Subclasses must set: _attr_translation_key + _state_key + _suffix

    _state_key: str = ""   # field name inside the device state dict
    _suffix: str = ""      # unique_id suffix

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_{self._suffix}"
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
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    @property
    def is_on(self) -> bool | None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        state = dev.get("state") or {}
        # Field may be absent on older firmware — treat absence as False.
        return bool(state.get(self._state_key, False))


class SmartGharTankSensorError(_TankBinaryBase):
    """True when the ultrasonic sensor failed to echo on the last TANK packet.

    Distinguishes "TX is alive but sensor failed" from "TX is offline" —
    sensor_offline is covered by the tank's conn_state sensor instead.
    """

    _attr_translation_key = "sensor_error"
    _attr_icon = "mdi:waveform"
    _state_key = "sensor_error"
    _suffix = "sensor_error"


class SmartGharTankSensorStuck(_TankBinaryBase):
    """True when the sensor reports a constant reading across 20 wakes.

    Catches defective JSN-SR04M / AJ-SR04M sensors that report their
    minimum range as a constant value regardless of actual water level —
    sensor_error doesn't catch this because the value looks plausible.
    """

    _attr_translation_key = "sensor_stuck"
    _attr_icon = "mdi:ruler-square"
    _state_key = "sensor_stuck"
    _suffix = "sensor_stuck"
