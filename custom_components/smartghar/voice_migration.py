"""One Auvra, one device: deal with a stock Wyoming entry for the same unit.

The firmware sends pipeline events to the connection that said `run-satellite`,
so two HA clients on one satellite fight. While an enabled stock `wyoming`
config entry points at this unit, the SmartGhar satellite stays off and a
repair issue offers to remove that entry. Nothing is ever removed without the
user confirming in the repair flow (see repairs.py).
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

WYOMING_DOMAIN = "wyoming"
ISSUE_PREFIX = "stock_wyoming_"


def issue_id(entry: ConfigEntry) -> str:
    return f"{ISSUE_PREFIX}{entry.entry_id}"


@callback
def find_stock_wyoming(
    hass: HomeAssistant, host: str, port: int, name: str
) -> list[ConfigEntry]:
    """Enabled stock Wyoming entries that drive this unit.

    Match: same host and port, or the same title (the satellite name HA took from
    the unit) on the same port, which covers a stored IP against our hostname.
    """
    found = []
    for wy in hass.config_entries.async_entries(WYOMING_DOMAIN):
        if wy.disabled_by is not None or wy.data.get("port") != port:
            continue
        same_host = wy.data.get("host") == host
        same_name = bool(name) and wy.title.casefold() == name.casefold()
        if same_host or same_name:
            found.append(wy)
    return found


@callback
def async_sync_stock_wyoming(
    hass: HomeAssistant, entry: ConfigEntry, host: str, port: int, name: str
) -> bool:
    """Create or clear the repair issue. True while a conflicting entry exists."""
    conflicts = find_stock_wyoming(hass, host, port, name)
    iid = issue_id(entry)
    if not conflicts:
        ir.async_delete_issue(hass, DOMAIN, iid)
        return False

    ent_reg = er.async_get(hass)
    stock_satellites = [
        e.entity_id
        for wy in conflicts
        for e in er.async_entries_for_config_entry(ent_reg, wy.entry_id)
        if e.domain == "assist_satellite"
    ]
    ir.async_create_issue(
        hass,
        DOMAIN,
        iid,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stock_wyoming",
        translation_placeholders={"name": name or host},
        data={
            "entry_id": entry.entry_id,
            "wyoming_entry_ids": [wy.entry_id for wy in conflicts],
            "stock_satellite_entity_ids": stock_satellites,
        },
    )
    return True
