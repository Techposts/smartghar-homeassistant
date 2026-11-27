"""Pump history sensors and time-rule attributes."""
from __future__ import annotations

import copy
from unittest.mock import AsyncMock, patch

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from custom_components.smartghar.const import DOMAIN

from .fixtures import BASE, HUB_ID
from .fixtures_pump import (
    INFO,
    LIVE_DEVICES,
    LIVE_HISTORY_EMPTY,
    LIVE_SWITCHES,
    history_no_clock,
    history_runs,
    switches_online,
    switches_with_time_rules,
)

WATER_UID = f"smartghar_{HUB_ID}_switch_1_pump_water_today"

RELAY = "switch.smart_switch_bad8"


def _sensor(hass, key):
    reg = er.async_get(hass)
    uid = f"smartghar_{HUB_ID}_switch_1_{key}"
    eid = reg.async_get_entity_id("sensor", DOMAIN, uid)
    return eid, (hass.states.get(eid) if eid else None)


def _hist_calls(aioclient_mock):
    return [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(f"{BASE}/api/history/pump")]


async def test_old_hub_without_endpoint_has_no_history_entities(hass, setup_auvra, aioclient_mock):
    coord_entry = await setup_auvra(INFO, LIVE_DEVICES, switches=switches_online())  # history 404
    coord = hass.data[DOMAIN][coord_entry.entry_id]
    assert not coord.pump_history_available
    for key in ("pump_energy_today", "pump_runtime_today", "pump_starts_today",
                "pump_water_today", "pump_dry_stops_7d", "pump_last_run"):
        assert _sensor(hass, key)[0] is None
    # the switch itself is unaffected, and no time-rule attributes appear
    attrs = hass.states.get(RELAY).attributes
    assert "commanded" in attrs
    assert "in_allowed_hours" not in attrs and "next_topup" not in attrs
    # probed once, not on every 30 s poll
    await coord.async_refresh()
    assert len(_hist_calls(aioclient_mock)) == 1


async def test_live_hub_with_empty_history(hass, setup_auvra):
    await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=LIVE_HISTORY_EMPTY)
    assert _sensor(hass, "pump_energy_today")[1].state == "0.0"
    assert _sensor(hass, "pump_runtime_today")[1].state == "0.0"
    assert _sensor(hass, "pump_starts_today")[1].state == "0"
    assert _sensor(hass, "pump_dry_stops_7d")[1].state == "0"
    assert _sensor(hass, "pump_water_today")[1].state == "0.0"  # Tank 3 capacity 1000 L
    assert _sensor(hass, "pump_last_run")[1].state == "unknown"


async def test_hub_with_history(hass, setup_auvra):
    await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_runs())
    e = _sensor(hass, "pump_energy_today")[1]
    assert e.state == "1.5"
    assert e.attributes["device_class"] == "energy"
    assert e.attributes["state_class"] == "total_increasing"
    assert e.attributes["unit_of_measurement"] == "kWh"
    r = _sensor(hass, "pump_runtime_today")[1]
    assert r.state == "40.0" and r.attributes["unit_of_measurement"] == "min"
    assert r.attributes["state_class"] == "total_increasing"
    assert _sensor(hass, "pump_starts_today")[1].state == "2"
    # 7 days to 27 Nov: 26th (1) + 24th (2); the 20th (4) is outside
    assert _sensor(hass, "pump_dry_stops_7d")[1].state == "3"
    # finished runs today: 30 -> 55 (250 L) and 20 -> 30 (100 L); the open run is not counted
    w = _sensor(hass, "pump_water_today")[1]
    assert w.state == "350.0" and w.attributes["device_class"] == "water"
    assert w.attributes["state_class"] == "total_increasing"
    last = _sensor(hass, "pump_last_run")[1]
    # newest finished run: 06:30 IST start + 1500 s = 06:55 IST = 01:25 UTC
    assert last.state.startswith("2026-11-27T01:25:00")
    a = last.attributes
    assert a["stopped_because"] == "allowed hours ended"
    assert a["started_by"] == "automatic (tank level)"
    assert a["duration_min"] == 25.0 and a["energy_kwh"] == 0.9
    assert (a["tank_start_pct"], a["tank_end_pct"], a["water_l"]) == (30, 55, 250.0)


async def test_water_sensor_only_when_capacity_known(hass, setup_auvra):
    devices = copy.deepcopy(LIVE_DEVICES)
    del devices["devices"][0]["config"]["capacity_l"]
    await setup_auvra(INFO, devices, switches=LIVE_SWITCHES, history=history_runs())
    assert _sensor(hass, "pump_water_today")[0] is None
    assert _sensor(hass, "pump_energy_today")[0] is not None


async def test_no_clock_makes_daily_sensors_unavailable(hass, setup_auvra):
    await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_no_clock())
    assert _sensor(hass, "pump_energy_today")[1].state == "unavailable"
    assert _sensor(hass, "pump_last_run")[1].state == "unknown"


async def test_time_rule_attributes(hass, setup_auvra):
    await setup_auvra(INFO, LIVE_DEVICES, switches=switches_with_time_rules(),
                      history=LIVE_HISTORY_EMPTY)
    a = hass.states.get(RELAY).attributes
    assert a["in_allowed_hours"] is True
    assert a["next_auto_start"] is None
    assert a["next_topup"].startswith("2026-11-28T00:00:00")
    assert a["time_wait"] == "waiting for 06:00"
    assert a["time_rules_paused"] is False


async def test_energy_resets_at_new_day_and_stays_total_increasing(hass, setup_auvra):
    entry = await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_runs())
    coord = hass.data[DOMAIN][entry.entry_id]
    eid, st = _sensor(hass, "pump_energy_today")
    assert st.state == "1.5"
    # Next local day: the hub has no row for it yet, and the 27th is now yesterday.
    next_day = history_runs(today=20261128)
    with patch.object(coord.client, "get_pump_history", AsyncMock(return_value=next_day)):
        await coord._history_tick()
    await hass.async_block_till_done()
    st = hass.states.get(eid)
    assert st.state == "0.0"
    assert st.attributes["state_class"] == "total_increasing"
    assert _sensor(hass, "pump_starts_today")[1].state == "0"
    assert _sensor(hass, "pump_water_today")[1].state == "0.0"
    # first run of the new day grows the total from 0
    d = history_runs(today=20261128)
    d["days"].insert(0, {"d": 20261128, "a": 1, "s": 600, "wh": 400.0, "n": 1, "dry": 0, "f": 0})
    with patch.object(coord.client, "get_pump_history", AsyncMock(return_value=d)):
        await coord._history_tick()
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "0.4"


async def test_water_total_never_drops_within_a_day(hass, setup_auvra):
    entry = await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_runs())
    coord = hass.data[DOMAIN][entry.entry_id]
    eid, st = _sensor(hass, "pump_water_today")
    assert st.state == "350.0"
    only_newest = history_runs()
    only_newest["runs"] = only_newest["runs"][:2]  # the older run fell out of the window
    with patch.object(coord.client, "get_pump_history", AsyncMock(return_value=only_newest)):
        await coord._history_tick()
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "350.0"


async def test_one_shared_fetch_and_no_polling_without_a_switch(hass, setup_auvra, aioclient_mock):
    entry = await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_runs())
    coord = hass.data[DOMAIN][entry.entry_id]
    assert len(_hist_calls(aioclient_mock)) == 1   # probe, not one per entity
    await coord._history_tick()
    await coord._history_tick()
    assert len(_hist_calls(aioclient_mock)) == 3   # one fetch per tick, six entities share it
    # a busy fetch is skipped
    coord._history_busy = True
    await coord._history_tick()
    assert len(_hist_calls(aioclient_mock)) == 3
    coord._history_busy = False

    # no paired switch: nothing is asked, nothing created
    no_sw = await setup_auvra_no_switch(hass, aioclient_mock)
    assert no_sw == 0


async def setup_auvra_no_switch(hass, aioclient_mock):
    from homeassistant.const import CONF_HOST
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import mock_device
    from .fixtures import HOST

    await hass.config_entries.async_unload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    mock_device(aioclient_mock, INFO, LIVE_DEVICES, switches={"switches": []}, history=history_runs())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=HUB_ID + "x", title="n",
                            data={CONF_HOST: HOST, "hub_id": HUB_ID + "x"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = hass.data[DOMAIN][entry.entry_id]
    await coord._history_tick()
    await coord.async_refresh()
    return len(_hist_calls(aioclient_mock))


def _restore_water(hass, litres, day, run_ids):
    """Pre-register the water sensor and give it a stored state, as after an HA restart."""
    eid = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, WATER_UID, suggested_object_id="water_today_restored"
    ).entity_id
    mock_restore_cache_with_extra_data(
        hass,
        [(
            State(eid, str(litres)),
            {
                "native_value": litres,
                "native_unit_of_measurement": "L",
                "day": day,
                "run_ids": run_ids,
            },
        )],
    )
    return eid


def _run_starts(hist):
    return [r["t"] for r in hist["runs"] if not r["open"]]


async def test_water_survives_restart_when_hub_returns_fewer_runs(hass, setup_auvra):
    full = history_runs()
    ids = _run_starts(full)
    eid = _restore_water(hass, 350.0, 20261127, ids)
    fewer = history_runs()
    fewer["runs"] = [fewer["runs"][0], fewer["runs"][1]]  # morning run fell out
    entry = await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=fewer)
    assert hass.states.get(eid).state == "350.0"
    # a genuinely new run today adds on top, never below the restored value
    coord = hass.data[DOMAIN][entry.entry_id]
    newer = history_runs()
    newer["runs"] = [dict(newer["runs"][1], t=newer["runs"][1]["t"] + 600, t0=55, t1=60)]
    with patch.object(coord.client, "get_pump_history", AsyncMock(return_value=newer)):
        await coord._history_tick()
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "400.0"


async def test_water_restore_on_new_day_resets_to_zero(hass, setup_auvra):
    eid = _restore_water(hass, 350.0, 20261127, _run_starts(history_runs()))
    await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=history_runs(today=20261128))
    assert hass.states.get(eid).state == "0.0"


async def test_water_no_double_count_after_restart(hass, setup_auvra):
    full = history_runs()
    eid = _restore_water(hass, 350.0, 20261127, _run_starts(full))
    await setup_auvra(INFO, LIVE_DEVICES, switches=LIVE_SWITCHES, history=full)
    assert hass.states.get(eid).state == "350.0"
