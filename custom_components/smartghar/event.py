"""Event entities — fill_complete (polling-derived in v0.3.0).

A fill event fires when a tank's level rises ≥5 percentage points between
two coordinator ticks. Real-time push via WebSocket lands in v0.4.0; until
then this is computed from polled state, which means very fast fills (<30s)
may miss but most tanker/municipal fills are slow enough to catch.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_KIND_TANK, DOMAIN, MODEL_TANK
from .device_info import subdevice_device_info
from .coordinator import SmartGharCoordinator

# Minimum jump in % between polls to count as a "fill" rather than noise.
FILL_THRESHOLD_PCT = 5.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[EventEntity] = []
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_TANK:
            entities.append(SmartGharFillEvent(coordinator, dev["id"]))
    async_add_entities(entities)


class SmartGharFillEvent(CoordinatorEntity[SmartGharCoordinator], EventEntity):
    """Fires when a tank's level jumps upward — i.e. someone refilled it."""

    _attr_has_entity_name = True
    _attr_translation_key = "fill_complete"
    _attr_event_types = ["fill_complete"]
    _attr_icon = "mdi:water-plus"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._last_level: float | None = None
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_fill_event"
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
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Seed the baseline on add so the first poll doesn't fire a spurious fill.
        dev = self.coordinator.device_by_id(self._tank_id)
        if dev:
            self._last_level = (dev.get("state") or {}).get("level_pct")

    @callback
    def _handle_coordinator_update(self) -> None:
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            super()._handle_coordinator_update()
            return

        level = (dev.get("state") or {}).get("level_pct")
        if level is None or self._last_level is None:
            self._last_level = level
            super()._handle_coordinator_update()
            return

        delta = float(level) - float(self._last_level)
        if delta >= FILL_THRESHOLD_PCT:
            cap_l = (dev.get("config") or {}).get("capacity_l")
            volume_l: float | None = None
            if cap_l:
                volume_l = round(cap_l * delta / 100.0, 1)
            self._trigger_event(
                "fill_complete",
                {
                    "from_pct": round(self._last_level, 1),
                    "to_pct": round(level, 1),
                    "delta_pct": round(delta, 1),
                    "volume_l": volume_l,
                    "tank_name": dev.get("name"),
                },
            )

        self._last_level = level
        super()._handle_coordinator_update()
