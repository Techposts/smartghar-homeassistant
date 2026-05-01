"""SmartGhar integration for Home Assistant.

Local-first integration that talks to SmartGhar Hubs (TankSync, PowerSync, ...)
over the home LAN via the documented HTTP/WebSocket protocol. Never reaches
out to any cloud service.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import SmartGharHubClient
from .const import CONF_LOCAL_TOKEN, DOMAIN
from .coordinator import SmartGharCoordinator

_LOGGER = logging.getLogger(__name__)

REFILL_MARKER_SCHEMA = vol.Schema({
    vol.Optional("tank"): cv.entity_id,
    vol.Optional("volume_l"): vol.Coerce(float),
    vol.Optional("source"): vol.In(["tanker", "municipal", "well", "rainwater", "other"]),
    vol.Optional("cost"): vol.Coerce(float),
    vol.Optional("note"): cv.string,
})

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.BUTTON,
    Platform.EVENT,
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartGhar from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_services(hass)

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
    coordinator: SmartGharCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.stop_ws()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
