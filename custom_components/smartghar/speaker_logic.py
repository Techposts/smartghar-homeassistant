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

Schema 1.2 adds (only on firmware whose /api/v1/info lists the capability):
    source       "idle" | "airplay" | "radio" | "url" | "dlna"
    sources      list[str]          what select_source accepts
    station      {id, name} | None  the radio station playing
    stream_title str                ICY title of the stream
    announcing   bool               an announcement is playing on top
    channel      "stereo" | "left" | "right" | "mono"
    eq           {preset, bass_db, treble_db, loudness, presets?}
"""
from __future__ import annotations

import hashlib
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

    `active` means "an AirPlay sender is connected". A 1.2 device playing
    radio or a URL has no sender, so a non-AirPlay `source` counts as active.
    """
    if not state:
        return None
    if state.get("power") == "standby":
        return STATE_STANDBY
    if not (state.get("active") or is_stream_source(state)):
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


# ── Schema 1.2: capabilities, sources, EQ, radio, play_url ─────────────────

# Capability strings in /api/v1/info `capabilities`. Each 1.2 feature and
# entity is created only when the device lists its capability (a 0.9.x
# device lists just media_player + airplay2 and must look exactly as before).
CAP_MEDIA_PLAYER = "media_player"
CAP_AIRPLAY2 = "airplay2"
CAP_EQ = "eq"
CAP_CHANNEL = "channel"
CAP_RADIO = "radio"
CAP_URL_PLAY = "url_play"
CAP_ANNOUNCE = "announce"
CAP_DLNA = "dlna"
CAP_IR = "ir"

SOURCE_IDLE = "idle"
SOURCE_AIRPLAY = "airplay"
STREAM_SOURCES = frozenset({"radio", "url", "dlna"})

# Display names for source ids. Unknown ids are shown title-cased.
SOURCE_LABELS = {
    "airplay": "AirPlay",
    "radio": "Radio",
    "url": "URL",
    "dlna": "DLNA",
}

CHANNEL_OPTIONS = ["stereo", "left", "right", "mono"]

# The plan's presets (night-2026-11-18.md, G2), used when the firmware does
# not send its own list in state.eq.presets.
DEFAULT_EQ_PRESETS = ["flat", "bass", "vocal", "bookshelf", "soundbar", "night"]
DEFAULT_EQ_RANGE_DB = (-12, 12)

# Radio stations in the media browser / play_media.
RADIO_URI_PREFIX = "smartghar://radio"

# Firmware limit on play_url (as built, 0.10.0-rc6): http/https only, at most
# 1024 characters, no spaces or control characters.
PLAY_URL_MAX_LEN = 1024


def capabilities(info: dict[str, Any] | None) -> frozenset[str]:
    """The capability set from /api/v1/info (empty when absent or malformed)."""
    if not info:
        return frozenset()
    caps = info.get("capabilities")
    if not isinstance(caps, list):
        return frozenset()
    return frozenset(c for c in caps if isinstance(c, str))


def has_channel(info: dict[str, Any] | None) -> bool:
    """The channel select ships with EQ in 1.2 (decision 8); a firmware that
    lists `channel` on its own is honoured too."""
    caps = capabilities(info)
    return CAP_CHANNEL in caps or CAP_EQ in caps


def is_stream_source(state: dict[str, Any] | None) -> bool:
    """True when a non-AirPlay source (radio / url / dlna) owns the output."""
    if not state:
        return False
    return state.get("source") in STREAM_SOURCES


def source_label(source_id: Any) -> str | None:
    """Device source id -> name shown in HA. None for idle / unknown."""
    if not isinstance(source_id, str) or source_id in ("", SOURCE_IDLE):
        return None
    return SOURCE_LABELS.get(source_id, source_id.replace("_", " ").title())


def source_labels(state: dict[str, Any] | None) -> list[str] | None:
    """state.sources -> display list, or None when the device sends none."""
    if not state:
        return None
    raw = state.get("sources")
    if not isinstance(raw, list):
        return None
    labels = [source_label(s) for s in raw]
    return [x for x in labels if x]


def source_id_for_label(state: dict[str, Any] | None, label: str) -> str | None:
    """Reverse of source_label over state.sources (case-insensitive)."""
    for sid in (state or {}).get("sources") or []:
        if not isinstance(sid, str):
            continue
        if source_label(sid) == label or sid.lower() == label.lower():
            return sid
    return None


def eq_state(state: dict[str, Any] | None) -> dict[str, Any]:
    eq = (state or {}).get("eq")
    return eq if isinstance(eq, dict) else {}


def eq_preset_ids(state: dict[str, Any] | None) -> list[str]:
    """Preset ids the device accepts: state.eq.presets, else the plan's six."""
    raw = eq_state(state).get("presets")
    if isinstance(raw, list):
        ids = [p for p in raw if isinstance(p, str) and p]
        if ids:
            return ids
    return list(DEFAULT_EQ_PRESETS)


def eq_preset_label(preset_id: str) -> str:
    """Preset id -> sound-mode name ("bookshelf" -> "Bookshelf")."""
    return preset_id.replace("_", " ").title()


def eq_preset_id_for_label(state: dict[str, Any] | None, label: str) -> str | None:
    """Sound-mode name or id -> the device's preset id (case-insensitive)."""
    for pid in eq_preset_ids(state):
        if label.lower() in (pid.lower(), eq_preset_label(pid).lower()):
            return pid
    return None


def current_eq_preset(state: dict[str, Any] | None) -> str | None:
    """The device's current preset id, matched case-insensitively to the
    preset list. A preset outside the list (e.g. "custom" after a manual
    bass/treble change) is returned as-is."""
    raw = eq_state(state).get("preset")
    if not isinstance(raw, str) or not raw:
        return None
    for pid in eq_preset_ids(state):
        if pid.lower() == raw.lower():
            return pid
    return raw


def eq_range_db(state: dict[str, Any] | None, key: str) -> tuple[float, float]:
    """(min, max) for bass_db / treble_db. Firmware may report a range as
    eq.<key>_range = [min, max] or eq.range_db = [min, max]; else ±12 dB."""
    eq = eq_state(state)
    for raw in (eq.get(f"{key}_range"), eq.get("range_db")):
        if (
            isinstance(raw, (list, tuple))
            and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw)
            and raw[0] < raw[1]
        ):
            return float(raw[0]), float(raw[1])
    return float(DEFAULT_EQ_RANGE_DB[0]), float(DEFAULT_EQ_RANGE_DB[1])


def station_uri(station_id: Any) -> str:
    return f"{RADIO_URI_PREFIX}/{station_id}"


def parse_station_uri(media_id: str) -> str | None:
    """smartghar://radio/<id> -> "<id>"; None for anything else."""
    prefix = RADIO_URI_PREFIX + "/"
    if isinstance(media_id, str) and media_id.startswith(prefix):
        sid = media_id[len(prefix):]
        return sid or None
    return None


def station_id_value(sid: str) -> int | str:
    """Send numeric station ids as numbers, others as strings."""
    return int(sid) if sid.isdigit() else sid


def radio_stations(payload: Any) -> list[dict[str, Any]]:
    """GET /api/v1/radio/stations -> list of {id, name, url, ...}.

    The contract returns a bare list; {"stations": [...]} is accepted too.
    Entries without an id are dropped.
    """
    if isinstance(payload, dict):
        payload = payload.get("stations")
    if not isinstance(payload, list):
        return []
    out = []
    for st in payload:
        if isinstance(st, dict) and st.get("id") not in (None, ""):
            out.append(st)
    return out


def normalise_play_url(url: str) -> str:
    """Percent-encode spaces, which the firmware refuses but users paste."""
    return url.strip().replace(" ", "%20")


def play_url_error(url: Any) -> str | None:
    """Why the device would reject this play_url, or None when it is fine."""
    if not isinstance(url, str) or not url:
        return "empty URL"
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"only http/https URLs can be played, got {url[:40]!r}"
    if any(c == " " or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return "the URL contains spaces or control characters"
    if len(url) > PLAY_URL_MAX_LEN:
        return f"URL is {len(url)} characters; the speaker accepts at most {PLAY_URL_MAX_LEN}"
    return None


def play_url_params(
    url: str,
    *,
    announce: bool = False,
    title: Any = None,
    volume: Any = None,
) -> dict[str, Any]:
    """Parameters of the play_url command (the body minus "cmd")."""
    body: dict[str, Any] = {"url": url, "announce": bool(announce)}
    if isinstance(title, str) and title:
        body["title"] = title[:128]
    if volume is not None and not isinstance(volume, bool):
        try:
            body["volume"] = min(max(int(round(float(volume))), 0), 100)
        except (TypeError, ValueError):
            pass
    return body


def station_logo_url(state: dict[str, Any] | None) -> str | None:
    """state.station.image_url while radio plays a favourite with a logo
    (firmware 0.10.0-ui2+); None otherwise or when it is not http(s)."""
    if not state or state.get("source") != "radio":
        return None
    station = state.get("station")
    if not isinstance(station, dict):
        return None
    return http_url_or_none(station.get("image_url"))


def http_url_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def logo_hash(url: str) -> str:
    """media_image_hash for a logo URL (stable, short)."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]
