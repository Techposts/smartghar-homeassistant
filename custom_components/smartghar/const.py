"""Constants for the SmartGhar integration."""
from __future__ import annotations

DOMAIN = "smartghar"
MANUFACTURER = "SmartGhar"

PROTOCOL_VERSION = "1.0"
ZEROCONF_TYPE = "_smartghar._tcp.local."

DEFAULT_PORT = 80
DEFAULT_TIMEOUT_S = 5.0

# Device kind taxonomy — mirrors the LoRa device_kind byte in hub firmware.
DEVICE_KIND_TANK = "tank"
DEVICE_KIND_POWER = "power"
DEVICE_KIND_PUMP_RELAY = "pump_relay"
DEVICE_KIND_GAS = "gas"
DEVICE_KIND_SOIL = "soil"
DEVICE_KIND_DOOR = "door"
DEVICE_KIND_AIR = "air"

# Config keys
CONF_HUB_ID = "hub_id"
CONF_LOCAL_TOKEN = "local_token"
