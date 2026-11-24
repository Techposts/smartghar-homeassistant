"""HA `update` entity for hub OTA — much nicer UX than binary_sensor.

Shows in HA's sidebar Updates section with native Install button, version
history, and release notes link. Same place users see HACS / ESPHome /
Tasmota updates.
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SmartGharApiError
from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info

_POLL_INTERVAL = 3       # seconds between OTA state polls
_POLL_MAX = 100          # ~5 min ceiling; a 1.4MB flash takes well under this
_REBOOT_GRACE = 45       # unreachable this long late in a flash == rebooting
_REBOOT_SETTLE = 20      # let the hub come back before re-reading its version


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
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
        | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_firmware_update"
        self._attr_in_progress = False
        self._attr_update_percentage: int | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

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
            f"The hub flashes the new firmware over the air. It will be "
            f"unreachable for ~60 seconds during the flash, then auto-reboot "
            f"and reconnect. Any attached sub-devices keep operating "
            f"independently and the hub picks them back up after reboot."
        )

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger the OTA and follow it to a real outcome.

        Firing the request and returning immediately is what made this feel
        broken: HA showed a progress bar, dropped it a second later, and
        reported nothing — success and failure looked identical. The hub has
        published {status, progress, error_msg} all along; nothing read it.
        """
        try:
            await self.coordinator.client.trigger_ota_install()
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Hub refused the update request: {err}") from err

        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()

        # The hub goes silent while it writes the final image and reboots, so
        # unreachability late in the run is expected rather than a failure.
        # Only give up once it has been unreachable for a sustained stretch.
        unreachable = 0
        try:
            for _ in range(_POLL_MAX):
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    state = await self.coordinator.client.get_ota_state()
                    unreachable = 0
                except SmartGharApiError:
                    unreachable += 1
                    if unreachable * _POLL_INTERVAL >= _REBOOT_GRACE:
                        break          # treat as flashed + rebooting
                    continue

                status = (state.get("status") or "").lower()
                pct = state.get("progress")
                if isinstance(pct, int):
                    self._attr_update_percentage = pct
                    self.async_write_ha_state()

                if status == "error":
                    raise HomeAssistantError(
                        "Hub OTA failed: "
                        f"{state.get('error_msg') or 'no detail reported'}"
                    )
                if status == "done":
                    break
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()

        # Let the hub finish rebooting before we believe its version again.
        await asyncio.sleep(_REBOOT_SETTLE)
        await self.coordinator.async_request_refresh()
