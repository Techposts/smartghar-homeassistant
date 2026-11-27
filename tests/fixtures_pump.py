"""Fixtures for pump history and time rules.

LIVE_* are verbatim answers of the TankSync hub at 192.168.0.100 (v3.8.0, switch
BAD8, Tank 3 of 1000 L) on 27 Nov 2026: that hub had no pump runs yet, so its
history is empty. HISTORY_RUNS is the same envelope with rows shaped from the
firmware (web_server.c handle_api_history_pump). The time-rule fields are built
from docs/plans/2026-11-28-pump-time-rules.md: the hub does not send them yet.
"""
from __future__ import annotations

import copy
from typing import Any

from .fixtures import HUB_ID

INFO: dict[str, Any] = {
    "schema_version": "1.1", "manufacturer": "SmartGhar", "product": "tanksync",
    "model": "TankSync Hub", "fw_version": "3.8.0", "hub_id": HUB_ID,
    "hub_name": "TankSync Hub", "topology": "hub", "uptime_s": 10, "wifi_rssi": -40,
    "ota": {"current": "3.8.0", "available": False, "latest": "", "channel": "stable"},
    "stream": {"ws_path": "/api/v1/stream"}, "capabilities": [],
}

LIVE_DEVICES: dict[str, Any] = {"devices": [{
    "id": 3, "kind": "tank", "name": "Tank 3", "fw": "3.7.1", "mac": "c8da290b9828",
    "state": {"level_pct": 35, "voltage": 3.72, "rssi": -25, "conn_state": "online"},
    "config": {"capacity_l": 1000},
}]}

LIVE_SWITCHES: dict[str, Any] = {"switches": [{
    "address": 1, "name": "Smart Switch BAD8", "relay_on": False, "relay_desired": 0,
    "load_ma": 0, "power_w": 0, "temp_c10": 0, "fault": 0, "vrms": None, "fw_version": "3.7.1",
    "stop_reason": 0, "runtime_s": 0, "rest_left_s": 0, "dry_left_s": 0, "arming": False,
    "live_wait": False, "fast": False, "hold_left_s": 0, "state": "waiting",
    "rule": {"enabled": True, "tank_addr": 3, "start_pct": 30, "stop_pct": 95, "source_addr": 0,
             "source_min_pct": 10, "max_runtime_s": 1800, "min_off_s": 120, "manual_hold": False,
             "mains_v": 230, "oc_limit_ma": 11500, "dry_min_ma": 0, "fast_s": 20,
             "failsafe_s": 60, "sound": 1},
}]}

LIVE_HISTORY_EMPTY: dict[str, Any] = {
    "ok": True, "available": True, "clock": True, "now": 1795803198, "utc_off": 19800,
    "today": 20261127, "tariff": 0.00, "demo": False, "total": 0, "days": [], "runs": [],
}

# 2026-11-27 12:00 IST = 06:30 UTC = epoch 1795761000.
NOW = 1795761000
_MORNING = 1795739400  # 06:00 IST (00:30 UTC)


def history_runs(today: int = 20261127) -> dict[str, Any]:
    """Two finished runs today and one open run, plus earlier days, switch 1."""
    return {
        "ok": True, "available": True, "clock": True, "now": NOW, "utc_off": 19800,
        "today": today, "tariff": 8.5, "demo": False, "total": 5,
        "days": [
            {"d": 20261127, "a": 1, "s": 2400, "wh": 1500.0, "n": 2, "dry": 0, "f": 0},
            {"d": 20261126, "a": 1, "s": 1800, "wh": 1100.0, "n": 1, "dry": 1, "f": 0},
            {"d": 20261124, "a": 1, "s": 900, "wh": 500.0, "n": 1, "dry": 2, "f": 0},
            {"d": 20261120, "a": 1, "s": 900, "wh": 500.0, "n": 1, "dry": 4, "f": 0},  # > 7 days ago
        ],
        "runs": [
            {"a": 1, "t": NOW - 120, "tc": 1, "dur": 120, "wh": 60.0, "avg": 1800, "pk": 2000,
             "ma": 9000, "v0": 228, "v1": 230, "t0": 40, "t1": None, "s0": None, "s1": None,
             "src": 1, "stop": 0, "fault": 0, "open": 1, "demo": 0, "gaps": 0, "notime": 0},
            {"a": 1, "t": _MORNING + 1800, "tc": 1, "dur": 1500, "wh": 900.0, "avg": 2100,
             "pk": 2300, "ma": 10200, "v0": 228, "v1": 230, "t0": 30, "t1": 55, "s0": None,
             "s1": None, "src": 1, "stop": 9, "fault": 0, "open": 0, "demo": 0, "gaps": 0,
             "notime": 0},
            {"a": 1, "t": _MORNING, "tc": 1, "dur": 900, "wh": 600.0, "avg": 2400, "pk": 2500,
             "ma": 10900, "v0": 228, "v1": 229, "t0": 20, "t1": 30, "s0": None, "s1": None,
             "src": 3, "stop": 1, "fault": 0, "open": 0, "demo": 0, "gaps": 0, "notime": 0},
        ],
    }


def history_no_clock() -> dict[str, Any]:
    h = copy.deepcopy(LIVE_HISTORY_EMPTY)
    h.update(clock=False, today=0, utc_off=0)
    return h


def switches_online() -> dict[str, Any]:
    """The live switch once it reports (state "online"; "waiting" hides attributes)."""
    sw = copy.deepcopy(LIVE_SWITCHES)
    sw["switches"][0]["state"] = "online"
    return sw


def switches_with_time_rules(**over: Any) -> dict[str, Any]:
    sw = switches_online()
    sw["switches"][0].update({
        "in_allowed_hours": True,
        "next_auto_start": None,
        "next_topup": 1795824000,       # 2026-11-28 05:30 IST
        "time_wait": "waiting for 06:00",
        "time_rules_paused": False,
    })
    sw["switches"][0].update(over)
    return sw
