"""Device API fixtures shaped exactly like the v1 contract.

V11_* are verbatim copies of what an Auvra on firmware 0.9.2 (schema 1.1)
answered on 18 Nov 2026 (GET /api/v1/info and /api/v1/devices).
V12_* add the schema 1.2 fields from the G5 contract
(smartghar-airplay-speaker docs/notes/night-2026-11-18.md).
"""
from __future__ import annotations

import copy
from typing import Any

HOST = "192.168.0.130"
HUB_ID = "auvra_b81f3fd80848"
BASE = f"http://{HOST}"

V11_INFO: dict[str, Any] = {
    "schema_version": "1.1",
    "manufacturer": "SmartGhar",
    "product": "auvra",
    "model": "Auvra",
    "fw_version": "0.9.2",
    "hub_id": HUB_ID,
    "hub_name": "Auvra Bedroom",
    "topology": "standalone",
    "uptime_s": 3112,
    "wifi_rssi": -18,
    "ota": {"current": "0.9.2", "available": False, "latest": "", "channel": "stable"},
    "stream": {"ws_path": "/api/v1/stream"},
    "capabilities": ["media_player", "airplay2"],
}

V11_DEVICES: dict[str, Any] = {
    "devices": [
        {
            "id": 1,
            "kind": "speaker",
            "name": "Auvra Bedroom",
            "state": {
                "power": "on",
                "active": False,
                "playing": False,
                "sender": "",
                "volume": 24,
                "muted": False,
                "title": "",
                "artist": "",
                "album": "",
                "art_rev": 0,
                "position_ms": None,
                "duration_ms": None,
                "stall": False,
                "remote": False,
            },
            "config": {"max_vol": 85, "night": True, "sync_trim_ms": 0},
        }
    ]
}

V12_CAPS = ["media_player", "airplay2", "eq", "channel", "radio", "url_play", "announce"]
V12_CAPS_STREAMS_OFF = ["media_player", "airplay2", "eq", "channel"]

# v1 config keys added in firmware 0.10.0-rc7.
V12_CONFIG = {"streams": True, "announce_min_volume": 8, "announce_duck_db": 15}

V12_INFO: dict[str, Any] = {
    **copy.deepcopy(V11_INFO),
    "schema_version": "1.2",
    "fw_version": "0.10.0",
    "ota": {"current": "0.10.0", "available": False, "latest": "", "channel": "stable"},
    "capabilities": list(V12_CAPS),
}


def v12_devices(config_overrides: dict[str, Any] | None = None, **state_overrides: Any) -> dict[str, Any]:
    devices = copy.deepcopy(V11_DEVICES)
    devices["devices"][0]["config"].update(config_overrides or {})
    state = devices["devices"][0]["state"]
    state.update(
        {
            "source": "idle",
            "sources": ["airplay", "radio"],
            "station": None,
            "stream_title": "",
            "announcing": False,
            "channel": "stereo",
            "eq": {"preset": "flat", "bass_db": 0, "treble_db": 0, "loudness": True},
        }
    )
    state.update(state_overrides)
    return devices


# The firmware ships no bundled stations: /api/v1/radio/stations is the
# user's favourites and is empty until they add some. These two come from the
# firmware repo's test set (docs/notes/radio-test-stations.json), as a user
# would add them.
V12_STATIONS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Hits Of Bollywood",
        "url": "http://stream.zeno.fm/8ty8szwpwfeuv",
        "codec": "mp3",
        "bitrate": 128,
        "favicon": "https://zeno.fm/favicon.ico",
    },
    {"id": 2, "name": "Radio Gaana", "url": "https://sp14.instainternet.com/8044/stream"},
]
LOGO_URL = "https://zeno.fm/favicon.ico"
