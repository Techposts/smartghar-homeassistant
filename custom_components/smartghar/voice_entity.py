"""Auvra voice settings as entities on the Auvra device.

Reads the unit's `voice` block (coordinator.voice, from /api/state); writes go to
POST /api/set. Entities exist only for a unit that reports voice support.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.assist_pipeline import AssistPipelineSelect, VadSensitivitySelect
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SmartGharApiError
from .assist_satellite import pipeline_changed_signal, voice_prefix
from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info
from .voice_logic import (
    DUCK_MAX,
    DUCK_MIN,
    KEY_TALK,
    SETTINGS,
    VoiceSetting,
    option_ids,
    setting_available,
    voice_supported,
)

# Settings the user touches every day stay out of the Configuration group.
_USER_FACING = {"mic_mute", "voice_enabled"}


def voice_entities_wanted(coordinator: SmartGharCoordinator) -> bool:
    return voice_supported(coordinator.voice)


async def write_voice_keys(coordinator: SmartGharCoordinator, fields: dict[str, Any]) -> None:
    """POST /api/set, raise on a rejected key, then re-read the unit's state."""
    try:
        resp = await coordinator.client.set_keys(fields)
    except SmartGharApiError as err:
        raise HomeAssistantError(f"Auvra did not accept {fields}: {err}") from err
    if isinstance(resp, dict) and resp.get("rejected"):
        raise HomeAssistantError(
            f"Auvra rejected {', '.join(map(str, resp['rejected']))}: {fields}"
        )
    await coordinator.async_request_refresh()


class _VoiceBase(CoordinatorEntity[SmartGharCoordinator]):
    """Common: Auvra hub device, availability from the voice block."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartGharCoordinator, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{voice_prefix(coordinator.hub_id)}_{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    @property
    def available(self) -> bool:
        return super().available and voice_supported(self.coordinator.voice)


class _VoiceSettingEntity(_VoiceBase):
    def __init__(self, coordinator: SmartGharCoordinator, setting: VoiceSetting) -> None:
        super().__init__(coordinator, setting.key)
        self._setting = setting
        self._attr_translation_key = setting.key
        if setting.key not in _USER_FACING:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def _raw(self) -> Any:
        return self.coordinator.voice.get(self._setting.read)

    @property
    def available(self) -> bool:
        return super().available and self._setting.read in self.coordinator.voice

    async def _write(self, value: Any) -> None:
        await write_voice_keys(self.coordinator, {self._setting.write: value})


class VoiceSwitch(_VoiceSettingEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        return None if self._raw is None else bool(self._raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)


class VoiceSelect(_VoiceSettingEntity, SelectEntity):
    def __init__(self, coordinator: SmartGharCoordinator, setting: VoiceSetting) -> None:
        super().__init__(coordinator, setting)
        self._attr_options = list(setting.options)

    @property
    def options(self) -> list[str]:
        # The wake word list comes from the unit (what its firmware carries).
        if self._setting.options_key:
            return option_ids(self.coordinator.voice, self._setting.options_key)
        return list(self._setting.options)

    @property
    def current_option(self) -> str | None:
        raw = self._raw
        return raw if isinstance(raw, str) and raw in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self._write(option)


class VoiceNumber(_VoiceSettingEntity, NumberEntity):
    _attr_native_min_value = DUCK_MIN
    _attr_native_max_value = DUCK_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL

    @property
    def native_value(self) -> float | None:
        raw = self._raw
        return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None

    async def async_set_native_value(self, value: float) -> None:
        await self._write(int(round(value)))


class VoiceTalkButton(_VoiceBase, ButtonEntity):
    """Push to talk. HA cannot start a conversation on a Wyoming satellite, so this is the way."""

    _attr_translation_key = "voice_talk"
    _attr_icon = "mdi:microphone-message"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator, "talk")

    @property
    def available(self) -> bool:
        voice = self.coordinator.voice
        return super().available and bool(voice.get("enabled")) and not voice.get("muted")

    async def async_press(self) -> None:
        # A press while a turn runs ends it (the same button on the unit's dial).
        busy = self.coordinator.voice.get("state") not in (None, "idle")
        await write_voice_keys(self.coordinator, {KEY_TALK: not busy})


class SmartGharPipelineSelect(AssistPipelineSelect):
    """Which Assist pipeline this Auvra uses ("Preferred" follows HA's default)."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, coordinator: SmartGharCoordinator) -> None:
        AssistPipelineSelect.__init__(
            self, hass, DOMAIN, voice_prefix(coordinator.hub_id)
        )
        self._hub_id = coordinator.hub_id
        self._coordinator = coordinator
        self._attr_device_info = hub_device_info(coordinator)

    async def async_select_option(self, option: str) -> None:
        await super().async_select_option(option)
        async_dispatcher_send(self.hass, pipeline_changed_signal(self._hub_id))


class SmartGharVadSelect(VadSensitivitySelect):
    """How long a pause ends the question."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, coordinator: SmartGharCoordinator) -> None:
        VadSensitivitySelect.__init__(self, hass, voice_prefix(coordinator.hub_id))
        self._hub_id = coordinator.hub_id
        self._attr_device_info = hub_device_info(coordinator)

    async def async_select_option(self, option: str) -> None:
        await super().async_select_option(option)
        async_dispatcher_send(self.hass, pipeline_changed_signal(self._hub_id))


def voice_settings_for(
    coordinator: SmartGharCoordinator, kind: str
) -> list[VoiceSetting]:
    if not voice_entities_wanted(coordinator):
        return []
    return [
        s for s in SETTINGS
        if s.kind == kind and setting_available(s, coordinator.voice)
    ]
