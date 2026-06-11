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

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, switch_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    # Only register the buzzer switch if the hub responded to /api/v1/hub/buzzer
    # at first refresh. Older firmware (rx-v2.7.x) lacks the endpoint and we
    # don't want to clutter HA with a permanently-unavailable entity.
    if coordinator.buzzer_available:
        entities.append(SmartGharHubBuzzerEnabled(coordinator))
    # One relay switch per paired Smart Switch. Toggling engages the hub's
    # manual-hold (automation pauses); the per-switch "Resume automation"
    # button (button.py) hands control back to the pump rule.
    for sw in coordinator.switches:
        if "address" in sw:
            entities.append(SmartGharPumpRelay(coordinator, sw["address"]))
    async_add_entities(entities)


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


class SmartGharPumpRelay(CoordinatorEntity[SmartGharCoordinator], SwitchEntity):
    """The relay on a paired Smart Switch (the pump on/off).

    `is_on` reflects the device-confirmed relay state (`relay_on` from the
    switch's SWSTAT), not just the commanded state, so HA shows the truth even
    if the switch hasn't acknowledged yet. Toggling engages the hub's manual-
    hold; use the "Resume automation" button to return to the pump rule.
    """

    _attr_has_entity_name = True
    _attr_name = None  # the relay IS the device — use the device name
    _attr_icon = "mdi:water-pump"
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: SmartGharCoordinator, addr: int) -> None:
        super().__init__(coordinator)
        self._addr = addr
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_switch_{addr}_relay"

    @property
    def _sw(self) -> dict[str, Any] | None:
        return self.coordinator.switch_by_addr(self._addr)

    @property
    def available(self) -> bool:
        sw = self._sw
        # "waiting" = paired but no telemetry yet; relay state unknown.
        return super().available and sw is not None and sw.get("state") != "waiting"

    @property
    def device_info(self) -> DeviceInfo:
        sw = self._sw or {}
        return switch_device_info(self.coordinator, self._addr, sw.get("name"))

    @property
    def is_on(self) -> bool | None:
        sw = self._sw
        return bool(sw.get("relay_on")) if sw else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sw = self._sw or {}
        rule = sw.get("rule", {})
        return {
            "commanded": bool(sw.get("relay_desired")),
            "automation_paused": bool(rule.get("manual_hold")),
            "automation_enabled": bool(rule.get("enabled")),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_switch_relay(self._addr, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_switch_relay(self._addr, False)
        await self.coordinator.async_request_refresh()
