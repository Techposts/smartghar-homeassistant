"""Pump history helpers: pure functions over the hub's /api/history/pump JSON.

Day rows: {"d": YYYYMMDD local, "a": switch address, "s": run seconds,
"wh": watt-hours, "n": starts, "dry": dry-run stops, "f": other fault stops}.
Run rows: {"a", "t" start epoch, "tc" 1 if the clock was known, "dur" seconds,
"wh", "t0"/"t1" tank % at start / stop (null if unknown), "src", "stop",
"open" 1 while still running, "notime" 1 if it can never be placed in time}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

# Why a run stopped. 0-8 are the hub's switch stop reasons, 9 is the time rule
# (allowed hours ended), 20-22 exist only in history.
STOP_TEXT: dict[int, str] = {
    0: "",
    1: "tank full",
    2: "max runtime reached",
    3: "dry-run protection",
    4: "source tank empty",
    5: "manual",
    6: "switch fault",
    7: "lost tank data while pumping",
    8: "switch stopped itself (lost the Hub or restarted)",
    9: "allowed hours ended",
    20: "the Hub restarted during the run",
    21: "the switch stopped reporting during the run",
    22: "the switch turned off by itself",
}

# How a run started.
SRC_TEXT: dict[int, str] = {
    0: "unknown",
    1: "automatic (tank level)",
    2: "button on the switch",
    3: "Hub web page",
    4: "TankSync app",
    5: "USB",
    6: "Home Assistant",
    7: "another app",
}


def _day_date(day: int) -> date | None:
    try:
        return date(day // 10000, day // 100 % 100, day % 100)
    except (ValueError, TypeError):
        return None


def clock_known(hist: dict[str, Any] | None) -> bool:
    return bool(hist and hist.get("clock") and hist.get("today"))


def day_row(hist: dict[str, Any], addr: int, day: int) -> dict[str, Any] | None:
    for row in hist.get("days") or []:
        if row.get("a") == addr and row.get("d") == day:
            return row
    return None


def today_totals(hist: dict[str, Any], addr: int) -> dict[str, float]:
    """Totals for the hub's local today. A new day has no row yet: all zero,
    which HA reads as a meter reset (TOTAL_INCREASING)."""
    row = day_row(hist, addr, hist.get("today", 0)) or {}
    return {
        "wh": float(row.get("wh") or 0),
        "run_s": float(row.get("s") or 0),
        "starts": int(row.get("n") or 0),
    }


def dry_stops_7d(hist: dict[str, Any], addr: int) -> int:
    today = _day_date(hist.get("today", 0))
    if today is None:
        return 0
    first = today - timedelta(days=6)
    total = 0
    for row in hist.get("days") or []:
        d = _day_date(row.get("d", 0))
        if row.get("a") == addr and d is not None and first <= d <= today:
            total += int(row.get("dry") or 0)
    return total


def run_day(hist: dict[str, Any], run: dict[str, Any]) -> int | None:
    """Local YYYYMMDD of a run's start, using the hub's UTC offset."""
    if not run.get("tc") or run.get("notime"):
        return None
    off = int(hist.get("utc_off") or 0)
    dt = datetime.fromtimestamp(int(run["t"]) + off, tz=timezone.utc)
    return dt.year * 10000 + dt.month * 100 + dt.day


def run_end(run: dict[str, Any]) -> datetime | None:
    if not run.get("tc") or run.get("notime") or run.get("open"):
        return None
    return datetime.fromtimestamp(int(run["t"]) + int(run.get("dur") or 0), tz=timezone.utc)


def run_litres(run: dict[str, Any], capacity_l: float | None) -> float | None:
    """Litres moved into the tank during a run, from its level rise."""
    t0, t1 = run.get("t0"), run.get("t1")
    if not capacity_l or t0 is None or t1 is None or t1 <= t0:
        return None
    return round(float(capacity_l) * (t1 - t0) / 100.0, 1)


def run_attributes(run: dict[str, Any], capacity_l: float | None) -> dict[str, Any]:
    t0, t1 = run.get("t0"), run.get("t1")
    attrs: dict[str, Any] = {
        "duration_min": round(int(run.get("dur") or 0) / 60, 1),
        "energy_kwh": round(float(run.get("wh") or 0) / 1000, 3),
        "started_by": SRC_TEXT.get(run.get("src", 0), "unknown"),
        "stopped_because": STOP_TEXT.get(run.get("stop", 0), "unknown"),
        "tank_start_pct": t0,
        "tank_end_pct": t1,
    }
    water = run_litres(run, capacity_l)
    if water is not None:
        attrs["water_l"] = water
    return attrs
