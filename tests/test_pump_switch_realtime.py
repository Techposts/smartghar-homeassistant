"""Pump Switch relay: optimistic state, fast confirm, 2 s switch refresh."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from .fixtures import V12_INFO, v12_devices

ENT = "switch.auvra_bedroom"
ADDR = 7


def _sw(on: bool) -> list[dict]:
    return [{"address": ADDR, "name": "Pump", "state": "online", "relay_on": on,
             "relay_desired": on, "rule": {}}]


async def _setup(hass, setup_auvra, first):
    with patch("custom_components.smartghar.api.SmartGharHubClient.get_switches",
               AsyncMock(return_value=first)):
        entry = await setup_auvra(V12_INFO, v12_devices(active=True, playing=False, sender="Mac"))
    return hass.data["smartghar"][entry.entry_id]


async def test_turn_on_is_optimistic_then_confirmed(hass: HomeAssistant, setup_auvra) -> None:
    coord = await _setup(hass, setup_auvra, _sw(False))
    assert hass.states.get(ENT).state == "off"
    reads = {"n": 0}

    async def get_switches():
        reads["n"] += 1
        return _sw(reads["n"] >= 3)   # confirms on the 3rd read

    set_relay = AsyncMock(return_value={"ok": True})
    with patch.object(coord.client, "get_switches", get_switches), \
         patch.object(coord.client, "set_switch_relay", set_relay), \
         patch.object(coord, "async_request_refresh", AsyncMock()) as refresh, \
         patch("custom_components.smartghar.switch.SWITCH_CONFIRM_POLL_S", 0.01):
        await hass.services.async_call("switch", "turn_on", {"entity_id": ENT}, blocking=True)
        # Shown at once, before any confirmation read.
        assert hass.states.get(ENT).state == "on"
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
        assert reads["n"] == 3
        assert hass.states.get(ENT).state == "on"
        assert coord.switch_by_addr(ADDR)["relay_on"] is True
        refresh.assert_not_called()
    set_relay.assert_awaited_once_with(ADDR, True)


async def test_unconfirmed_command_times_out_and_reverts(hass: HomeAssistant, setup_auvra, caplog) -> None:
    coord = await _setup(hass, setup_auvra, _sw(False))
    with patch.object(coord.client, "get_switches", AsyncMock(return_value=_sw(False))), \
         patch.object(coord.client, "set_switch_relay", AsyncMock(return_value={"ok": True})), \
         patch("custom_components.smartghar.switch.SWITCH_CONFIRM_POLL_S", 0.01), \
         patch("custom_components.smartghar.switch.SWITCH_CONFIRM_TIMEOUT_S", 0.1):
        await hass.services.async_call("switch", "turn_on", {"entity_id": ENT}, blocking=True)
        assert hass.states.get(ENT).state == "on"
        await asyncio.sleep(0.4)
        await hass.async_block_till_done()
    assert hass.states.get(ENT).state == "off"
    assert "did not confirm" in caplog.text


async def test_switch_tick_updates_state_without_stacking(hass: HomeAssistant, setup_auvra) -> None:
    coord = await _setup(hass, setup_auvra, _sw(False))
    gate = asyncio.Event()
    calls = {"n": 0}

    async def slow():
        calls["n"] += 1
        await gate.wait()
        return _sw(True)

    with patch.object(coord.client, "get_switches", slow):
        t1 = asyncio.create_task(coord._switch_tick())
        await asyncio.sleep(0)
        await coord._switch_tick()          # skipped: one still in flight
        assert calls["n"] == 1
        gate.set()
        await t1
    await hass.async_block_till_done()
    assert hass.states.get(ENT).state == "on"
