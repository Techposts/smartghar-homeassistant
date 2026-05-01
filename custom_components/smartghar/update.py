"""HA `update` entity for hub OTA — much nicer UX than binary_sensor.

Shows in HA's sidebar Updates section with native Install button, version
history, and release notes link. Same place users see HACS / ESPHome /
Tasmota updates.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SmartGharApiError
from .const import DOMAIN, MANUFACTURER, MODEL_HUB
from .coordinator import SmartGharCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SmartGharHubUpdate(coordinator)])


class SmartGharHubUpdate(CoordinatorEntity[SmartGharCoordinator], UpdateEntity):
    """OTA update entity for the hub firmware.

    Maps the hub's `/api/v1/info.ota` payload to HA's update model:
        installed_version  ←  ota.current
        latest_version     ←  ota.available  (or installed when up-to-date)
    """

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_firmware_update"

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
    def installed_version(self) -> str | None:
        ota = self.coordinator.info.get("ota") or {}
        return ota.get("current") or self.coordinator.info.get("fw_version")

    @property
    def latest_version(self) -> str | None:
        ota = self.coordinator.info.get("ota") or {}
        # If hub reports an `available` newer version, surface it. Otherwise
        # latest = installed (HA shows "Up-to-date").
        return ota.get("available") or self.installed_version

    @property
    def release_url(self) -> str | None:
        latest = self.latest_version
        if not latest:
            return None
        # Cloud firmware releases live in the private repo; the public
        # marketing changelog is the closest user-facing reference.
        return f"https://smartghar.org/changelog#{latest}"

    async def async_release_notes(self) -> str | None:
        latest = self.latest_version
        if latest == self.installed_version:
            return None
        return (
            f"### Hub firmware {latest} available\n\n"
            f"Currently installed: **{self.installed_version}**.\n\n"
            f"The install pulls the .bin from `tanksync.smartghar.org` and "
            f"flashes it over the air. The hub will be unreachable for ~60 "
            f"seconds during the flash, then auto-reboot and reconnect. "
            f"Tank state is unaffected — TX devices keep transmitting; the "
            f"hub catches up after reboot."
        )

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger the OTA install. Hub flashes + reboots in background."""
        try:
            await self.coordinator.client.trigger_ota_install()
        except SmartGharApiError as err:
            # If /api/v1/hub/ota/install isn't on this firmware version yet,
            # fall back to triggering a check. v0.5+ firmware has install.
            if "404" in str(err):
                await self.coordinator.client.trigger_ota_check()
                return
            raise
        # Refresh after a short delay so the available-version field updates
        # post-install. Hub is mid-flash; coordinator will see UpdateFailed
        # for ~60s, that's expected.
        await self.coordinator.async_request_refresh()
