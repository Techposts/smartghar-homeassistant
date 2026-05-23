"""Switch entities — hub buzzer master enable (rx-v2.8.4+).

Single hub-level switch that mutes every alert except the boot tone.
The boot tone always plays on power-up regardless of this switch —
that's intentional, it confirms the hub is initialized.

Per-alert switches (Critical-low / Overflow / Sensor-offline) are
intentionally NOT exposed here. They live on the hub's local web UI
under the "Advanced" collapse. HA's per-tank sensor_error +
sensor_stuck binary sensors already give automation surfaces for the
specific conditions if a user wants to script around alert behavior.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Only register the buzzer switch if the hub responded to /api/v1/hub/buzzer
    # at first refresh. Older firmware (rx-v2.7.x) lacks the endpoint and we
    # don't want to clutter HA with a permanently-unavailable entity.
    if coordinator.buzzer_available:
        async_add_entities([SmartGharHubBuzzerEnabled(coordinator)])


class SmartGharHubBuzzerEnabled(
    CoordinatorEntity[SmartGharCoordinator], SwitchEntity
):
    """Master enable for the hub's physical buzzer.

    Off mutes every alert except the boot tone. State reflects the
    `master_enable` field from /api/v1/hub/buzzer; setting via async_turn_on
    /off PUTs back to the same endpoint.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "buzzer_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_buzzer_enabled"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    @property
    def is_on(self) -> bool | None:
        return bool(self.coordinator.buzzer.get("master_enable", False))

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.buzzer_available

    async def _set(self, on: bool) -> None:
        await self.coordinator.client.put_buzzer({"master_enable": on})
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
