"""Voice-ready: expose a curated set to Assist with aliases, and undo it cleanly.

A ledger (HA Store `smartghar.voice_ready`) records, for every entity we touched,
whether it was exposed before and which aliases we added. An entity already in
the ledger is not touched again, so a user who later un-exposes one by hand keeps
that choice. Reverting walks the ledger only: nothing outside it is changed.

The same ledger remembers which units had `wyoming_advertise` turned off by us,
so the stock Wyoming discovery can be given back on rollback.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .api import SmartGharApiError, SmartGharCannotConnect
from .const import DEVICE_KIND_TANK, DOMAIN
from .coordinator import SmartGharCoordinator
from .voice_logic import (
    KEY_ADVERTISE,
    dedupe_aliases,
    last_fill_aliases,
    level_aliases,
    next_arrival_aliases,
    pump_aliases,
    speaker_aliases,
    used_today_aliases,
    used_week_aliases,
    voice_supported,
    volume_aliases,
)
from .voice_options import voice_ready_enabled, voice_satellite_enabled

_LOGGER = logging.getLogger(__name__)
ASSISTANT = "conversation"
STORE_VERSION = 1
STORE_KEY = "smartghar.voice_ready"


def _store(hass: HomeAssistant) -> Store:
    return Store(hass, STORE_VERSION, STORE_KEY)


async def _load(hass: HomeAssistant) -> dict[str, Any]:
    data = await _store(hass).async_load() or {}
    data.setdefault("entities", {})
    data.setdefault("advertise_off", [])
    return data


def wanted_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SmartGharCoordinator
) -> dict[str, list[str]]:
    """entity_id -> aliases to add, for what the registry holds for this entry."""
    hub = coordinator.hub_id
    prefix = f"smartghar_{hub}_"
    tanks = {
        d["id"]: d.get("name") or f"Tank {d['id']}"
        for d in coordinator.devices
        if d.get("kind") == DEVICE_KIND_TANK
    }
    single = len(tanks) == 1
    pumps = {
        sw["address"]: sw.get("name") or "Pump"
        for sw in coordinator.switches
        if "address" in sw
    }
    pump_single = len(pumps) == 1
    unit_name = coordinator.info.get("hub_name") or "Auvra"

    out: dict[str, list[str]] = {}
    for ent in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        uid = ent.unique_id
        if not uid.startswith(prefix):
            continue
        rest = uid[len(prefix):]
        if rest.startswith("tank_"):
            tid_s, _, kind = rest[len("tank_"):].partition("_")
            if not tid_s.isdigit() or int(tid_s) not in tanks:
                continue
            name = tanks[int(tid_s)]
            makers = {
                "level_pct": level_aliases,
                "water_volume": volume_aliases,
                "used_today": used_today_aliases,
                "used_week": used_week_aliases,
                "last_fill": last_fill_aliases,
            }
            if kind in makers:
                out[ent.entity_id] = makers[kind](name, single)
        elif rest.startswith("switch_") and rest.endswith("_relay"):
            addr = rest[len("switch_"):-len("_relay")]
            if addr.isdigit() and int(addr) in pumps:
                out[ent.entity_id] = pump_aliases(pumps[int(addr)], pump_single)
        elif rest == "next_arrival":
            out[ent.entity_id] = next_arrival_aliases()
        elif rest.startswith("speaker_") and rest.endswith("_media_player"):
            out[ent.entity_id] = speaker_aliases(unit_name)
    return out


def hidden_entities(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Entities Assist must not control: HA exposes switches and buttons by default.

    "Mute the microphone" by voice cannot be undone by voice, and an LLM that can
    write the voice settings can break the unit. Only voice-ready entries do this.
    """
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if any(e.unique_id.endswith(f"_voice_{k}") for k in _NEVER_EXPOSE)
    ]


_NEVER_EXPOSE = ("mic_mute", "voice_enabled", "talk", "voice_tone", "voice_aec", "wyoming_advertise")


async def async_apply_voice_ready(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SmartGharCoordinator
) -> None:
    """Expose and alias what is not in the ledger yet."""
    data = await _load(hass)
    ledger: dict[str, Any] = data["entities"]
    reg = er.async_get(hass)
    changed = False
    wanted = wanted_entities(hass, entry, coordinator)
    hidden = set(hidden_entities(hass, entry))
    wanted.update({eid: [] for eid in hidden_entities(hass, entry) if eid not in wanted})
    for entity_id, aliases in wanted.items():
        if entity_id in ledger:
            continue
        ent = reg.async_get(entity_id)
        if ent is None:
            continue
        added = dedupe_aliases(sorted(a for a in ent.aliases if isinstance(a, str)), aliases)
        ledger[entity_id] = {
            "entry_id": entry.entry_id,
            "was_exposed": async_should_expose(hass, ASSISTANT, entity_id),
            "aliases": added,
        }
        async_expose_entity(hass, ASSISTANT, entity_id, entity_id not in hidden)
        if added:
            reg.async_update_entity(entity_id, aliases=set(ent.aliases) | set(added))
        changed = True
    if changed:
        await _store(hass).async_save(data)


async def async_revert_voice_ready(hass: HomeAssistant, entry_id: str) -> None:
    """Put exposure and aliases back the way the ledger says they were."""
    data = await _load(hass)
    ledger: dict[str, Any] = data["entities"]
    reg = er.async_get(hass)
    mine = [eid for eid, rec in ledger.items() if rec.get("entry_id") == entry_id]
    for entity_id in mine:
        rec = ledger.pop(entity_id)
        ent = reg.async_get(entity_id)
        if ent is None:
            continue
        async_expose_entity(hass, ASSISTANT, entity_id, bool(rec.get("was_exposed")))
        ours = {a.casefold() for a in rec.get("aliases", [])}
        keep = {a for a in ent.aliases if not isinstance(a, str) or a.casefold() not in ours}
        if keep != set(ent.aliases):
            reg.async_update_entity(entity_id, aliases=keep)
    if mine:
        await _store(hass).async_save(data)


async def _set_advertise(coordinator: SmartGharCoordinator, on: bool) -> bool:
    try:
        resp = await coordinator.client.set_keys({KEY_ADVERTISE: on})
    except (SmartGharApiError, SmartGharCannotConnect) as err:
        _LOGGER.warning("Could not set %s=%s on the unit: %s", KEY_ADVERTISE, on, err)
        return False
    return not (isinstance(resp, dict) and resp.get("rejected"))


async def async_sync_advertise(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SmartGharCoordinator
) -> None:
    """Satellite on: stock Wyoming discovery off (once). Satellite off: give it back."""
    voice = coordinator.voice
    if not voice_supported(voice):
        return
    data = await _load(hass)
    off: list[str] = data["advertise_off"]
    hub = coordinator.hub_id
    if voice_satellite_enabled(entry):
        if voice.get("advertise") and await _set_advertise(coordinator, False):
            if hub not in off:
                off.append(hub)
                await _store(hass).async_save(data)
    elif hub in off:
        if await _set_advertise(coordinator, True):
            off.remove(hub)
            await _store(hass).async_save(data)


async def async_restore_advertise(
    hass: HomeAssistant, coordinator: SmartGharCoordinator
) -> None:
    """On removal: turn advertising back on if we turned it off."""
    data = await _load(hass)
    if coordinator.hub_id in data["advertise_off"] and await _set_advertise(coordinator, True):
        data["advertise_off"].remove(coordinator.hub_id)
        await _store(hass).async_save(data)


async def async_sync_voice(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SmartGharCoordinator
) -> None:
    """Called after the platforms are up, on every setup and reload."""
    if voice_ready_enabled(entry):
        await async_apply_voice_ready(hass, entry, coordinator)
    else:
        await async_revert_voice_ready(hass, entry.entry_id)
    await async_sync_advertise(hass, entry, coordinator)


__all__ = [
    "DOMAIN",
    "async_restore_advertise",
    "async_revert_voice_ready",
    "async_sync_voice",
]
