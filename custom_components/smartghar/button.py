"""Button entities — momentary actions on the hub."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    async_add_entities(
        [
            SmartGharHubOtaCheck(coordinator),
        ]
    )


class SmartGharHubOtaCheck(CoordinatorEntity[SmartGharCoordinator], ButtonEntity):
    """Trigger an OTA manifest check on demand.

    The hub already auto-checks every OTA_CHECK_INTERVAL_H hours; this is for
    impatient users / automations that want to verify a fresh release.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ota_check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-download-outline"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_ota_check"

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

    async def async_press(self) -> None:
        await self.coordinator.client.trigger_ota_check()
        # Brief delay to let the hub start the check, then refresh so the
        # `ota.available` field on /info reflects any new release.
        await self.coordinator.async_request_refresh()
