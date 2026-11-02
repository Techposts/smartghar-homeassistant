"""Pure helpers for the Auvra speaker (device kind "speaker").

No Home Assistant imports on purpose. media_player.py maps these results onto
HA's MediaPlayerState / MediaPlayerEntity, and tests/test_speaker_state_map.py
exercises this module directly on a machine without Home Assistant installed.

Device state (from /api/v1/devices, kind="speaker"):
    power        "on" | "standby"   standby = idle screen off, still reachable
    active       bool               an AirPlay sender is connected
    playing      bool
    stall        bool               buffering
    sender       str                connected sender name, "" when none
    volume       0-100, muted bool
    title / artist / album          "" when unknown
    art_rev      int                0 = no artwork; bumps when artwork changes
    position_ms / duration_ms       int | None
"""
from __future__ import annotations

from typing import Any

ART_PATH = "/api/art.jpg"
DEFAULT_HTTP_PORT = 80

# These strings equal the values of
# homeassistant.components.media_player.MediaPlayerState so the entity can do
# MediaPlayerState(name) without this module importing HA.
STATE_STANDBY = "standby"
STATE_IDLE = "idle"
STATE_PLAYING = "playing"
STATE_PAUSED = "paused"
STATE_BUFFERING = "buffering"

# Volume step for media_player.volume_up / volume_down, in device points (%).
VOLUME_STEP_PCT = 5


def speaker_state(state: dict[str, Any] | None) -> str | None:
    """Map the device state dict to a MediaPlayerState value name.

    Precedence: standby > no sender (idle) > playing (+stall = buffering)
    > sender connected but not playing (paused). None when there is no
    state at all (device missing from the registry).
    """
    if not state:
        return None
    if state.get("power") == "standby":
        return STATE_STANDBY
    if not state.get("active"):
        return STATE_IDLE
    if state.get("playing"):
        return STATE_BUFFERING if state.get("stall") else STATE_PLAYING
    return STATE_PAUSED


def volume_level(state: dict[str, Any] | None) -> float | None:
    """Device volume 0-100 -> HA volume_level 0.0-1.0 (clamped)."""
    if not state:
        return None
    raw = state.get("volume")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return min(max(value, 0.0), 100.0) / 100.0


def volume_percent(level: float) -> int:
    """HA volume_level 0.0-1.0 -> device volume 0-100 (rounded, clamped)."""
    return int(round(min(max(float(level), 0.0), 1.0) * 100))


def stepped_volume(current: Any, delta: int) -> int | None:
    """Current device volume +/- delta, clamped to 0-100. None if unknown."""
    if current is None or isinstance(current, bool):
        return None
    try:
        value = int(current)
    except (TypeError, ValueError):
        return None
    return min(max(value + delta, 0), 100)


def text_or_none(value: Any) -> str | None:
    """Firmware sends "" for unknown title/artist/album/sender; HA wants None."""
    if isinstance(value, str) and value != "":
        return value
    return None


def ms_to_seconds(value: Any) -> int | None:
    """position_ms / duration_ms -> whole seconds for media_position/duration."""
    if value is None or isinstance(value, bool):
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms < 0:
        return None
    return ms // 1000


def art_revision(value: Any) -> int:
    """Normalise art_rev to a non-negative int (0 = no artwork)."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        rev = int(value)
    except (TypeError, ValueError):
        return 0
    return rev if rev > 0 else 0


def art_url(host: str | None, art_rev: Any, port: int = DEFAULT_HTTP_PORT) -> str | None:
    """Cache-busting artwork URL, or None when the device has no artwork.

    The rev query parameter changes whenever the artwork changes, so HA's
    media-image cache (keyed on URL / media_image_hash) refetches.
    """
    rev = art_revision(art_rev)
    if rev == 0 or not host:
        return None
    netloc = host if port in (None, DEFAULT_HTTP_PORT) else f"{host}:{port}"
    return f"http://{netloc}{ART_PATH}?rev={rev}"


def art_hash(art_rev: Any) -> str | None:
    """media_image_hash input: the revision counter itself."""
    rev = art_revision(art_rev)
    return str(rev) if rev else None
