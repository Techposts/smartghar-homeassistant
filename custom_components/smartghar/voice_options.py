"""Option keys for the voice features (Options flow and add flow)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

from .voice_logic import parse_arrival_times

CONF_VOICE_READY = "voice_ready"
CONF_VOICE_SATELLITE = "voice_satellite"
CONF_ARRIVAL_TIMES = "water_arrival_times"


def voice_ready_enabled(entry: ConfigEntry) -> bool:
    """Voice-ready defaults to ON, also for entries made before the option existed."""
    return bool(entry.options.get(CONF_VOICE_READY, True))


def voice_satellite_enabled(entry: ConfigEntry) -> bool:
    """The integration drives the unit's Wyoming satellite itself (default ON)."""
    return bool(entry.options.get(CONF_VOICE_SATELLITE, True))


def arrival_times(entry: ConfigEntry):
    return parse_arrival_times(entry.options.get(CONF_ARRIVAL_TIMES))
