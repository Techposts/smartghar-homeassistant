#!/usr/bin/env python3
"""Unit checks for the Auvra speaker state mapping (speaker_logic.py).

Runs WITHOUT Home Assistant: speaker_logic has no HA imports, and the
media_player entity only wraps its results in MediaPlayerState. Mirrors the
style of test_serial_link_sim.py (plain asserts, pass/fail tally).

Usage:
  python3 tests/test_speaker_state_map.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "smartghar"))
from speaker_logic import (  # noqa: E402
    STATE_BUFFERING,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_STANDBY,
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

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def state(**overrides) -> dict:
    """A full speaker state dict as the firmware sends it, with overrides."""
    base = {
        "power": "on", "active": False, "playing": False, "sender": "",
        "volume": 40, "muted": False, "title": "", "artist": "", "album": "",
        "art_rev": 0, "position_ms": None, "duration_ms": None, "stall": False,
    }
    base.update(overrides)
    return base


def main() -> int:
    # ── State mapping ─────────────────────────────────────────────────────
    print("state mapping")
    s = speaker_state(state(power="standby"))
    check("standby -> standby", s == STATE_STANDBY, s)
    s = speaker_state(state(power="standby", active=True, playing=True))
    check("standby wins over active/playing", s == STATE_STANDBY, s)
    s = speaker_state(state())
    check("on, no sender -> idle", s == STATE_IDLE, s)
    s = speaker_state(state(playing=True))
    check("playing without a sender is still idle", s == STATE_IDLE, s)
    s = speaker_state(state(active=True, playing=True, sender="Ravi's iPhone"))
    check("active + playing -> playing", s == STATE_PLAYING, s)
    s = speaker_state(state(active=True, playing=False, sender="Ravi's iPhone"))
    check("active + not playing -> paused", s == STATE_PAUSED, s)
    s = speaker_state(state(active=True, playing=True, stall=True))
    check("stall while playing -> buffering", s == STATE_BUFFERING, s)
    s = speaker_state(state(active=True, playing=False, stall=True))
    check("stall while paused stays paused", s == STATE_PAUSED, s)
    check("no state -> None", speaker_state({}) is None and speaker_state(None) is None)
    check("enum value names match HA MediaPlayerState",
          (STATE_STANDBY, STATE_IDLE, STATE_PLAYING, STATE_PAUSED, STATE_BUFFERING)
          == ("standby", "idle", "playing", "paused", "buffering"))

    # ── Volume ────────────────────────────────────────────────────────────
    print("volume")
    check("volume 55 -> 0.55", volume_level(state(volume=55)) == 0.55, volume_level(state(volume=55)))
    check("volume 0 -> 0.0", volume_level(state(volume=0)) == 0.0)
    check("volume 100 -> 1.0", volume_level(state(volume=100)) == 1.0)
    check("volume 130 clamps to 1.0", volume_level(state(volume=130)) == 1.0)
    check("volume missing -> None", volume_level({}) is None and volume_level(None) is None)
    check("volume garbage -> None", volume_level(state(volume="loud")) is None)
    check("level 0.55 -> 55", volume_percent(0.55) == 55)
    check("level 0.004 rounds to 0", volume_percent(0.004) == 0)
    check("level 1.7 clamps to 100", volume_percent(1.7) == 100)
    check("level -0.2 clamps to 0", volume_percent(-0.2) == 0)
    check("round trip 0..100", all(volume_percent(volume_level(state(volume=v))) == v for v in range(101)))
    check("step up 40 -> 45", stepped_volume(40, VOLUME_STEP_PCT) == 45)
    check("step down 3 clamps to 0", stepped_volume(3, -VOLUME_STEP_PCT) == 0)
    check("step up 98 clamps to 100", stepped_volume(98, VOLUME_STEP_PCT) == 100)
    check("step from unknown -> None", stepped_volume(None, VOLUME_STEP_PCT) is None)

    # ── Metadata ──────────────────────────────────────────────────────────
    print("metadata")
    check('"" -> None', text_or_none("") is None)
    check("None -> None", text_or_none(None) is None)
    check("non-string -> None", text_or_none(7) is None)
    check("text passes through", text_or_none("Blue in Green") == "Blue in Green")
    check("position 61_500 ms -> 61 s", ms_to_seconds(61_500) == 61)
    check("duration 0 ms -> 0 s", ms_to_seconds(0) == 0)
    check("position None -> None", ms_to_seconds(None) is None)
    check("position negative -> None", ms_to_seconds(-5) is None)
    check("position bool -> None", ms_to_seconds(True) is None)
    check("position str digits -> seconds", ms_to_seconds("3000") == 3)

    # ── Artwork ───────────────────────────────────────────────────────────
    print("artwork")
    check("art_rev 0 -> no url", art_url("192.168.1.50", 0) is None)
    check("art_rev None -> no url", art_url("192.168.1.50", None) is None)
    check("art_rev 7 -> cache-busted url",
          art_url("192.168.1.50", 7) == "http://192.168.1.50/api/art.jpg?rev=7",
          art_url("192.168.1.50", 7))
    check("hostname works",
          art_url("auvra-0848.local", 12) == "http://auvra-0848.local/api/art.jpg?rev=12")
    check("non-default port is included",
          art_url("192.168.1.50", 3, port=8080) == "http://192.168.1.50:8080/api/art.jpg?rev=3")
    check("default port is omitted", art_url("192.168.1.50", 3, port=80) == "http://192.168.1.50/api/art.jpg?rev=3")
    check("empty host -> no url", art_url("", 3) is None)
    check("negative rev -> no url", art_url("192.168.1.50", -1) is None)
    check("hash follows rev", art_hash(7) == "7" and art_hash(0) is None and art_hash(None) is None)
    check("rev normalises", art_revision("4") == 4 and art_revision("x") == 0 and art_revision(True) == 0)
    check("url changes when rev changes",
          art_url("h", 1) != art_url("h", 2) and art_hash(1) != art_hash(2))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
