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

from .const import DEVICE_KIND_PRESENCE, DOMAIN, MODEL_PRESENCE
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
