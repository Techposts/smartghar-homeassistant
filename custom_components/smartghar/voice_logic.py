"""Pure helpers for Auvra voice and the "voice-ready" option.

Only the standard library and `datetime` here, so the logic is testable without
Home Assistant objects. Entities map these results onto HA.

The unit's voice block (GET /api/state -> voice) carries, among others:
    enabled, muted, mic (hardware present), state, ha_connected, duck_db,
    wake_sens "low|normal|high", advertise, tone, tone_style "A|B|C",
    follow_up "off|on|question", aec (only on firmware that has it),
    wake_word (the model id in use), wake_words [{id, phrase}] (the choices; only
    on firmware with a selectable wake word), port (only if the firmware reports it).
Writes go to POST /api/set with the keys in SETTINGS below.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

WYOMING_PORT = 10700
KEY_ADVERTISE = "wyoming_advertise"
KEY_TALK = "voice_talk"


@dataclass(frozen=True)
class VoiceSetting:
    """One firmware setting: how to read it back and how to write it."""

    key: str            # unique-id suffix and translation key
    read: str           # key inside voice {...}
    write: str          # key for POST /api/set
    kind: str           # "switch" | "select" | "number"
    options: tuple[str, ...] = ()
    optional: bool = False   # entity only when the unit reports `read` (or `options_key`)
    options_key: str = ""    # voice-block list of {"id", "phrase"} the options come from (else `options`)


SETTINGS: tuple[VoiceSetting, ...] = (
    VoiceSetting("voice_enabled", "enabled", "voice_enabled", "switch"),
    VoiceSetting("mic_mute", "muted", "mic_mute", "switch"),
    VoiceSetting("voice_tone", "tone", "voice_tone", "switch"),
    VoiceSetting("voice_aec", "aec", "voice_aec", "switch", optional=True),
    VoiceSetting(KEY_ADVERTISE, "advertise", KEY_ADVERTISE, "switch"),
    VoiceSetting("follow_up", "follow_up", "follow_up", "select",
                 ("off", "on", "question")),
    VoiceSetting("wake_sens", "wake_sens", "wake_sens", "select",
                 ("low", "normal", "high")),
    VoiceSetting("wake_word", "wake_word", "wake_word", "select",
                 optional=True, options_key="wake_words"),
    VoiceSetting("tone_style", "tone_style", "voice_tone_style", "select",
                 ("A", "B", "C")),
    VoiceSetting("duck_db", "duck_db", "duck_db", "number"),
)

DUCK_MIN, DUCK_MAX = 0, 40


def voice_supported(voice: dict[str, Any] | None) -> bool:
    """A unit supports voice when /api/state has a voice block with a mic."""
    return bool(voice) and bool(voice.get("mic"))


def voice_port(voice: dict[str, Any] | None) -> int:
    """Wyoming port: what the unit reports, else the firmware default."""
    port = (voice or {}).get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return port
    return WYOMING_PORT


def setting_available(setting: VoiceSetting, voice: dict[str, Any] | None) -> bool:
    if not setting.optional:
        return True
    return voice is not None and (setting.options_key or setting.read) in voice


def option_ids(voice: dict[str, Any] | None, key: str) -> list[str]:
    """Ids from a voice-block list of {"id", "phrase"}; anything malformed is skipped."""
    items = (voice or {}).get(key)
    if not isinstance(items, list):
        return []
    return [i["id"] for i in items if isinstance(i, dict) and isinstance(i.get("id"), str) and i["id"]]


# ----------------------------------------------------------- local time phrases

def _clock(dt: datetime) -> str:
    """'2:30 PM' (no leading zero, locale independent)."""
    hour12 = dt.hour % 12 or 12
    return f"{hour12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def local_phrase(when: datetime, now: datetime) -> str:
    """'today at 2:30 PM' / 'yesterday at ...' / 'tomorrow at ...' / 'Mon 23 Nov at ...'.

    `when` and `now` must both be in the user's local zone (dt_util.as_local).
    """
    day_delta = (when.date() - now.date()).days
    if day_delta == 0:
        day = "today"
    elif day_delta == -1:
        day = "yesterday"
    elif day_delta == 1:
        day = "tomorrow"
    else:
        day = f"{when.strftime('%a')} {when.day} {when.strftime('%b')}"
    return f"{day} at {_clock(when)}"


def parse_arrival_times(text: str | None) -> list[time]:
    """'06:30, 18:00' -> [06:30, 18:00]. Bad items are skipped; sorted, unique."""
    out: set[time] = set()
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hh, _, mm = part.partition(":")
            out.add(time(int(hh), int(mm or 0)))
        except ValueError:
            continue
    return sorted(out)


def next_arrival(times: list[time], now: datetime) -> datetime | None:
    """The next daily arrival strictly after `now` (same tzinfo as now)."""
    if not times:
        return None
    for day in (0, 1):
        for t in times:
            cand = datetime.combine(now.date() + timedelta(days=day), t, tzinfo=now.tzinfo)
            if cand > now:
                return cand
    return None


# ------------------------------------------------------------------- aliases

def level_aliases(tank: str, single: bool) -> list[str]:
    names = [f"{tank} level", f"{tank} water level", f"{tank} ka level"]
    names += [f"{tank} का लेवल", f"{tank} में पानी का स्तर"]
    if single:
        names += ["water level", "tank level", "टंकी का लेवल", "पानी का लेवल", "पानी का स्तर"]
    return names


def volume_aliases(tank: str, single: bool) -> list[str]:
    names = [f"water in the {tank}", f"{tank} water volume", f"{tank} में कितना पानी है"]
    if single:
        names += ["water in the tank", "टंकी में कितना पानी है"]
    return names


def used_today_aliases(tank: str, single: bool) -> list[str]:
    names = [f"{tank} water used today", f"{tank} aaj ka paani"]
    if single:
        names += ["water used today", "आज कितना पानी खर्च हुआ"]
    return names


def used_week_aliases(tank: str, single: bool) -> list[str]:
    names = [f"{tank} water used this week", f"{tank} is hafte ka paani"]
    if single:
        names += ["water used this week", "इस हफ्ते कितना पानी खर्च हुआ"]
    return names


def last_fill_aliases(tank: str, single: bool) -> list[str]:
    names = [f"{tank} last filled", f"when was the {tank} last filled"]
    if single:
        names += ["when was the tank last filled", "last time the tank was filled",
                  "टंकी आखिरी बार कब भरी"]
    return names


def pump_aliases(pump: str, single: bool) -> list[str]:
    names = [pump]
    if single:
        names += ["pump", "motor", "पंप", "मोटर"]
    return names


def next_arrival_aliases() -> list[str]:
    return ["next water arrival", "when does the water come next", "पानी कब आएगा"]


def speaker_aliases(name: str) -> list[str]:
    names = [name, "speaker", "स्पीकर"]
    if name.casefold() != "auvra":
        names.append("Auvra")
    return names


def dedupe_aliases(existing: list[str], new: list[str]) -> list[str]:
    """`new` minus anything already in `existing` (case-insensitive), in order."""
    have = {a.casefold() for a in existing}
    out: list[str] = []
    for a in new:
        if a.casefold() not in have:
            have.add(a.casefold())
            out.append(a)
    return out


# ------------------------------------------------------------ usage periods

def day_start(now: datetime) -> datetime:
    """Local midnight of `now`'s day."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(now: datetime) -> datetime:
    """Local midnight of the Monday of `now`'s week."""
    return day_start(now) - timedelta(days=now.weekday())
