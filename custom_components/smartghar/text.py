"""Text entities — editable tank name (per attached tank).

Edits propagate via PUT /api/v1/devices/<id>; the hub's existing config-sync
MQTT path then updates the cloud + PWA so the rename is reflected everywhere.
"""
from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_KIND_TANK, DOMAIN, MODEL_TANK
from .device_info import subdevice_device_info
from .coordinator import SmartGharCoordinator

# Hub firmware caps tank names at TX_NAME_MAX (16 chars) but enforces a
# stricter sanitised set on the device side. We mirror the length limit and
# let the hub reject bad characters with a 4xx; the user sees the error in HA.
TANK_NAME_MAX_LEN = 15


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TextEntity] = []
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_TANK:
            entities.append(SmartGharTankName(coordinator, dev["id"]))
    async_add_entities(entities)


class SmartGharTankName(CoordinatorEntity[SmartGharCoordinator], TextEntity):
    """User-editable tank name."""

    _attr_has_entity_name = True
    _attr_translation_key = "tank_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_max = TANK_NAME_MAX_LEN
    _attr_native_min = 1
    _attr_icon = "mdi:rename-box"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_name"
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
    def native_value(self) -> str | None:
        dev = self.coordinator.device_by_id(self._tank_id)
        return dev.get("name") if dev else None

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.client.update_device(self._tank_id, {"name": value})
        # Refresh so the new name appears across all entities + the device card.
        await self.coordinator.async_request_refresh()
