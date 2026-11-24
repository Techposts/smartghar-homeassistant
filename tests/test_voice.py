"""Auvra voice: settings entities, writes, satellite gate and the stock-Wyoming repair."""
from __future__ import annotations

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartghar import voice_logic as vl
from custom_components.smartghar.const import DOMAIN
from custom_components.smartghar.voice_migration import find_stock_wyoming

from .conftest import calls
from .fixtures import HOST, V12_INFO, v12_devices

VOICE = {
    "mic": True, "enabled": True, "muted": False, "state": "idle",
    "ha_connected": False, "duck_db": 12, "wake_sens": "normal",
    "advertise": True, "tone": True, "tone_style": "A", "follow_up": "off",
}


def _state(**over):
    return {"voice": {**VOICE, **over}}


@pytest.fixture(autouse=True)
def _expose_nothing_by_default(hass, core_loaded):
    """So a test sees what voice-ready itself does, not HA's per-domain defaults."""
    hass.data["homeassistant.exposed_entities"].async_set_expose_new_entities("conversation", False)


def _eid(hass, platform, suffix):
    reg = er.async_get(hass)
    for e in reg.entities.values():
        if e.platform == DOMAIN and e.domain == platform and e.unique_id.endswith(f"_voice_{suffix}"):
            return e.entity_id
    return None


# ---------------------------------------------------------------- pure logic

def test_voice_supported_needs_mic():
    assert vl.voice_supported({"mic": True})
    assert not vl.voice_supported({"mic": False})
    assert not vl.voice_supported({})
    assert not vl.voice_supported(None)


def test_voice_port_default_and_reported():
    assert vl.voice_port({}) == 10700
    assert vl.voice_port({"port": 10800}) == 10800
    assert vl.voice_port({"port": "x"}) == 10700
    assert vl.voice_port({"port": True}) == 10700


def test_local_phrase_is_local_not_utc():
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 25, 9, 0, tzinfo=ist)
    when = datetime(2026, 11, 25, 2, 30, tzinfo=ZoneInfo("UTC")).astimezone(ist)
    assert vl.local_phrase(when, now) == "today at 8:00 AM"
    assert vl.local_phrase(datetime(2026, 11, 24, 18, 40, tzinfo=ist), now) == "yesterday at 6:40 PM"
    assert vl.local_phrase(datetime(2026, 11, 23, 19, 5, tzinfo=ist), now) == "Mon 23 Nov at 7:05 PM"
    assert vl.local_phrase(datetime(2026, 11, 26, 0, 5, tzinfo=ist), now) == "tomorrow at 12:05 AM"


def test_arrival_times():
    assert vl.parse_arrival_times("18:00, 06:30; bad, 25:99x") == [time(6, 30), time(18, 0)]
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 25, 19, 0, tzinfo=ist)
    nxt = vl.next_arrival([time(6, 30), time(18, 0)], now)
    assert nxt == datetime(2026, 11, 26, 6, 30, tzinfo=ist)
    assert vl.next_arrival([], now) is None


def test_aliases_single_vs_many_tanks():
    assert "water level" in vl.level_aliases("Main", single=True)
    assert "water level" not in vl.level_aliases("Main", single=False)
    assert vl.speaker_aliases("Auvra Bedroom")[:3] == ["Auvra Bedroom", "speaker", "स्पीकर"]
    assert "Auvra" in vl.speaker_aliases("Auvra Bedroom")
    assert vl.dedupe_aliases(["Speaker"], ["speaker", "Auvra"]) == ["Auvra"]


# ----------------------------------------------------------------- entities

async def test_no_voice_entities_without_voice_block(hass, setup_auvra):
    await setup_auvra(V12_INFO, v12_devices())
    assert _eid(hass, "switch", "mic_mute") is None
    assert _eid(hass, "assist_satellite", "satellite") is None


async def test_voice_entities_and_writes(hass, setup_auvra, aioclient_mock):
    await setup_auvra(V12_INFO, v12_devices(), state=_state())
    mute = _eid(hass, "switch", "mic_mute")
    assert mute and hass.states.get(mute).state == "off"
    assert _eid(hass, "switch", "voice_aec") is None  # firmware has no aec key
    follow = hass.states.get(_eid(hass, "select", "follow_up"))
    assert follow.state == "off"
    assert hass.states.get(_eid(hass, "number", "duck_db")).state == "12.0"

    await hass.services.async_call("switch", "turn_on", {"entity_id": mute}, blocking=True)
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": _eid(hass, "select", "tone_style"), "option": "C"}, blocking=True,
    )
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": _eid(hass, "number", "duck_db"), "value": 20}, blocking=True,
    )
    assert calls(aioclient_mock, "POST", "/api/set") == [
        {"wyoming_advertise": False},  # setup: stop stock Wyoming discovery
        {"mic_mute": True}, {"voice_tone_style": "C"}, {"duck_db": 20},
    ]


async def test_optional_aec_appears_when_reported(hass, setup_auvra):
    await setup_auvra(V12_INFO, v12_devices(), state=_state(aec=False))
    assert _eid(hass, "switch", "voice_aec")


async def test_rejected_key_raises(hass, setup_auvra, aioclient_mock):
    from homeassistant.exceptions import HomeAssistantError

    await setup_auvra(V12_INFO, v12_devices(), state=_state())
    with patch(
        "custom_components.smartghar.api.SmartGharHubClient.set_keys",
        return_value={"ok": False, "rejected": ["mic_mute"]},
    ), pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": _eid(hass, "switch", "mic_mute")}, blocking=True
        )


async def test_on_the_auvra_device(hass, setup_auvra):
    await setup_auvra(V12_INFO, v12_devices(), state=_state())
    reg = er.async_get(hass)
    mute = reg.async_get(_eid(hass, "switch", "mic_mute"))
    sat = reg.async_get(_eid(hass, "assist_satellite", "satellite"))
    media = next(e for e in reg.entities.values() if e.domain == "media_player")
    assert mute.device_id == sat.device_id == media.device_id


# --------------------------------------------------------- stock wyoming gate

def _stock(hass, host=HOST, port=10700, title="Auvra Bedroom", **kw):
    e = MockConfigEntry(domain="wyoming", title=title, data={"host": host, "port": port}, **kw)
    e.add_to_hass(hass)
    return e


async def test_find_stock_wyoming_matches(hass):
    a = _stock(hass)
    _stock(hass, host="1.2.3.4", title="Other", port=10700)
    _stock(hass, port=10300)  # a different port: another satellite
    off = _stock(hass, disabled_by=__import__("homeassistant.config_entries", fromlist=["x"]).ConfigEntryDisabler.USER)
    found = find_stock_wyoming(hass, HOST, 10700, "Auvra Bedroom")
    assert found == [a]
    assert off not in found


async def test_conflict_raises_repair_and_blocks_satellite(hass, setup_auvra):
    stock = _stock(hass)
    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stock_wyoming_{entry.entry_id}")
    assert issue and issue.is_fixable
    # never auto-deleted
    assert hass.config_entries.async_get_entry(stock.entry_id) is not None
    sat = hass.states.get(_eid(hass, "assist_satellite", "satellite"))
    assert sat.state == "unavailable"


async def test_repair_flow_removes_only_on_confirm(hass, setup_auvra):
    from custom_components.smartghar.repairs import async_create_fix_flow

    stock = _stock(hass)
    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    iid = f"stock_wyoming_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, iid)
    flow = await async_create_fix_flow(hass, iid, issue.data)
    flow.hass = hass
    form = await flow.async_step_init()
    assert form["type"] == "form"
    assert hass.config_entries.async_get_entry(stock.entry_id) is not None
    done = await flow.async_step_confirm({})
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(stock.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, iid) is None


# ------------------------------------------------------------- advertising

async def test_advertise_turned_off_once_and_restored_on_untick(hass, setup_auvra, aioclient_mock):
    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    assert calls(aioclient_mock, "POST", "/api/set") == [{"wyoming_advertise": False}]

    # untick "Voice assistant in this integration": advertising is given back
    hass.config_entries.async_update_entry(
        entry, options={"voice_satellite": False}
    )
    await hass.async_block_till_done()
    assert calls(aioclient_mock, "POST", "/api/set")[-1] == {"wyoming_advertise": True}


async def test_advertise_left_alone_when_already_off(hass, setup_auvra, aioclient_mock):
    await setup_auvra(V12_INFO, v12_devices(), state=_state(advertise=False))
    assert calls(aioclient_mock, "POST", "/api/set") == []


# -------------------------------------------------------------- voice-ready

def _speaker_entity(hass):
    reg = er.async_get(hass)
    return next(e for e in reg.entities.values() if e.domain == "media_player")


async def test_voice_ready_exposes_speaker_with_aliases(hass, setup_auvra):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    await setup_auvra(V12_INFO, v12_devices(), state=_state())
    ent = _speaker_entity(hass)
    assert async_should_expose(hass, "conversation", ent.entity_id)
    assert {"speaker", "Auvra", "Auvra Bedroom"} <= set(ent.aliases)


async def test_voice_ready_never_exposes_settings(hass, setup_auvra):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    await setup_auvra(V12_INFO, v12_devices(), state=_state())
    assert not async_should_expose(hass, "conversation", _eid(hass, "switch", "mic_mute"))


async def test_voice_ready_untick_reverts_and_keeps_user_aliases(hass, setup_auvra):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    reg = er.async_get(hass)
    eid = _speaker_entity(hass).entity_id
    reg.async_update_entity(eid, aliases=set(reg.async_get(eid).aliases) | {"my box"})

    hass.config_entries.async_update_entry(entry, options={"voice_ready": False})
    await hass.async_block_till_done()

    ent = reg.async_get(eid)
    assert not async_should_expose(hass, "conversation", eid)
    assert {a for a in ent.aliases if isinstance(a, str)} == {"my box"}


async def test_voice_ready_off_from_the_start_touches_nothing(hass, setup_auvra):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    await setup_auvra(V12_INFO, v12_devices(), state=_state(), options={"voice_ready": False})
    ent = _speaker_entity(hass)
    assert not async_should_expose(hass, "conversation", ent.entity_id)
    assert not [a for a in ent.aliases if isinstance(a, str)]


async def test_user_unexpose_survives_reload(hass, setup_auvra):
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
        async_should_expose,
    )

    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    eid = _speaker_entity(hass).entity_id
    async_expose_entity(hass, "conversation", eid, False)  # the user's choice
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", eid)


async def test_removal_reverts_and_restores_advertise(hass, setup_auvra, aioclient_mock):
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    eid = _speaker_entity(hass).entity_id
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", eid)
    assert calls(aioclient_mock, "POST", "/api/set")[-1] == {"wyoming_advertise": True}


# ------------------------------------------------------------- options flow

async def test_options_flow_defaults_on(hass, setup_auvra):
    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    keys = {str(k): k.default() for k in result["data_schema"].schema}
    assert keys == {"voice_ready": True, "voice_satellite": True}
    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {"voice_ready": False, "voice_satellite": True}
    )
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["voice_ready"] is False


async def test_default_exposed_settings_are_hidden_from_assist(hass, setup_auvra):
    """HA exposes switches by default; the mic mute must not be voice-controllable."""
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose

    hass.data["homeassistant.exposed_entities"].async_set_expose_new_entities("conversation", True)
    entry = await setup_auvra(V12_INFO, v12_devices(), state=_state())
    mute = _eid(hass, "switch", "mic_mute")
    assert not async_should_expose(hass, "conversation", mute)
    assert async_should_expose(hass, "conversation", _speaker_entity(hass).entity_id)
    # untick: back to HA's default for them
    hass.config_entries.async_update_entry(entry, options={"voice_ready": False})
    await hass.async_block_till_done()
    assert async_should_expose(hass, "conversation", mute)
