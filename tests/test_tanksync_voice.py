"""TankSync: spoken-friendly sensors and the voice-ready set."""
from __future__ import annotations

import copy

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.helpers import entity_registry as er

from custom_components.smartghar.const import DOMAIN

from .fixtures import HUB_ID

INFO = {
    "schema_version": "1.1", "manufacturer": "SmartGhar", "product": "tanksync",
    "model": "TankSync Hub", "fw_version": "2.9.0", "hub_id": HUB_ID,
    "hub_name": "TankSync Hub", "topology": "hub", "uptime_s": 10, "wifi_rssi": -40,
    "ota": {"current": "2.9.0", "available": False, "latest": "", "channel": "stable"},
    "stream": {"ws_path": "/api/v1/stream"}, "capabilities": [],
}


def _tank(tid=1, name="Main Tank", level=80.0):
    return {
        "id": tid, "kind": "tank", "name": name,
        "state": {"level_pct": level, "conn_state": "online", "voltage": 3.9, "rssi": -60},
        "config": {"capacity_l": 1000},
    }


@pytest.fixture(autouse=True)
def _expose_nothing_by_default(hass, core_loaded):
    hass.data["homeassistant.exposed_entities"].async_set_expose_new_entities("conversation", False)


def _by_suffix(hass, suffix):
    reg = er.async_get(hass)
    return next(
        (e for e in reg.entities.values() if e.platform == DOMAIN and e.unique_id.endswith(suffix)),
        None,
    )


async def _push(hass, entry, tid, level):
    coord = hass.data[DOMAIN][entry.entry_id]
    data = copy.deepcopy(coord.data)
    for d in data["devices"]:
        if d["id"] == tid:
            d["state"]["level_pct"] = level
    coord.async_set_updated_data(data)
    await hass.async_block_till_done()


async def test_used_today_and_last_fill(hass, setup_auvra):
    entry = await setup_auvra(INFO, {"devices": [_tank()]})
    today = hass.states.get(_by_suffix(hass, "tank_1_used_today").entity_id)
    assert today.state == "0.0"
    assert hass.states.get(_by_suffix(hass, "tank_1_last_fill").entity_id).state == "not seen yet"

    await _push(hass, entry, 1, 70.0)  # 10 % of 1000 L drained
    assert hass.states.get(today.entity_id).state == "100.0"
    week = hass.states.get(_by_suffix(hass, "tank_1_used_week").entity_id)
    assert week.state == "100.0"

    await _push(hass, entry, 1, 95.0)  # a fill
    fill = hass.states.get(_by_suffix(hass, "tank_1_last_fill").entity_id)
    assert fill.state.startswith("today at ")
    assert "timestamp" in fill.attributes and fill.attributes["hours_ago"] < 1


async def test_small_rise_is_not_a_fill(hass, setup_auvra):
    entry = await setup_auvra(INFO, {"devices": [_tank()]})
    await _push(hass, entry, 1, 81.0)
    assert hass.states.get(_by_suffix(hass, "tank_1_last_fill").entity_id).state == "not seen yet"


async def test_voice_ready_set_for_single_tank_and_pump(hass, setup_auvra):
    await setup_auvra(
        INFO, {"devices": [_tank()]},
        switches={"switches": [{"address": 7, "name": "Borewell Pump", "relay": False}]},
    )
    for suffix in ("tank_1_level_pct", "tank_1_water_volume", "tank_1_used_today",
                   "tank_1_used_week", "tank_1_last_fill", "switch_7_relay"):
        ent = _by_suffix(hass, suffix)
        assert async_should_expose(hass, "conversation", ent.entity_id), suffix
    level = _by_suffix(hass, "tank_1_level_pct")
    assert {"water level", "tank level"} <= set(level.aliases)
    assert "pump" in _by_suffix(hass, "switch_7_relay").aliases or "Borewell Pump" in _by_suffix(
        hass, "switch_7_relay").aliases
    # diagnostics stay out
    assert not async_should_expose(hass, "conversation", _by_suffix(hass, "tank_1_voltage").entity_id)
    assert not async_should_expose(hass, "conversation", _by_suffix(hass, "tank_1_consumption").entity_id)


async def test_generic_aliases_only_for_a_single_tank(hass, setup_auvra):
    await setup_auvra(
        INFO, {"devices": [_tank(1, "Roof"), _tank(2, "Underground")]},
    )
    aliases = set(_by_suffix(hass, "tank_1_level_pct").aliases)
    assert "water level" not in aliases and "Roof level" in aliases


async def test_next_arrival_only_with_times(hass, setup_auvra):
    await setup_auvra(INFO, {"devices": [_tank()]})
    assert _by_suffix(hass, "next_arrival") is None


async def test_next_arrival_with_times(hass, setup_auvra):
    await setup_auvra(
        INFO, {"devices": [_tank()]}, options={"water_arrival_times": "06:30, 18:00"}
    )
    ent = _by_suffix(hass, "next_arrival")
    state = hass.states.get(ent.entity_id)
    assert " at " in state.state and "in_minutes" in state.attributes
    assert async_should_expose(hass, "conversation", ent.entity_id)
