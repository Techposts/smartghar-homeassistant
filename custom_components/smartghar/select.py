"""Select entities — hub buzzer volume profile (rx-v2.8.4+).

Single hub-level select exposing the global Quiet / Standard / Loud
profile. Applies uniformly to every alert — per-alert profiles
were dropped in rx-v2.8.0 in favor of a single global volume knob.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info


# Profile labels must match the firmware enum (buzzer_profile_t in buzzer.h):
#   0 = BUZZ_PROFILE_QUIET
#   1 = BUZZ_PROFILE_STANDARD
#   2 = BUZZ_PROFILE_LOUD
BUZZER_PROFILES = ["Quiet", "Standard", "Loud"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.buzzer_available:
        async_add_entities([SmartGharHubBuzzerVolume(coordinator)])


class SmartGharHubBuzzerVolume(
    CoordinatorEntity[SmartGharCoordinator], SelectEntity
):
    """Global volume profile for the hub buzzer."""

    _attr_has_entity_name = True
    _attr_translation_key = "buzzer_volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:volume-high"
    _attr_options = BUZZER_PROFILES

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_buzzer_volume"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.buzzer_available

    @property
    def current_option(self) -> str | None:
        prof_idx = self.coordinator.buzzer.get("master_profile")
        if prof_idx is None:
            return None
        try:
            return BUZZER_PROFILES[int(prof_idx)]
        except (IndexError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        try:
            prof_idx = BUZZER_PROFILES.index(option)
        except ValueError:
            return  # ignore unknown options (HA shouldn't send any)
        await self.coordinator.client.put_buzzer({"master_profile": prof_idx})
        await self.coordinator.async_request_refresh()
