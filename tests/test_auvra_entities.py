"""Auvra schema 1.2 config entities: channel, EQ preset, bass, treble, loudness."""
from __future__ import annotations

import copy

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import calls
from .fixtures import HUB_ID, V12_INFO, v12_devices


def _eid(hass: HomeAssistant, domain: str, suffix: str) -> str:
    eid = er.async_get(hass).async_get_entity_id(
        domain, "smartghar", f"smartghar_{HUB_ID}_speaker_1_{suffix}"
    )
    assert eid is not None, (domain, suffix)
    return eid


async def test_entities_read_state(hass, setup_auvra) -> None:
    await setup_auvra(
        V12_INFO,
        v12_devices(channel="left",
                    eq={"preset": "bass", "bass_db": 6, "treble_db": -2, "loudness": False}),
    )
    channel = hass.states.get(_eid(hass, "select", "channel"))
    assert channel.state == "left"
    assert channel.attributes["options"] == ["stereo", "left", "right", "mono"]
    preset = hass.states.get(_eid(hass, "select", "eq_preset"))
    assert preset.state == "bass"
    assert preset.attributes["options"] == [
        "flat", "bass", "vocal", "bookshelf", "soundbar", "night",
    ]
    bass = hass.states.get(_eid(hass, "number", "bass"))
    assert float(bass.state) == 6
    assert bass.attributes["min"] == -12 and bass.attributes["max"] == 12
    assert float(hass.states.get(_eid(hass, "number", "treble")).state) == -2
    assert hass.states.get(_eid(hass, "switch", "loudness")).state == "off"


async def test_device_reported_range(hass, setup_auvra) -> None:
    devices = v12_devices(eq={"preset": "flat", "bass_db": 0, "treble_db": 0,
                              "loudness": True, "range_db": [-6, 6]})
    await setup_auvra(V12_INFO, devices)
    bass = hass.states.get(_eid(hass, "number", "bass"))
    assert bass.attributes["min"] == -6 and bass.attributes["max"] == 6


async def test_custom_preset_shown(hass, setup_auvra) -> None:
    await setup_auvra(
        V12_INFO,
        v12_devices(eq={"preset": "custom", "bass_db": 3, "treble_db": 0, "loudness": True}),
    )
    preset = hass.states.get(_eid(hass, "select", "eq_preset"))
    assert preset.state == "custom"
    assert preset.attributes["options"][-1] == "custom"


@pytest.mark.parametrize(
    ("domain", "suffix", "service", "data", "body"),
    [
        ("select", "channel", "select_option", {"option": "right"}, {"channel": "right"}),
        ("select", "eq_preset", "select_option", {"option": "vocal"}, {"eq": {"preset": "vocal"}}),
        ("number", "bass", "set_value", {"value": -4}, {"eq": {"bass_db": -4}}),
        ("number", "treble", "set_value", {"value": 7}, {"eq": {"treble_db": 7}}),
        ("switch", "loudness", "turn_off", {}, {"eq": {"loudness": False}}),
        ("switch", "loudness", "turn_on", {}, {"eq": {"loudness": True}}),
    ],
)
async def test_entity_writes(
    hass, setup_auvra, aioclient_mock, domain, suffix, service, data, body
) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: _eid(hass, domain, suffix), **data}, blocking=True
    )
    assert calls(aioclient_mock, "PUT", "/api/v1/devices/1") == [body]


async def test_no_eq_capability_no_entities(hass, setup_auvra) -> None:
    info = copy.deepcopy(V12_INFO)
    info["capabilities"] = ["media_player", "airplay2", "radio", "url_play", "announce"]
    await setup_auvra(info, v12_devices())
    reg = er.async_get(hass)
    for domain, suffix in (("select", "channel"), ("select", "eq_preset"),
                           ("number", "bass"), ("number", "treble"),
                           ("switch", "loudness")):
        assert reg.async_get_entity_id(
            domain, "smartghar", f"smartghar_{HUB_ID}_speaker_1_{suffix}"
        ) is None


async def test_channel_capability_alone(hass, setup_auvra) -> None:
    info = copy.deepcopy(V12_INFO)
    info["capabilities"] = ["media_player", "airplay2", "channel"]
    await setup_auvra(info, v12_devices())
    assert hass.states.get(_eid(hass, "select", "channel")).state == "stereo"
    assert er.async_get(hass).async_get_entity_id(
        "select", "smartghar", f"smartghar_{HUB_ID}_speaker_1_eq_preset"
    ) is None


async def test_rejected_key_raises(hass, setup_auvra, aioclient_mock) -> None:
    from homeassistant.exceptions import HomeAssistantError

    from .fixtures import BASE

    await setup_auvra(V12_INFO, v12_devices())
    aioclient_mock.clear_requests()
    aioclient_mock.put(f"{BASE}/api/v1/devices/1", json={"ok": False, "rejected": ["eq"]})
    with pytest.raises(HomeAssistantError, match="rejected eq"):
        await hass.services.async_call(
            "number", "set_value",
            {ATTR_ENTITY_ID: _eid(hass, "number", "bass"), "value": 5}, blocking=True,
        )


from .fixtures import V12_CONFIG as ANNOUNCE_CONFIG  # rc7 as built: streams true, 8, 15


async def test_announce_settings_and_streams(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices(ANNOUNCE_CONFIG))
    floor = hass.states.get(_eid(hass, "number", "announce_min_volume"))
    duck = hass.states.get(_eid(hass, "number", "announce_duck_db"))
    assert float(floor.state) == 8 and floor.attributes["max"] == 100
    assert float(duck.state) == 15 and duck.attributes["max"] == 40
    assert hass.states.get(_eid(hass, "switch", "streams")).state == "on"

    for domain, service, eid, data in (
        ("number", "set_value", _eid(hass, "number", "announce_min_volume"), {"value": 25}),
        ("number", "set_value", _eid(hass, "number", "announce_duck_db"), {"value": 20}),
        ("switch", "turn_off", _eid(hass, "switch", "streams"), {}),
    ):
        await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: eid, **data}, blocking=True)
    assert calls(aioclient_mock, "PUT", "/api/v1/devices/1") == [
        {"announce_min_volume": 25}, {"announce_duck_db": 20}, {"streams": False},
    ]


async def test_announce_settings_need_capability_and_keys(hass, setup_auvra) -> None:
    """Firmware before rc7 kept these keys in /api/set only: no v1 config keys, no entities.
    Without the announce capability the numbers stay away even with keys."""
    await setup_auvra(V12_INFO, v12_devices())
    reg = er.async_get(hass)
    for domain, suffix in (("number", "announce_min_volume"), ("number", "announce_duck_db"),
                           ("switch", "streams")):
        assert reg.async_get_entity_id(
            domain, "smartghar", f"smartghar_{HUB_ID}_speaker_1_{suffix}") is None


async def test_streams_off_keeps_switch_drops_announce(hass, setup_auvra) -> None:
    info = copy.deepcopy(V12_INFO)
    info["capabilities"] = ["media_player", "airplay2", "eq", "channel"]  # streams off
    await setup_auvra(info, v12_devices({**ANNOUNCE_CONFIG, "streams": False}))
    assert hass.states.get(_eid(hass, "switch", "streams")).state == "off"
    assert er.async_get(hass).async_get_entity_id(
        "number", "smartghar", f"smartghar_{HUB_ID}_speaker_1_announce_min_volume") is None
