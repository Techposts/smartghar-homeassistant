"""A poll that was in flight when a WS push landed must not undo the push.

Seen on the real device (e2e, 18 Nov 2026): after media_play, HA's refresh GET
was answered before the sender resumed (playing=false), the WS snapshot with
playing=true arrived 130 ms before that GET finished, and the stale poll
overwrote it until the next 30 s poll.
"""
from __future__ import annotations

import copy
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from .fixtures import V12_INFO, v12_devices

MP = "media_player.auvra_bedroom"


async def test_push_during_poll_wins(hass: HomeAssistant, setup_auvra) -> None:
    entry = await setup_auvra(V12_INFO, v12_devices(active=True, playing=False, sender="Mac"))
    coordinator = hass.data["smartghar"][entry.entry_id]
    assert hass.states.get(MP).state == "paused"

    pushed = v12_devices(active=True, playing=True, sender="Mac")
    stale = v12_devices(active=True, playing=False, sender="Mac")
    real_get_devices = coordinator.client.get_devices

    async def get_devices_with_push():
        coordinator._handle_ws_msg({"kind": "snapshot", "hub": {}, **copy.deepcopy(pushed)})
        await real_get_devices()  # keep the HTTP path exercised
        return stale["devices"]

    with patch.object(coordinator.client, "get_devices", get_devices_with_push):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(MP).state == "playing"

    # Without a push, the poll result is applied as usual.
    with patch.object(coordinator.client, "get_devices", return_value=stale["devices"]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(MP).state == "paused"


async def test_capabilities_from_ws_snapshot_drop_features_at_once(hass: HomeAssistant, setup_auvra) -> None:
    """rc7 pushes hub.capabilities in every snapshot: switching streams off
    removes PLAY_MEDIA etc. without waiting for the 30 s poll, and the entry
    reloads so the announce entities go away."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F
    from homeassistant.helpers import entity_registry as er

    from .fixtures import HUB_ID, V12_CAPS_STREAMS_OFF, V12_CONFIG

    entry = await setup_auvra(V12_INFO, v12_devices(V12_CONFIG))
    coordinator = hass.data["smartghar"][entry.entry_id]
    assert hass.states.get(MP).attributes["supported_features"] & F.PLAY_MEDIA
    assert hass.states.get("number.auvra_bedroom_announcement_minimum_volume") is not None

    off = v12_devices({**V12_CONFIG, "streams": False})
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        coordinator._handle_ws_msg({
            "kind": "snapshot",
            "hub": {"uptime_s": 5, "capabilities": list(V12_CAPS_STREAMS_OFF)},
            **copy.deepcopy(off),
        })
        await hass.async_block_till_done()
    features = hass.states.get(MP).attributes["supported_features"]
    assert not features & F.PLAY_MEDIA
    assert not features & F.MEDIA_ANNOUNCE
    assert features & F.SELECT_SOUND_MODE
    reload.assert_called_once_with(entry.entry_id)
    assert er.async_get(hass).async_get_entity_id(
        "switch", "smartghar", f"smartghar_{HUB_ID}_speaker_1_streams") is not None
