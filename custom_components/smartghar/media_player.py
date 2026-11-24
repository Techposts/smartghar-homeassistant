"""Media player entity — SmartGhar Auvra AirPlay 2 streamer (kind="speaker").

Auvra is a standalone hub with one device (id 1), so the entity lands on the
hub's own HA device card and takes the device name (no entity name of its
own). State, now-playing metadata, artwork revision and volume all arrive via
the coordinator — WS snapshots on every change, 30 s poll as the fallback.

Writes follow the integration's conventions:
  - volume / mute   → flat PUT /api/v1/devices/<id>  {"volume": 55} / {"muted": true}
  - transport       → POST /api/v1/devices/<id>/command {"cmd": "play"|…}
  - turn_off/on     → command "standby" / "wake"
Transport commands are relayed by the firmware to the connected AirPlay
sender (iPhone / Mac). With no sender connected the firmware answers
ok:false and nothing happens — logged at debug, not raised, so automations
that fire "pause" at an idle speaker don't error.

Artwork: the firmware serves /api/art.jpg (404 when none). `art_rev` bumps on
every artwork change, so the URL carries it as a cache-buster and doubles as
media_image_hash. media_image_remotely_accessible stays False so HA proxies
the image for remote / mobile clients.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import SmartGharApiError
from .const import DEVICE_KIND_SPEAKER, DOMAIN, MODEL_SPEAKER
from .coordinator import SmartGharCoordinator
from .device_info import subdevice_device_info
from .speaker_logic import (
    CAP_ANNOUNCE,
    CAP_EQ,
    CAP_RADIO,
    CAP_URL_PLAY,
    RADIO_URI_PREFIX,
    VOLUME_STEP_PCT,
    art_hash,
    art_revision,
    art_url,
    capabilities,
    current_eq_preset,
    eq_preset_id_for_label,
    eq_preset_ids,
    eq_preset_label,
    http_url_or_none,
    is_stream_source,
    logo_hash,
    ms_to_seconds,
    normalise_play_url,
    parse_station_uri,
    play_url_error,
    play_url_params,
    radio_stations,
    source_id_for_label,
    source_label,
    source_labels,
    speaker_state,
    station_id_value,
    station_logo_url,
    station_uri,
    stepped_volume,
    text_or_none,
    volume_level,
    volume_percent,
)

_LOGGER = logging.getLogger(__name__)

SPEAKER_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
)


def speaker_features(caps: frozenset[str]) -> MediaPlayerEntityFeature:
    """Feature flags for a device with these /api/v1/info capabilities.

    1.1 devices (media_player + airplay2) get exactly SPEAKER_FEATURES.
    """
    features = SPEAKER_FEATURES
    if CAP_URL_PLAY in caps or CAP_RADIO in caps:
        features |= MediaPlayerEntityFeature.PLAY_MEDIA
        features |= MediaPlayerEntityFeature.BROWSE_MEDIA
    if CAP_ANNOUNCE in caps and CAP_URL_PLAY in caps:
        features |= MediaPlayerEntityFeature.MEDIA_ANNOUNCE
    if CAP_RADIO in caps:
        features |= MediaPlayerEntityFeature.SELECT_SOURCE
    if CAP_EQ in caps:
        features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
    return features


def _is_audio(item: BrowseMedia) -> bool:
    return str(item.media_content_type).startswith("audio/")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[MediaPlayerEntity] = []
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_SPEAKER:
            entities.append(SmartGharSpeaker(coordinator, dev["id"]))
    async_add_entities(entities)


class SmartGharSpeaker(CoordinatorEntity[SmartGharCoordinator], MediaPlayerEntity):
    """One Auvra speaker."""

    _attr_has_entity_name = True
    _attr_name = None  # the speaker IS the device — use the device name
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_image_remotely_accessible = False

    def __init__(self, coordinator: SmartGharCoordinator, device_id: int) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_speaker_{device_id}_media_player"
        )
        self._last_position_ms: Any = None
        self._position_updated_at: datetime | None = None
        self._track_position()

    # ── Coordinator plumbing ───────────────────────────────────────────────

    @property
    def _device(self) -> dict[str, Any] | None:
        return self.coordinator.device_by_id(self._device_id)

    @property
    def _state(self) -> dict[str, Any]:
        dev = self._device
        return (dev.get("state") or {}) if dev else {}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_position()
        super()._handle_coordinator_update()

    def _track_position(self) -> None:
        """Stamp media_position_updated_at whenever the reported position moves.

        HA extrapolates the live position from (media_position, updated_at)
        while playing, so the stamp must belong to the reading it was taken
        with — not to every refresh.
        """
        position_ms = self._state.get("position_ms")
        if position_ms != self._last_position_ms:
            self._last_position_ms = position_ms
            self._position_updated_at = dt_util.utcnow()

    @property
    def available(self) -> bool:
        return super().available and self._device is not None

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._device or {"kind": DEVICE_KIND_SPEAKER, "id": self._device_id}
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_SPEAKER,
            fallback_name=f"Speaker {self._device_id}",
        )

    @property
    def _caps(self) -> frozenset[str]:
        return capabilities(self.coordinator.info)

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        # A property, not a class attribute: /api/v1/info is re-read on every
        # poll, so a firmware update (or a setting that turns a feature off)
        # shows up without reloading the integration.
        return speaker_features(self._caps)

    # ── State ──────────────────────────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState | None:
        name = speaker_state(self._state)
        return MediaPlayerState(name) if name else None

    @property
    def volume_level(self) -> float | None:
        return volume_level(self._state)

    @property
    def is_volume_muted(self) -> bool | None:
        state = self._state
        return bool(state["muted"]) if "muted" in state else None

    @property
    def media_content_type(self) -> MediaType | str | None:
        state = self._state
        if state.get("source") == "radio":
            return MediaType.CHANNEL
        if state.get("active") or is_stream_source(state):
            return MediaType.MUSIC
        return None

    @property
    def media_title(self) -> str | None:
        state = self._state
        if is_stream_source(state):
            return (
                text_or_none(state.get("stream_title"))
                or text_or_none(state.get("title"))
                or self.media_channel
            )
        return text_or_none(state.get("title"))

    @property
    def media_channel(self) -> str | None:
        station = self._state.get("station")
        if isinstance(station, dict):
            return text_or_none(station.get("name"))
        return None

    # Sources (schema 1.2, capability `radio`)

    @property
    def source(self) -> str | None:
        if CAP_RADIO not in self._caps:
            return None
        return source_label(self._state.get("source"))

    @property
    def source_list(self) -> list[str] | None:
        if CAP_RADIO not in self._caps:
            return None
        return source_labels(self._state)

    # Sound mode = EQ preset (schema 1.2, capability `eq`)

    @property
    def sound_mode(self) -> str | None:
        if CAP_EQ not in self._caps:
            return None
        preset = current_eq_preset(self._state)
        return eq_preset_label(preset) if preset else None

    @property
    def sound_mode_list(self) -> list[str] | None:
        if CAP_EQ not in self._caps:
            return None
        return [eq_preset_label(p) for p in eq_preset_ids(self._state)]

    @property
    def media_artist(self) -> str | None:
        return text_or_none(self._state.get("artist"))

    @property
    def media_album_name(self) -> str | None:
        return text_or_none(self._state.get("album"))

    @property
    def media_duration(self) -> int | None:
        return ms_to_seconds(self._state.get("duration_ms"))

    @property
    def media_position(self) -> int | None:
        return ms_to_seconds(self._state.get("position_ms"))

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_updated_at

    @property
    def app_name(self) -> str | None:
        state = self._state
        if is_stream_source(state):
            return source_label(state.get("source"))
        if not state.get("active"):
            return None
        return text_or_none(state.get("sender"))

    @property
    def media_image_url(self) -> str | None:
        state = self._state
        # Radio: the favourite's logo. HA proxies it (remotely_accessible is
        # False), so an http logo works under an https HA and clients never
        # contact the station's site.
        if (logo := station_logo_url(state)) is not None:
            return logo
        # art_rev is reset to 0 by the firmware when the sender goes away;
        # gating on `active` as well keeps a stale revision from producing a
        # URL that would 404 at the proxy.
        if not state.get("active"):
            return None
        return art_url(self.coordinator.client.host, state.get("art_rev"))

    @property
    def media_image_hash(self) -> str | None:
        if (logo := station_logo_url(self._state)) is not None:
            return logo_hash(logo)
        if not self._state.get("active"):
            return None
        return art_hash(self._state.get("art_rev"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        attrs: dict[str, Any] = {
            "sender": state.get("sender") or "",
            "stall": bool(state.get("stall")),
            "art_rev": art_revision(state.get("art_rev")),
        }
        # 1.2 keys appear only when the device sends them, so a 1.1 device's
        # attributes stay exactly as they were.
        if "announcing" in state:
            attrs["announcing"] = bool(state.get("announcing"))
        if "stream_title" in state:
            attrs["stream_title"] = state.get("stream_title") or ""
        return attrs

    # ── Commands ───────────────────────────────────────────────────────────

    async def _strict_command(self, cmd: str, params: dict[str, Any] | None = None) -> None:
        """A 1.2 command whose refusal the user should see (play_url,
        select_source, radio_play): ok:false becomes a HomeAssistantError."""
        try:
            resp = await self.coordinator.client.device_command(
                self._device_id, cmd, params
            )
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Auvra did not accept {cmd}: {err}") from err
        if isinstance(resp, dict) and resp.get("ok") is False:
            raise HomeAssistantError(
                f"Auvra refused {cmd}: {resp.get('msg') or 'no reason given'}"
            )
        await self.coordinator.async_request_refresh()

    async def _command(self, cmd: str) -> None:
        try:
            resp = await self.coordinator.client.device_command(self._device_id, cmd)
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Auvra did not answer {cmd}: {err}") from err
        if isinstance(resp, dict) and resp.get("ok") is False:
            _LOGGER.debug(
                "Speaker %s did not act on %r (no AirPlay sender connected?)",
                self.entity_id, cmd,
            )
        await self.coordinator.async_request_refresh()

    async def _put(self, fields: dict[str, Any]) -> None:
        try:
            resp = await self.coordinator.client.update_device(self._device_id, fields)
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Auvra did not accept {fields}: {err}") from err
        if isinstance(resp, dict) and resp.get("rejected"):
            raise HomeAssistantError(
                f"Auvra rejected {', '.join(map(str, resp['rejected']))}: {fields}"
            )
        await self.coordinator.async_request_refresh()

    async def async_media_play(self) -> None:
        await self._command("play")

    async def async_media_pause(self) -> None:
        await self._command("pause")

    async def async_media_play_pause(self) -> None:
        await self._command("play_pause")

    async def async_media_stop(self) -> None:
        await self._command("stop")

    async def async_media_next_track(self) -> None:
        await self._command("next")

    async def async_media_previous_track(self) -> None:
        await self._command("previous")

    async def async_set_volume_level(self, volume: float) -> None:
        await self._put({"volume": volume_percent(volume)})

    async def async_mute_volume(self, mute: bool) -> None:
        await self._put({"muted": bool(mute)})

    async def async_volume_up(self) -> None:
        target = stepped_volume(self._state.get("volume"), VOLUME_STEP_PCT)
        if target is not None:
            await self._put({"volume": target})

    async def async_volume_down(self) -> None:
        target = stepped_volume(self._state.get("volume"), -VOLUME_STEP_PCT)
        if target is not None:
            await self._put({"volume": target})

    async def async_turn_off(self) -> None:
        await self._command("standby")

    async def async_turn_on(self) -> None:
        await self._command("wake")

    # ── Schema 1.2: sources, sound mode, play_media, browse ────────────────

    async def async_select_source(self, source: str) -> None:
        if CAP_RADIO not in self._caps:
            raise HomeAssistantError("This Auvra firmware has no source selection")
        source_id = source_id_for_label(self._state, source)
        if source_id is None:
            raise HomeAssistantError(f"Unknown source {source!r}")
        await self._strict_command("select_source", {"source": source_id})

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        if CAP_EQ not in self._caps:
            raise HomeAssistantError("This Auvra firmware has no EQ presets")
        preset = eq_preset_id_for_label(self._state, sound_mode)
        if preset is None:
            raise HomeAssistantError(f"Unknown sound mode {sound_mode!r}")
        await self._put({"eq": {"preset": preset}})

    async def _station(self, station_id: str) -> dict[str, Any] | None:
        try:
            payload = await self.coordinator.client.get_radio_stations()
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Could not read the Auvra favourites: {err}") from err
        for st in radio_stations(payload):
            if str(st.get("id")) == station_id:
                return st
        return None

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play an Auvra favourite (radio_play), an HA media source (TTS, local
        media, HA's Radio Browser) or a URL (both play_url).

        announce=True follows HA's announce semantics: the device ducks or
        pauses what is playing, plays the clip on top and restores the
        previous playback itself (decision 5). Without the `announce`
        capability an announcement is refused rather than played as normal
        media, which would replace the current playback with no restore.
        """
        caps = self._caps
        announce = bool(kwargs.get("announce"))
        extra = kwargs.get("extra") or {}
        if announce and CAP_ANNOUNCE not in caps:
            raise HomeAssistantError("This Auvra firmware cannot play announcements")

        title = extra.get("title")
        metadata = extra.get("metadata")
        if not title and isinstance(metadata, dict):
            title = metadata.get("title")

        station_id = parse_station_uri(media_id)
        if station_id is not None:
            # An Auvra favourite (the device's own station list). Played with
            # radio_play so the device reports source "radio" + station,
            # next/previous step through the favourites and select_source
            # "Radio" resumes it. Only an announcement uses its URL.
            if CAP_RADIO not in caps:
                raise HomeAssistantError("This Auvra firmware has no radio")
            if not announce:
                await self._strict_command(
                    "radio_play", {"station_id": station_id_value(station_id)}
                )
                return
            if CAP_URL_PLAY not in caps:
                raise HomeAssistantError("This Auvra firmware cannot announce a URL")
            station = await self._station(station_id)
            if station is None:
                raise HomeAssistantError(f"Unknown Auvra favourite {station_id!r}")
            media_id = str(station.get("url") or "")
            title = title or station.get("name")

        if CAP_URL_PLAY not in caps:
            raise HomeAssistantError("This Auvra firmware can only play its favourites")

        if media_source.is_media_source_id(media_id):
            try:
                play_item = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id
                )
            except media_source.Unresolvable as err:
                raise HomeAssistantError(f"Cannot play {media_id}: {err}") from err
            media_id = play_item.url

        # Relative HA paths (/api/tts_proxy/..., /media/local/...) become an
        # absolute, signed URL the speaker can fetch; foreign URLs pass as-is.
        url = normalise_play_url(async_process_play_media_url(self.hass, media_id))
        if (why := play_url_error(url)) is not None:
            raise HomeAssistantError(f"Auvra cannot play this media: {why}")

        await self._strict_command(
            "play_url",
            play_url_params(
                url, announce=announce, title=title, volume=extra.get("volume")
            ),
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        caps = self._caps
        if media_content_id and media_source.is_media_source_id(media_content_id):
            if CAP_URL_PLAY not in caps:
                raise BrowseError("This Auvra firmware cannot play HA media")
            return await media_source.async_browse_media(
                self.hass, media_content_id, content_filter=_is_audio
            )
        if media_content_id == RADIO_URI_PREFIX:
            if CAP_RADIO not in caps:
                raise BrowseError("This Auvra firmware has no radio")
            return await self._browse_radio()
        if media_content_id:
            raise BrowseError(f"Unknown media {media_content_id!r}")
        return await self._browse_root(caps)

    async def _browse_root(self, caps: frozenset[str]) -> BrowseMedia:
        children: list[BrowseMedia] = []
        if CAP_RADIO in caps:
            children.append(
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=RADIO_URI_PREFIX,
                    media_content_type=MediaType.CHANNELS,
                    title="Auvra favourites",
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.CHANNEL,
                )
            )
        if CAP_URL_PLAY in caps:
            try:
                ms_root = await media_source.async_browse_media(
                    self.hass, None, content_filter=_is_audio
                )
            except BrowseError as err:
                _LOGGER.debug("Media sources unavailable for %s: %s", self.entity_id, err)
            else:
                # One media source comes back as itself, several as a root
                # whose children are the sources (the Sonos pattern).
                if ms_root.domain is None and ms_root.children:
                    children.extend(ms_root.children)
                else:
                    children.append(ms_root)
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id="",
            media_content_type="",
            title=self.coordinator.info.get("hub_name") or "Auvra",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_radio(self) -> BrowseMedia:
        try:
            payload = await self.coordinator.client.get_radio_stations()
        except SmartGharApiError as err:
            raise BrowseError(f"Could not read the Auvra favourites: {err}") from err
        children = [
            BrowseMedia(
                media_class=MediaClass.CHANNEL,
                media_content_id=station_uri(st["id"]),
                media_content_type=MediaType.CHANNEL,
                title=str(st.get("name") or st.get("url") or st["id"]),
                can_play=True,
                can_expand=False,
                thumbnail=http_url_or_none(st.get("favicon")),
            )
            for st in radio_stations(payload)
        ]
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=RADIO_URI_PREFIX,
            media_content_type=MediaType.CHANNELS,
            title="Auvra favourites",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.CHANNEL,
        )
