"""SmartGhar integration for Home Assistant.

Local-first integration that talks to SmartGhar Hubs (TankSync, PowerSync, ...)
over the home LAN via the documented HTTP/WebSocket protocol. Never reaches
out to any cloud service.

v0.1.0 ships polling-based read-only sensors. WebSocket real-time push +
bidirectional control land in v0.2.0+ once firmware Phase 1.2/1.3 are tagged.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartGharHubClient
from .const import CONF_LOCAL_TOKEN, DOMAIN
from .coordinator import SmartGharCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartGhar from a config entry."""
    hass.data.setdefault(DOMAIN, {})

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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
