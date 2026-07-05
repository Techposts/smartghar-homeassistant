"""SmartGhar integration for Home Assistant.

Local-first integration that talks to SmartGhar Hubs (TankSync, PowerSync, ...)
over the home LAN via the documented HTTP/WebSocket protocol. Never reaches
out to any cloud service.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import SmartGharHubClient
from .const import (
    CONF_CONNECTION,
    CONF_LOCAL_TOKEN,
    CONF_SERIAL_PORT,
    CONNECTION_USB,
    DOMAIN,
)
from .coordinator import SmartGharCoordinator

_LOGGER = logging.getLogger(__name__)

REFILL_MARKER_SCHEMA = vol.Schema({
    vol.Optional("tank"): cv.entity_id,
    vol.Optional("volume_l"): vol.Coerce(float),
    vol.Optional("source"): vol.In(["tanker", "municipal", "well", "rainwater", "other"]),
    vol.Optional("cost"): vol.Coerce(float),
    vol.Optional("note"): cv.string,
})

TEST_BUZZER_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("event"): vol.All(vol.Coerce(int), vol.Range(min=0, max=13)),
    vol.Optional("profile"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
})

# USB-CDC coordinator entries expose the node-level surfaces only — hub-level
# HTTP things (OTA update entity, LED numbers, buzzer, …) don't exist on the
# serial protocol (v1).
SERIAL_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
]

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.UPDATE,
]


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-wide services. Idempotent."""
    if hass.services.has_service(DOMAIN, "refill_marker"):
        return

    async def _refill_marker(call: ServiceCall) -> None:
        """Fire a smartghar_refill_marker event with the user's metadata.

        Doesn't update consumption totals (that's auto-tracked from level
        deltas already). Pure logging surface — automations subscribe to
        the event for "refill happened with these properties" workflows.
        """
        event_data = {
            "tank": call.data.get("tank"),
            "volume_l": call.data.get("volume_l"),
            "source": call.data.get("source", "manual"),
            "cost": call.data.get("cost"),
            "note": call.data.get("note"),
            "logged_at": dt_util.utcnow().isoformat(),
        }
        # Drop None fields so the event payload is clean.
        event_data = {k: v for k, v in event_data.items() if v is not None}
        hass.bus.async_fire(f"{DOMAIN}_refill_marker", event_data)
        _LOGGER.info("Refill marker logged: %s", event_data)

    hass.services.async_register(
        DOMAIN, "refill_marker", _refill_marker, schema=REFILL_MARKER_SCHEMA
    )

    async def _test_buzzer(call: ServiceCall) -> None:
        """Preview a buzzer alert pattern on the hub that owns `entity_id`.

        Resolves entity_id → device → SmartGhar hub_id via the device
        registry, then dispatches to that hub's coordinator client.
        Bypasses master_enable + quiet hours on the firmware side (preview
        intent, not a real alert).
        """
        entity_id = call.data["entity_id"]
        event = call.data["event"]
        profile = call.data.get("profile")

        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        entity_entry = ent_reg.async_get(entity_id)
        if entity_entry is None or entity_entry.device_id is None:
            _LOGGER.warning("test_buzzer: entity %s not found / has no device", entity_id)
            return
        device_entry = dev_reg.async_get(entity_entry.device_id)
        if device_entry is None:
            _LOGGER.warning("test_buzzer: device for %s not found", entity_id)
            return

        # Walk the integration's coordinators and match by hub_id derived
        # from the device's identifiers. Sub-device identifiers carry the
        # hub_id prefix (see device_info._subdevice_identifier).
        target_coordinator: SmartGharCoordinator | None = None
        for coord in (hass.data.get(DOMAIN) or {}).values():
            if not isinstance(coord, SmartGharCoordinator):
                continue
            for domain, ident in device_entry.identifiers:
                if domain != DOMAIN:
                    continue
                # Hub-level identifier is exactly hub_id; sub-device
                # identifier starts with "<hub_id>_". Either matches.
                if ident == coord.hub_id or ident.startswith(coord.hub_id + "_"):
                    target_coordinator = coord
                    break
            if target_coordinator:
                break

        if target_coordinator is None:
            _LOGGER.warning("test_buzzer: no SmartGhar hub owns entity %s", entity_id)
            return

        try:
            await target_coordinator.client.test_buzzer(event=event, profile=profile)
            _LOGGER.info(
                "test_buzzer: event=%d profile=%s on hub %s",
                event, profile, target_coordinator.hub_id,
            )
        except Exception as err:  # noqa: BLE001 — surface to user via log
            _LOGGER.error("test_buzzer: hub %s rejected: %s", target_coordinator.hub_id, err)

    hass.services.async_register(
        DOMAIN, "test_buzzer", _test_buzzer, schema=TEST_BUZZER_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartGhar from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_services(hass)

    # USB-CDC coordinator ("HA stick"): a hub plugged into the HA machine
    # speaking the proto-v1 NDJSON serial protocol. No WiFi/HTTP involved;
    # entities are pushed from the serial stream.
    if entry.data.get(CONF_CONNECTION) == CONNECTION_USB:
        from .serial_coordinator import SmartGharSerialCoordinator

        serial_coordinator = SmartGharSerialCoordinator(
            hass, entry.data[CONF_SERIAL_PORT])
        try:
            await serial_coordinator.async_connect()
        except Exception as err:  # noqa: BLE001 — retryable setup failure
            raise ConfigEntryNotReady(
                f"Serial hub on {entry.data[CONF_SERIAL_PORT]}: {err}") from err
        hass.data[DOMAIN][entry.entry_id] = serial_coordinator
        await hass.config_entries.async_forward_entry_setups(entry, SERIAL_PLATFORMS)
        return True

    session = async_get_clientsession(hass)
    client = SmartGharHubClient(
        host=entry.data[CONF_HOST],
        session=session,
        token=entry.data.get(CONF_LOCAL_TOKEN),
    )

    hub_id = entry.unique_id or entry.data.get("hub_id", "unknown")
    coordinator = SmartGharCoordinator(hass, client, hub_id)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the WebSocket push channel after platforms are wired up so
    # entities exist by the time the first snapshot arrives.
    coordinator.start_ws()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.data.get(CONF_CONNECTION) == CONNECTION_USB:
        serial_coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if serial_coordinator is not None:
            await serial_coordinator.async_shutdown_link()
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, SERIAL_PLATFORMS)
        if unloaded:
            hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        return unloaded

    coordinator: SmartGharCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.stop_ws()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
