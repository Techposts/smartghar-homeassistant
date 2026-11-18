"""Auvra speaker: schema 1.1 devices unchanged, schema 1.2 media features.

Every 1.2 feature and entity is gated on /api/v1/info `capabilities`; the
1.1 tests pin that a 0.9.x device looks exactly as it did before.
"""
from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_EXTRA,
    ATTR_SOUND_MODE,
    ATTR_SOUND_MODE_LIST,
    DOMAIN as MP_DOMAIN,
    SERVICE_BROWSE_MEDIA,
    SERVICE_PLAY_MEDIA,
    SERVICE_SELECT_SOUND_MODE,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_source import PlayMedia
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotSupported
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartghar.media_player import SPEAKER_FEATURES

from .conftest import calls, mock_device
from .fixtures import (
    BASE,
    HUB_ID,
    V11_DEVICES,
    V11_INFO,
    V12_INFO,
    V12_STATIONS,
    v12_devices,
)

MP = "media_player.auvra_bedroom"
NEW_ENTITY_SUFFIXES = ("channel", "eq_preset", "bass", "treble", "loudness")


def _uid(suffix: str) -> str:
    return f"smartghar_{HUB_ID}_speaker_1_{suffix}"


def _entity_for(hass: HomeAssistant, domain: str, suffix: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, "smartghar", _uid(suffix))


def _info_with(caps: list[str]) -> dict:
    info = copy.deepcopy(V12_INFO)
    info["capabilities"] = caps
    return info


# ── 1.1 devices: exactly as before ─────────────────────────────────────────


async def test_v11_device_has_no_new_features_or_entities(hass, setup_auvra) -> None:
    await setup_auvra(V11_INFO, V11_DEVICES)

    state = hass.states.get(MP)
    assert state is not None
    assert state.state == "idle"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == SPEAKER_FEATURES
    for attr in (ATTR_INPUT_SOURCE_LIST, ATTR_SOUND_MODE_LIST, ATTR_INPUT_SOURCE, ATTR_SOUND_MODE):
        assert attr not in state.attributes
    assert "announcing" not in state.attributes
    assert set(state.attributes) >= {"sender", "stall", "art_rev"}

    reg = er.async_get(hass)
    for domain, suffix in (
        ("select", "channel"), ("select", "eq_preset"), ("number", "bass"),
        ("number", "treble"), ("switch", "loudness"),
    ):
        assert reg.async_get_entity_id(domain, "smartghar", _uid(suffix)) is None
    # Existing 0.12.0 speaker entities stay.
    assert _entity_for(hass, "number", "max_vol") is not None
    assert _entity_for(hass, "number", "sync_trim_ms") is not None
    assert _entity_for(hass, "switch", "night") is not None


async def test_v11_device_rejects_play_media(hass, setup_auvra) -> None:
    await setup_auvra(V11_INFO, V11_DEVICES)
    with pytest.raises(ServiceNotSupported):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_PLAY_MEDIA,
            {ATTR_ENTITY_ID: MP, ATTR_MEDIA_CONTENT_TYPE: "music",
             ATTR_MEDIA_CONTENT_ID: "http://example.com/a.mp3"},
            blocking=True,
        )


async def test_v11_transport_unchanged(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V11_INFO, V11_DEVICES)
    await hass.services.async_call(MP_DOMAIN, "media_pause", {ATTR_ENTITY_ID: MP}, blocking=True)
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [{"cmd": "pause"}]


# ── 1.2 media_player ───────────────────────────────────────────────────────


async def test_v12_features_sources_sound_modes(hass, setup_auvra) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    state = hass.states.get(MP)
    features = state.attributes[ATTR_SUPPORTED_FEATURES]
    for flag in (
        MediaPlayerEntityFeature.PLAY_MEDIA,
        MediaPlayerEntityFeature.MEDIA_ANNOUNCE,
        MediaPlayerEntityFeature.SELECT_SOURCE,
        MediaPlayerEntityFeature.BROWSE_MEDIA,
        MediaPlayerEntityFeature.SELECT_SOUND_MODE,
    ):
        assert features & flag, flag
    assert features & SPEAKER_FEATURES == SPEAKER_FEATURES
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == ["AirPlay", "Radio"]
    assert ATTR_INPUT_SOURCE not in state.attributes  # idle
    assert state.attributes[ATTR_SOUND_MODE] == "Flat"
    assert state.attributes[ATTR_SOUND_MODE_LIST] == [
        "Flat", "Bass", "Vocal", "Bookshelf", "Soundbar", "Night",
    ]
    assert state.attributes["announcing"] is False


@pytest.mark.parametrize(
    ("caps", "present", "absent"),
    [
        (["media_player", "airplay2", "eq"],
         [MediaPlayerEntityFeature.SELECT_SOUND_MODE],
         [MediaPlayerEntityFeature.PLAY_MEDIA, MediaPlayerEntityFeature.SELECT_SOURCE,
          MediaPlayerEntityFeature.MEDIA_ANNOUNCE, MediaPlayerEntityFeature.BROWSE_MEDIA]),
        (["media_player", "airplay2", "radio"],
         [MediaPlayerEntityFeature.PLAY_MEDIA, MediaPlayerEntityFeature.SELECT_SOURCE,
          MediaPlayerEntityFeature.BROWSE_MEDIA],
         [MediaPlayerEntityFeature.MEDIA_ANNOUNCE, MediaPlayerEntityFeature.SELECT_SOUND_MODE]),
        (["media_player", "airplay2", "url_play"],
         [MediaPlayerEntityFeature.PLAY_MEDIA, MediaPlayerEntityFeature.BROWSE_MEDIA],
         [MediaPlayerEntityFeature.MEDIA_ANNOUNCE, MediaPlayerEntityFeature.SELECT_SOURCE]),
        (["media_player", "airplay2", "url_play", "announce"],
         [MediaPlayerEntityFeature.MEDIA_ANNOUNCE],
         [MediaPlayerEntityFeature.SELECT_SOUND_MODE]),
    ],
)
async def test_feature_gates(hass, setup_auvra, caps, present, absent) -> None:
    await setup_auvra(_info_with(caps), v12_devices())
    features = hass.states.get(MP).attributes[ATTR_SUPPORTED_FEATURES]
    for flag in present:
        assert features & flag, flag
    for flag in absent:
        assert not features & flag, flag


async def test_radio_playing_state(hass, setup_auvra) -> None:
    await setup_auvra(
        V12_INFO,
        v12_devices(
            source="radio", playing=True, active=False, sender="",
            station={"id": 1, "name": "Hits Of Bollywood"}, stream_title="Hawa Hawai",
            title="Hawa Hawai", artist="Hits Of Bollywood", art_rev=0,
            position_ms=None, duration_ms=None,
        ),
    )
    state = hass.states.get(MP)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "Hawa Hawai"
    assert state.attributes["media_channel"] == "Hits Of Bollywood"
    assert state.attributes["media_content_type"] == "channel"
    assert state.attributes[ATTR_INPUT_SOURCE] == "Radio"
    assert state.attributes["app_name"] == "Radio"
    assert state.attributes["media_artist"] == "Hits Of Bollywood"
    assert "media_position" not in state.attributes


async def test_select_source(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_SELECT_SOURCE,
        {ATTR_ENTITY_ID: MP, ATTR_INPUT_SOURCE: "Radio"}, blocking=True,
    )
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"source": "radio", "cmd": "select_source"}
    ]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            MP_DOMAIN, SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: MP, ATTR_INPUT_SOURCE: "Bluetooth"}, blocking=True,
        )


async def test_select_sound_mode(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_SELECT_SOUND_MODE,
        {ATTR_ENTITY_ID: MP, ATTR_SOUND_MODE: "Night"}, blocking=True,
    )
    assert calls(aioclient_mock, "PUT", "/api/v1/devices/1") == [{"eq": {"preset": "night"}}]


async def test_device_presets_list_wins(hass, setup_auvra) -> None:
    devices = v12_devices(eq={"preset": "rock", "bass_db": 2, "treble_db": 0,
                              "loudness": False, "presets": ["flat", "rock"]})
    await setup_auvra(V12_INFO, devices)
    state = hass.states.get(MP)
    assert state.attributes[ATTR_SOUND_MODE_LIST] == ["Flat", "Rock"]
    assert state.attributes[ATTR_SOUND_MODE] == "Rock"


# ── play_media / announce ──────────────────────────────────────────────────


async def _play(hass, media_id, media_type="music", **extra_data):
    data = {ATTR_ENTITY_ID: MP, ATTR_MEDIA_CONTENT_TYPE: media_type,
            ATTR_MEDIA_CONTENT_ID: media_id, **extra_data}
    await hass.services.async_call(MP_DOMAIN, SERVICE_PLAY_MEDIA, data, blocking=True)


async def test_play_favourite_uses_radio_play(hass, setup_auvra, aioclient_mock) -> None:
    """radio_play keeps source=radio + station on the device, so next/previous
    step through the favourites and select_source "Radio" resumes it."""
    await setup_auvra(V12_INFO, v12_devices(), V12_STATIONS)
    await _play(hass, "smartghar://radio/1", "channel")
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"station_id": 1, "cmd": "radio_play"}
    ]


async def test_play_favourite_radio_only_firmware(hass, setup_auvra, aioclient_mock) -> None:
    """Without url_play the favourite can still be started by id."""
    await setup_auvra(_info_with(["media_player", "airplay2", "radio"]), v12_devices(), V12_STATIONS)
    await _play(hass, "smartghar://radio/2", "channel")
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"station_id": 2, "cmd": "radio_play"}
    ]


async def test_radio_browser_media_source_plays_url(hass, setup_auvra, aioclient_mock) -> None:
    """HA's Radio Browser source resolves to the station's own URL, which is
    passed through unsigned."""
    await setup_auvra(V12_INFO, v12_devices())
    with patch(
        "homeassistant.components.media_source.async_resolve_media",
        return_value=PlayMedia("https://sp14.instainternet.com/8044/stream", "audio/aac"),
    ):
        await _play(hass, "media-source://radio_browser/0a1b2c3d", "audio/aac")
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"url": "https://sp14.instainternet.com/8044/stream", "announce": False,
         "cmd": "play_url"}
    ]


async def test_play_plain_url(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    await _play(hass, "http://stream.zeno.fm/g95zm67prfhvv",
                **{ATTR_MEDIA_EXTRA: {"title": "Hits Of Lata Mangeshkar", "volume": 20}})
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"url": "http://stream.zeno.fm/g95zm67prfhvv", "announce": False,
         "title": "Hits Of Lata Mangeshkar", "volume": 20, "cmd": "play_url"}
    ]


async def test_announce_tts_media_source(hass, setup_auvra, aioclient_mock) -> None:
    """tts.speak sends media-source://tts/... with announce=True; HA resolves
    it to /api/tts_proxy/..., which must reach the device as an absolute URL."""
    await hass.config.async_update(internal_url="http://192.168.0.10:8123")
    await setup_auvra(V12_INFO, v12_devices(source="airplay", active=True, playing=True))
    with patch(
        "homeassistant.components.media_source.async_resolve_media",
        return_value=PlayMedia("/api/tts_proxy/abc123.mp3", "audio/mpeg"),
    ) as resolve:
        await _play(
            hass,
            "media-source://tts/tts.google_translate_en_com?message=Tank+full",
            **{ATTR_MEDIA_ANNOUNCE: True},
        )
    assert resolve.call_args.args[2] == MP
    body = calls(aioclient_mock, "POST", "/api/v1/devices/1/command")
    assert body == [{"url": "http://192.168.0.10:8123/api/tts_proxy/abc123.mp3",
                     "announce": True, "cmd": "play_url"}]


async def test_local_media_is_signed(hass, setup_auvra, aioclient_mock) -> None:
    await hass.config.async_update(internal_url="http://192.168.0.10:8123")
    await setup_auvra(V12_INFO, v12_devices())
    with patch(
        "homeassistant.components.media_source.async_resolve_media",
        return_value=PlayMedia("/media/local/chime.mp3", "audio/mpeg"),
    ):
        await _play(hass, "media-source://media_source/local/chime.mp3",
                    **{ATTR_MEDIA_ANNOUNCE: True})
    (body,) = calls(aioclient_mock, "POST", "/api/v1/devices/1/command")
    assert body["url"].startswith("http://192.168.0.10:8123/media/local/chime.mp3?authSig=")
    assert body["announce"] is True
    assert len(body["url"]) <= 1024


async def test_announce_refused_without_capability(hass, setup_auvra, aioclient_mock) -> None:
    """Playing an announcement as normal media would replace the current
    playback with nothing to restore it, so it is refused."""
    await setup_auvra(_info_with(["media_player", "airplay2", "url_play"]), v12_devices())
    with pytest.raises(HomeAssistantError, match="announcements"):
        await _play(hass, "http://example.com/ding.mp3", **{ATTR_MEDIA_ANNOUNCE: True})
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == []


async def test_url_play_refused_on_radio_only(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(_info_with(["media_player", "airplay2", "radio"]), v12_devices())
    with pytest.raises(HomeAssistantError):
        await _play(hass, "http://example.com/a.mp3")
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == []


async def test_bad_urls_rejected_before_device(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    with pytest.raises(HomeAssistantError, match="at most 1024"):
        await _play(hass, "http://example.com/" + "a" * 1010 + ".mp3")
    with pytest.raises(HomeAssistantError, match="http/https"):
        await _play(hass, "rtsp://example.com/stream")
    with pytest.raises(HomeAssistantError, match="control characters"):
        await _play(hass, "http://example.com/a\tb.mp3")
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == []


async def test_url_limit_is_1024(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    url = "http://example.com/" + "a" * (1024 - len("http://example.com/"))
    assert len(url) == 1024
    await _play(hass, url)
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command")[0]["url"] == url


async def test_spaces_are_encoded(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    await _play(hass, "http://example.com/my chime.mp3")
    (body,) = calls(aioclient_mock, "POST", "/api/v1/devices/1/command")
    assert body["url"] == "http://example.com/my%20chime.mp3"


async def test_device_refusal_surfaces(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices())
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/v1/devices/1/command",
                        json={"ok": False, "msg": "Stream unreachable"})
    with pytest.raises(HomeAssistantError, match="Stream unreachable"):
        await _play(hass, "http://stream.zeno.fm/8ty8szwpwfeuv")


async def test_unknown_favourite_announce(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices(), [])
    with pytest.raises(HomeAssistantError, match="Unknown Auvra favourite"):
        await _play(hass, "smartghar://radio/99", "channel", **{ATTR_MEDIA_ANNOUNCE: True})
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == []


async def test_url_playing_state(hass, setup_auvra) -> None:
    """As built: a URL plays with active=false, sender "", title = the
    request's title, no station, no artwork, no position."""
    await setup_auvra(
        V12_INFO,
        v12_devices(source="url", playing=True, active=False, sender="",
                    title="Doorbell test", artist="", art_rev=0,
                    position_ms=None, duration_ms=None),
    )
    state = hass.states.get(MP)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "Doorbell test"
    assert state.attributes["app_name"] == "URL"
    assert "media_channel" not in state.attributes
    assert "entity_picture" not in state.attributes
    # Shown although not selectable: source_list stays ["AirPlay", "Radio"].
    assert state.attributes[ATTR_INPUT_SOURCE] == "URL"


async def test_radio_buffering_state(hass, setup_auvra) -> None:
    await setup_auvra(
        V12_INFO,
        v12_devices(source="radio", playing=True, stall=True, active=False,
                    station={"id": 2, "name": "Radio Gaana"}, stream_title=""),
    )
    state = hass.states.get(MP)
    assert state.state == "buffering"
    assert state.attributes["media_title"] == "Radio Gaana"


async def test_announce_favourite_uses_its_url(hass, setup_auvra, aioclient_mock) -> None:
    await setup_auvra(V12_INFO, v12_devices(), V12_STATIONS)
    await _play(hass, "smartghar://radio/2", "channel", **{ATTR_MEDIA_ANNOUNCE: True})
    assert calls(aioclient_mock, "POST", "/api/v1/devices/1/command") == [
        {"url": "https://sp14.instainternet.com/8044/stream", "announce": True,
         "title": "Radio Gaana", "cmd": "play_url"}
    ]


# ── browse ─────────────────────────────────────────────────────────────────


async def _browse(hass, content_id=None):
    data = {ATTR_ENTITY_ID: MP}
    if content_id is not None:
        data[ATTR_MEDIA_CONTENT_ID] = content_id
        data[ATTR_MEDIA_CONTENT_TYPE] = "channels"
    resp = await hass.services.async_call(
        MP_DOMAIN, SERVICE_BROWSE_MEDIA, data, blocking=True, return_response=True
    )
    return resp[MP]


async def test_browse_root_and_favourites(hass, setup_auvra) -> None:
    await setup_auvra(V12_INFO, v12_devices(), V12_STATIONS)
    root = (await _browse(hass)).as_dict()
    titles = [c["title"] for c in root["children"]]
    assert titles[0] == "Auvra favourites"

    radio = (await _browse(hass, "smartghar://radio")).as_dict()
    assert [(c["title"], c["media_content_id"], c["can_play"], c["thumbnail"])
            for c in radio["children"]] == [
        ("Hits Of Bollywood", "smartghar://radio/1", True, "https://zeno.fm/favicon.ico"),
        ("Radio Gaana", "smartghar://radio/2", True, None),
    ]


async def test_browse_empty_favourites(hass, setup_auvra) -> None:
    """No bundled presets: a new speaker's favourites list is empty."""
    await setup_auvra(V12_INFO, v12_devices(), [])
    radio = (await _browse(hass, "smartghar://radio")).as_dict()
    assert radio["children"] == []


async def test_browse_includes_media_sources(hass, setup_auvra, tmp_path) -> None:
    (tmp_path / "chime.mp3").write_bytes(b"ID3")
    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "media_source", {})
    await setup_auvra(V12_INFO, v12_devices(), V12_STATIONS)
    root = (await _browse(hass)).as_dict()
    ids = [c["media_content_id"] for c in root["children"]]
    assert ids[0] == "smartghar://radio"
    assert any(i.startswith("media-source://") for i in ids[1:]), ids


async def test_browse_without_radio_capability(hass, setup_auvra) -> None:
    await setup_auvra(_info_with(["media_player", "airplay2", "url_play"]), v12_devices())
    root = (await _browse(hass)).as_dict()
    assert all(c["media_content_id"] != "smartghar://radio" for c in root["children"])


# ── capability change → reload ─────────────────────────────────────────────


async def test_firmware_update_adds_entities(
    hass, setup_auvra, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await setup_auvra(V11_INFO, V11_DEVICES)
    assert _entity_for(hass, "select", "eq_preset") is None

    mock_device(aioclient_mock, V12_INFO, v12_devices())
    coordinator = hass.data["smartghar"][entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _entity_for(hass, "select", "eq_preset") is not None
    features = hass.states.get(MP).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & MediaPlayerEntityFeature.PLAY_MEDIA



# ── station logo (firmware 0.10.0-ui2) ─────────────────────────────────────


async def test_radio_logo_is_the_media_image(hass, setup_auvra) -> None:
    from .fixtures import LOGO_URL

    await setup_auvra(
        V12_INFO,
        v12_devices(source="radio", playing=True, active=False, sender="", art_rev=0,
                    station={"id": 1, "name": "Hits Of Bollywood", "image_url": LOGO_URL},
                    stream_title="Song", title="Song", artist="Hits Of Bollywood"),
    )
    state = hass.states.get(MP)
    pic = state.attributes.get("entity_picture")
    # Proxied through HA, never the station's URL itself.
    assert pic and pic.startswith("/api/media_player_proxy/media_player.auvra_bedroom")
    ent = hass.data["media_player"].get_entity(MP)
    assert ent.media_image_url == LOGO_URL
    assert ent.media_image_remotely_accessible is False
    assert ent.media_image_hash and ent.media_image_hash != "0"


async def test_radio_without_logo_has_no_picture(hass, setup_auvra) -> None:
    await setup_auvra(
        V12_INFO,
        v12_devices(source="radio", playing=True, active=False, sender="", art_rev=0,
                    station={"id": 2, "name": "Radio Gaana"}, stream_title=""),
    )
    assert "entity_picture" not in hass.states.get(MP).attributes


async def test_logo_ignored_outside_radio_and_airplay_art_kept(hass, setup_auvra) -> None:
    from .fixtures import LOGO_URL

    await setup_auvra(
        V12_INFO,
        v12_devices(source="airplay", playing=True, active=True, sender="Mac", art_rev=5,
                    station={"id": 1, "name": "x", "image_url": LOGO_URL}),
    )
    ent = hass.data["media_player"].get_entity(MP)
    assert ent.media_image_url == "http://192.168.0.130/api/art.jpg?rev=5"
    assert ent.media_image_hash == "5"
