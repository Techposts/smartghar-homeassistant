"""Diagnostics support for SmartGhar.

Returns a redacted snapshot of the integration's state so users can attach
it to GitHub issues without leaking host IPs / hub_ids / tokens.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_HUB_ID, CONF_LOCAL_TOKEN, DOMAIN
from .coordinator import SmartGharCoordinator

REDACT_KEYS = {CONF_HOST, CONF_LOCAL_TOKEN, CONF_HUB_ID, "hub_id", "ip", "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnostics dump for one config entry (one hub)."""
    coordinator: SmartGharCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"error": "coordinator not initialised"}

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
            "options": dict(entry.options) if entry.options else {},
            "unique_id_redacted": entry.unique_id[:6] + "..." if entry.unique_id else None,
        },
        "coordinator": {
            "name": coordinator.name,
            "update_interval_s": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
        },
        "info": async_redact_data(coordinator.info, REDACT_KEYS),
        "devices": [
            async_redact_data(d, REDACT_KEYS) for d in coordinator.devices
        ],
        "led": coordinator.led,
    }
