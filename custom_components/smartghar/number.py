"""Number entities — editable per-tank capacity + hub LED brightness."""
from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_KIND_TANK,
    DOMAIN,
    MANUFACTURER,
    MODEL_HUB,
    MODEL_TANK,
)
from .coordinator import SmartGharCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    # Hub-level: LED brightness slider.
    entities.append(SmartGharHubLedBrightness(coordinator))
    # Per-tank: capacity slider in litres.
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_TANK:
            entities.append(SmartGharTankCapacity(coordinator, dev["id"]))
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
        dev = self.coordinator.device_by_id(self._tank_id) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.hub_id}_tank_{self._tank_id}")},
            via_device=(DOMAIN, self.coordinator.hub_id),
            manufacturer=MANUFACTURER,
            model=MODEL_TANK,
            name=dev.get("name") or f"Tank {self._tank_id}",
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


class SmartGharHubLedBrightness(CoordinatorEntity[SmartGharCoordinator], NumberEntity):
    """Hub LED strip brightness (0–255)."""

    _attr_has_entity_name = True
    _attr_translation_key = "led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_led_brightness"

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
