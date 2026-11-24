"""Select entities — hub buzzer volume profile (rx-v2.8.4+) and the Auvra
speaker's output channel + EQ preset (schema 1.2).

Single hub-level select exposing the global Quiet / Standard / Loud
profile. Applies uniformly to every alert — per-alert profiles
were dropped in rx-v2.8.0 in favor of a single global volume knob.

Auvra: `channel` (stereo / left / right / mono; for a stereo pair set one
speaker Left and one Right, then pick both in AirPlay) and `eq_preset`.
Both exist only when /api/v1/info lists the `eq` capability.
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
from .speaker_entity import SmartGharSpeakerConfigEntity, speaker_ids_with
from .speaker_logic import CAP_EQ, CHANNEL_OPTIONS, current_eq_preset, eq_preset_ids
from .voice_entity import (
    SmartGharPipelineSelect,
    SmartGharVadSelect,
    VoiceSelect,
    voice_entities_wanted,
    voice_settings_for,
)
from .voice_options import voice_satellite_enabled

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
    entities: list[SelectEntity] = []
    if coordinator.buzzer_available:
        entities.append(SmartGharHubBuzzerVolume(coordinator))
    for device_id in speaker_ids_with(coordinator, "channel"):
        entities.append(SmartGharSpeakerChannel(coordinator, device_id))
    for device_id in speaker_ids_with(coordinator, CAP_EQ):
        entities.append(SmartGharSpeakerEqPreset(coordinator, device_id))
    entities.extend(VoiceSelect(coordinator, s) for s in voice_settings_for(coordinator, "select"))
    if voice_entities_wanted(coordinator) and voice_satellite_enabled(entry):
        entities.append(SmartGharPipelineSelect(hass, coordinator))
        entities.append(SmartGharVadSelect(hass, coordinator))
    async_add_entities(entities)


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


class SmartGharSpeakerChannel(SmartGharSpeakerConfigEntity, SelectEntity):
    """Which channel this speaker plays: stereo, left, right or mono."""

    _key = "channel"
    _capability = "channel"
    _attr_translation_key = "channel"
    _attr_icon = "mdi:speaker-multiple"
    _attr_options = CHANNEL_OPTIONS

    @property
    def current_option(self) -> str | None:
        value = self._value("channel")
        if isinstance(value, str) and value.lower() in CHANNEL_OPTIONS:
            return value.lower()
        return None

    async def async_select_option(self, option: str) -> None:
        await self._put({"channel": option})


class SmartGharSpeakerEqPreset(SmartGharSpeakerConfigEntity, SelectEntity):
    """EQ preset (Flat, Bass, Vocal, Bookshelf, Soundbar, Night, ...)."""

    _key = "eq_preset"
    _capability = CAP_EQ
    _attr_translation_key = "eq_preset"
    _attr_icon = "mdi:equalizer"

    @property
    def options(self) -> list[str]:
        opts = eq_preset_ids(self._state)
        current = current_eq_preset(self._state)
        # A preset outside the list ("custom" after a manual bass change) is
        # shown rather than blanked.
        if current and current not in opts:
            opts = [*opts, current]
        return opts

    @property
    def current_option(self) -> str | None:
        return current_eq_preset(self._state)

    async def async_select_option(self, option: str) -> None:
        await self._put({"eq": {"preset": option}})
