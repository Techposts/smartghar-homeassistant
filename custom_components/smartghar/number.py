"""Number entities — editable per-tank capacity + hub LED brightness +
diagnostic settings (TX sleep, samples, LoRa power) + Auvra speaker
config (max volume, speaker offset).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_KIND_SPEAKER,
    DEVICE_KIND_TANK,
    DOMAIN,
    MODEL_SPEAKER,
    MODEL_TANK,
)
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    # Hub-level: LED brightness slider.
    entities.append(SmartGharHubLedBrightness(coordinator))
    # Per-tank: capacity slider + diagnostic power-tuning numbers.
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_TANK:
            entities.append(SmartGharTankCapacity(coordinator, dev["id"]))
            entities.append(SmartGharTankSleepInterval(coordinator, dev["id"]))
            entities.append(SmartGharTankSampleCount(coordinator, dev["id"]))
            entities.append(SmartGharTankLoraPower(coordinator, dev["id"]))
        elif dev.get("kind") == DEVICE_KIND_SPEAKER:
            # Auvra: volume ceiling + AirPlay sync trim, both config knobs.
            entities.append(SmartGharSpeakerMaxVolume(coordinator, dev["id"]))
            entities.append(SmartGharSpeakerOffset(coordinator, dev["id"]))
    async_add_entities(entities)


class SmartGharTankCapacity(CoordinatorEntity[SmartGharCoordinator], NumberEntity):
    """Tank capacity in litres."""

    _attr_has_entity_name = True
    _attr_translation_key = "tank_capacity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_device_class = NumberDeviceClass.VOLUME_STORAGE
    _attr_native_min_value = 50.0
    _attr_native_max_value = 50000.0
    _attr_native_step = 50.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:water"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_capacity"
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
    def native_value(self) -> float | None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        return (dev.get("config") or {}).get("capacity_l")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.update_device(self._tank_id, {"capacity_l": value})
        await self.coordinator.async_request_refresh()


# ─── Diagnostic per-tank tuning numbers ───────────────────────────────────────
#
# These are advanced power/range knobs. Wrong values can shorten battery life
# or lose LoRa link, so they sit under EntityCategory.DIAGNOSTIC — visible to
# power users in HA's diagnostic section, hidden from casual dashboards.

class _TankConfigNumberBase(CoordinatorEntity[SmartGharCoordinator], NumberEntity):
    """Per-tank config-field number that propagates via PUT /api/v1/devices/<id>."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_mode = NumberMode.BOX
    _config_field: str = ""  # subclass overrides

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_{self._config_field}"
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
    def native_value(self) -> float | None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        return (dev.get("config") or {}).get(self._config_field)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.update_device(
            self._tank_id, {self._config_field: self._coerce(value)}
        )
        await self.coordinator.async_request_refresh()

    def _coerce(self, value: float) -> Any:
        return value


class SmartGharTankSleepInterval(_TankConfigNumberBase):
    """TX sleep interval in seconds — how often the transmitter wakes to send.

    Longer = better battery life, slower readings. 180 s is the default.
    """

    _config_field = "sleep_s"
    _attr_translation_key = "tank_sleep_interval"
    _attr_native_min_value = 60
    _attr_native_max_value = 3600
    _attr_native_step = 30
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer-sand"

    def _coerce(self, value: float) -> int:
        return int(value)


class SmartGharTankSampleCount(_TankConfigNumberBase):
    """How many sensor reads the TX averages per wake. 1–10."""

    _config_field = "samples"
    _attr_translation_key = "tank_samples"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_icon = "mdi:counter"

    def _coerce(self, value: float) -> int:
        return int(value)


class SmartGharTankLoraPower(_TankConfigNumberBase):
    """LoRa transmit power 1–22 dBm. Higher = more range + battery drain."""

    _config_field = "lora_pwr"
    _attr_translation_key = "tank_lora_power"
    _attr_native_min_value = 1
    _attr_native_max_value = 22
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dBm"
    _attr_icon = "mdi:radio-tower"

    def _coerce(self, value: float) -> int:
        return int(value)


# ─── Auvra speaker config numbers ─────────────────────────────────────────────


class _SpeakerConfigNumberBase(CoordinatorEntity[SmartGharCoordinator], NumberEntity):
    """Per-speaker config-field number written with the flat PUT
    /api/v1/devices/<id> {"<field>": <int>} and read back from `config`."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _config_field: str = ""  # subclass overrides

    def __init__(self, coordinator: SmartGharCoordinator, device_id: int) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_speaker_{device_id}_{self._config_field}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._device_id) or {
            "kind": DEVICE_KIND_SPEAKER, "id": self._device_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_SPEAKER,
            fallback_name=f"Speaker {self._device_id}",
        )

    @property
    def native_value(self) -> float | None:
        dev = self.coordinator.device_by_id(self._device_id)
        if not dev:
            return None
        return (dev.get("config") or {}).get(self._config_field)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._device_id) is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.update_device(
            self._device_id, {self._config_field: int(value)}
        )
        await self.coordinator.async_request_refresh()


class SmartGharSpeakerMaxVolume(_SpeakerConfigNumberBase):
    """Volume ceiling the speaker will not exceed, 50–100 %."""

    _config_field = "max_vol"
    _attr_translation_key = "max_volume"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"


class SmartGharSpeakerOffset(_SpeakerConfigNumberBase):
    """AirPlay sync trim in milliseconds (−250…+250, 5 ms steps).

    Nudges this speaker earlier (negative) or later (positive) relative
    to the other AirPlay receivers in a multi-room group.
    """

    _config_field = "sync_trim_ms"
    _attr_translation_key = "speaker_offset"
    _attr_native_min_value = -250
    _attr_native_max_value = 250
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:tune"


class SmartGharHubLedBrightness(CoordinatorEntity[SmartGharCoordinator], NumberEntity):
    """Hub LED brightness.

    TankSync / AmbiSense hubs expose the raw 0–255 strip scale; Auvra's
    /api/v1/hub/led speaks 0–100 (maps to its light on / dim / off).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_led_brightness"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    @property
    def native_max_value(self) -> float:
        if self.coordinator.info.get("product") == "auvra":
            return 100
        return 255

    @property
    def native_value(self) -> float | None:
        return self.coordinator.led.get("brightness")

    @property
    def available(self) -> bool:
        # If LED endpoint never returned a value (older firmware, or LED not
        # initialised), mark unavailable rather than show a stale 0.
        return super().available and "brightness" in self.coordinator.led

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.put_led({"brightness": int(value)})
        await self.coordinator.async_request_refresh()
