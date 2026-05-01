"""SmartGhar integration for Home Assistant.

Local-first integration that talks to SmartGhar Hubs (TankSync, PowerSync, etc.)
over the home LAN via the documented HTTP/WebSocket protocol. Never reaches
out to any cloud service.

This file is a placeholder for the v0.0.1 scaffold. Functional integration
ships in v0.1.0 once hub firmware rx-v2.8.0 is released.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PLATFORMS: list[str] = []  # sensor, binary_sensor, light, button, number, text, event — added in v0.1.0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartGhar from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
