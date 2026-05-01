"""Binary sensor entities — OTA availability."""
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

from .const import DOMAIN, MANUFACTURER, MODEL_HUB
from .coordinator import SmartGharCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SmartGharHubOtaAvailable(coordinator)])


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
        info = self.coordinator.info
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.hub_id)},
            manufacturer=MANUFACTURER,
            model=MODEL_HUB,
            name=info.get("hub_name") or f"SmartGhar Hub ({self.coordinator.hub_id[:6]})",
            sw_version=info.get("fw_version"),
        )

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
