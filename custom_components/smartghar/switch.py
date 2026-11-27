"""Switch entities — hub buzzer master enable (rx-v2.8.4+), Smart Switch
relays, and the Auvra speaker's night mode.

The buzzer switch is a single hub-level switch that mutes every alert
except the boot tone. The boot tone always plays on power-up regardless of
this switch — that's intentional, it confirms the hub is initialized.

Per-alert switches (Critical-low / Overflow / Sensor-offline) are
intentionally NOT exposed here. They live on the hub's local web UI
under the "Advanced" collapse. HA's per-tank sensor_error +
sensor_stuck binary sensors already give automation surfaces for the
specific conditions if a user wants to script around alert behavior.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CONNECTION,
    CONNECTION_USB,
    DEVICE_KIND_SPEAKER,
    DOMAIN,
    MODEL_SPEAKER,
)
from .serial_entity import SmartGharSerialNodeEntity, async_add_serial_nodes
from .speaker_entity import SmartGharSpeakerConfigEntity, speaker_ids_with
from .speaker_logic import CAP_EQ
from .api import SmartGharApiError
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info, switch_device_info

_LOGGER = logging.getLogger(__name__)

SWITCH_CONFIRM_TIMEOUT_S = 6.0
SWITCH_CONFIRM_POLL_S = 0.3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get(CONF_CONNECTION) == CONNECTION_USB:
        _async_setup_serial_switches(hass, entry, async_add_entities)
        return

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
    # Auvra speaker: night mode (config flag on the device).
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_SPEAKER:
            entities.append(SmartGharSpeakerNightMode(coordinator, dev["id"]))
    # Auvra EQ loudness (schema 1.2, capability `eq`).
    for device_id in speaker_ids_with(coordinator, CAP_EQ):
        entities.append(SmartGharSpeakerLoudness(coordinator, device_id))
    # Radio and streams on/off: shown whenever v1 config carries `streams`,
    # because turning it off removes the radio/url_play/announce capabilities.
    for device_id in speaker_ids_with(coordinator, None, "streams"):
        entities.append(SmartGharSpeakerStreams(coordinator, device_id))
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
        # Commanded state shown until the switch confirms (or the deadline).
        self._pending: bool | None = None
        self._confirm_task: asyncio.Task | None = None

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
        if self._pending is not None:
            return self._pending
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
        await self._command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(False)

    async def _command(self, on: bool) -> None:
        self._cancel_confirm()
        await self.coordinator.client.set_switch_relay(self._addr, on)
        # Show the target at once; the hub only learns the real state ~0.1 s
        # later, so an immediate refresh would read the old one and flip back.
        self._pending = on
        self.async_write_ha_state()
        self._confirm_task = self.hass.async_create_background_task(
            self._confirm(on), name=f"smartghar_switch_confirm_{self._addr}"
        )

    def _cancel_confirm(self) -> None:
        if self._confirm_task and not self._confirm_task.done():
            self._confirm_task.cancel()
        self._confirm_task = None

    async def _confirm(self, on: bool) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SWITCH_CONFIRM_TIMEOUT_S
        confirmed = False
        try:
            while loop.time() < deadline:
                try:
                    switches = await self.coordinator.client.get_switches()
                except SmartGharApiError:
                    switches = None
                if switches is not None:
                    self.coordinator.merge_switches(switches)
                    sw = next((s for s in switches if s.get("address") == self._addr), None)
                    if sw is not None and bool(sw.get("relay_on")) == on:
                        confirmed = True
                        break
                await asyncio.sleep(SWITCH_CONFIRM_POLL_S)
        except asyncio.CancelledError:
            raise  # a newer command owns the pending state now
        if not confirmed:
            _LOGGER.warning(
                "Pump switch %s did not confirm %s within %.0f s",
                self._addr, "ON" if on else "OFF", SWITCH_CONFIRM_TIMEOUT_S,
            )
        self._pending = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_confirm()
        await super().async_will_remove_from_hass()


class SmartGharSpeakerNightMode(CoordinatorEntity[SmartGharCoordinator], SwitchEntity):
    """Auvra night mode — the `night` flag in the speaker's config.

    Read from /api/v1/devices config.night; written with the flat PUT
    /api/v1/devices/<id> {"night": true|false} like the other speaker knobs.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "night"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: SmartGharCoordinator, device_id: int) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_speaker_{device_id}_night"
        )

    @property
    def _dev(self) -> dict[str, Any] | None:
        return self.coordinator.device_by_id(self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev or {"kind": DEVICE_KIND_SPEAKER, "id": self._device_id}
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_SPEAKER,
            fallback_name=f"Speaker {self._device_id}",
        )

    @property
    def available(self) -> bool:
        return super().available and self._dev is not None

    @property
    def is_on(self) -> bool | None:
        dev = self._dev
        if not dev:
            return None
        config = dev.get("config") or {}
        return bool(config["night"]) if "night" in config else None

    async def _set(self, on: bool) -> None:
        await self.coordinator.client.update_device(self._device_id, {"night": on})
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SmartGharSpeakerLoudness(SmartGharSpeakerConfigEntity, SwitchEntity):
    """Auvra loudness compensation (state.eq.loudness), schema 1.2."""

    _key = "loudness"
    _capability = CAP_EQ
    _attr_translation_key = "loudness"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:volume-plus"

    @property
    def is_on(self) -> bool | None:
        value = self._eq_value("loudness")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._put({"eq": {"loudness": True}})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._put({"eq": {"loudness": False}})


class SmartGharSpeakerStreams(SmartGharSpeakerConfigEntity, SwitchEntity):
    """Auvra radio, URL playback and announcements (v1 config `streams`).

    Turning it off makes the device drop the radio / url_play / announce
    capabilities; the integration then reloads and those features go away.
    """

    _key = "streams"
    _capability = None
    _config_key = "streams"
    _attr_translation_key = "streams"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:radio"

    @property
    def is_on(self) -> bool | None:
        value = ((self._dev or {}).get("config") or {}).get("streams")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._put({"streams": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._put({"streams": False})


# ─── USB-CDC coordinator ("HA stick") pump/relay switch ─────────────────────

class SmartGharSerialPumpSwitch(SmartGharSerialNodeEntity, SwitchEntity):
    """Smart Switch relay over the serial protocol (proto v1 `pump` command).

    Optimistic OFF→pending: the ack confirms the hub accepted the command; the
    real state lands with the next SWSTAT-driven telemetry echo. Hub-side
    safety automation keeps precedence — a refusal surfaces as a logged error
    (code=conflict) and the state snaps back on the next echo.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_name = "Pump"

    def __init__(self, coordinator, node_id: int) -> None:
        super().__init__(coordinator, node_id)
        hub_id = coordinator.hub.get("device_id", "unknown")
        self._attr_unique_id = f"{hub_id}-node{node_id}-relay"

    @property
    def is_on(self) -> bool | None:
        val = self.sensor_value("relay")
        return None if val is None else bool(val)

    async def _set(self, on: bool) -> None:
        link = self.coordinator.link
        if link is None:
            raise HomeAssistantError("Serial hub is not connected")
        try:
            await link.set_pump(self._node_id, on)
        except Exception as err:  # noqa: BLE001 — includes conflict refusals
            raise HomeAssistantError(f"Hub refused pump command: {err}") from err

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


def _async_setup_serial_switches(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    def factory(node):
        if node.device_type != "switch":
            return []
        return [SmartGharSerialPumpSwitch(coordinator, node.node_id)]

    async_add_serial_nodes(coordinator, async_add_entities, factory)
