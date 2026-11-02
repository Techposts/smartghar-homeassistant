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

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DEVICE_KIND_SPEAKER, DOMAIN, MODEL_SPEAKER
from .coordinator import SmartGharCoordinator
from .device_info import subdevice_device_info
from .speaker_logic import (
    VOLUME_STEP_PCT,
    art_hash,
    art_revision,
    art_url,
    ms_to_seconds,
    speaker_state,
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
    _attr_supported_features = SPEAKER_FEATURES
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
        return MediaType.MUSIC if self._state.get("active") else None

    @property
    def media_title(self) -> str | None:
        return text_or_none(self._state.get("title"))

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
        if not state.get("active"):
            return None
        return text_or_none(state.get("sender"))

    @property
    def media_image_url(self) -> str | None:
        state = self._state
        # art_rev is reset to 0 by the firmware when the sender goes away;
        # gating on `active` as well keeps a stale revision from producing a
        # URL that would 404 at the proxy.
        if not state.get("active"):
            return None
        return art_url(self.coordinator.client.host, state.get("art_rev"))

    @property
    def media_image_hash(self) -> str | None:
        if not self._state.get("active"):
            return None
        return art_hash(self._state.get("art_rev"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        return {
            "sender": state.get("sender") or "",
            "stall": bool(state.get("stall")),
            "art_rev": art_revision(state.get("art_rev")),
        }

    # ── Commands ───────────────────────────────────────────────────────────

    async def _command(self, cmd: str) -> None:
        resp = await self.coordinator.client.device_command(self._device_id, cmd)
        if isinstance(resp, dict) and resp.get("ok") is False:
            _LOGGER.debug(
                "Speaker %s did not act on %r (no AirPlay sender connected?)",
                self.entity_id, cmd,
            )
        await self.coordinator.async_request_refresh()

    async def _put(self, fields: dict[str, Any]) -> None:
        await self.coordinator.client.update_device(self._device_id, fields)
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
