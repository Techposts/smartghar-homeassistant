"""Constants for the SmartGhar integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "smartghar"
MANUFACTURER = "SmartGhar"

# Spec version this integration speaks. Hub firmware advertises its
# schema_version on /api/v1/info; if it ever bumps to 2.x, this integration
# would need updating. 1.1 added `topology` + `stream` blocks (alpha.4).
# 1.2 (additive) adds Auvra capabilities: eq, radio, url_play, announce.
PROTOCOL_VERSION = "1.2"
ZEROCONF_TYPE = "_smartghar._tcp.local."

DEFAULT_PORT = 80
# 10 s: an Auvra on a busy 2.4 GHz AP showed 3 s ping spikes and 5 s HTTP
# answers during the 0.10 e2e; at 5 s the command succeeded but HA reported a
# failure.
DEFAULT_TIMEOUT_S = 10.0

# Polling intervals — water levels change slowly so 30s is plenty. Real-time
# push lands in v0.2.0 once firmware Phase 1.3 ships the WebSocket endpoint.
SCAN_INTERVAL = timedelta(seconds=30)

# Device-kind taxonomy (mirrors LoRa device_kind byte in hub firmware).
DEVICE_KIND_TANK = "tank"
DEVICE_KIND_POWER = "power"
DEVICE_KIND_PUMP_RELAY = "pump_relay"
DEVICE_KIND_GAS = "gas"
DEVICE_KIND_SOIL = "soil"
DEVICE_KIND_DOOR = "door"
DEVICE_KIND_AIR = "air"
# AmbiSense — radar presence + LED follow-me. Standalone hub (single
# ESP32 advertising itself) presents one virtual sub-device of this kind.
DEVICE_KIND_PRESENCE = "presence"
# SmartGhar Smart Switch — mains relay + current/temp sensing controlling a
# pump. Polled from /api/switches (NOT the tank device list); the hub runs the
# pump automation and HA exposes manual control + telemetry.
DEVICE_KIND_SWITCH = "switch"
# Auvra — AirPlay 2 streamer. Standalone hub (product="auvra") presenting one
# device of this kind (id=1): transport, volume, now-playing + artwork.
DEVICE_KIND_SPEAKER = "speaker"

# Config keys
CONF_HUB_ID = "hub_id"
CONF_LOCAL_TOKEN = "local_token"
# USB-CDC coordinator ("HA stick") entries — see serial_link/serial_coordinator.
CONF_CONNECTION = "connection"       # absent/"lan" = HTTP hub, "usb" = serial stick
CONF_SERIAL_PORT = "serial_port"     # /dev/serial/by-id/… or any pyserial URL (socket://…)
CONNECTION_LAN = "lan"
CONNECTION_USB = "usb"

# Model strings for HA device registry — visible in Settings → Devices.
# Hub model is dispatched by the `product` field from /api/v1/info so a
# single integration shows the right name for each Techposts product.
MODEL_HUB_TANKSYNC = "TankSync Hub"
MODEL_HUB_AMBISENSE = "AmbiSense Hub"
MODEL_HUB_AUVRA = "Auvra"
MODEL_HUB_GENERIC = "SmartGhar Hub"

MODEL_TANK = "TankSync TX (Tank)"
MODEL_PRESENCE = "AmbiSense Sensor"
MODEL_SWITCH = "SmartGhar Smart Switch"
# Only used if an Auvra ever reports topology="hub"; standalone collapses the
# speaker onto the hub card, whose model comes from hub_model_for_product().
MODEL_SPEAKER = "Auvra"


def hub_model_for_product(product: str | None) -> str:
    """Pick the device-registry model string for the hub based on the
    `product` field returned by /api/v1/info. Unknown or missing
    products fall through to the generic SmartGhar label rather than
    silently mislabeling them as TankSync."""
    if product == "tanksync":
        return MODEL_HUB_TANKSYNC
    if product == "ambisense":
        return MODEL_HUB_AMBISENSE
    if product == "auvra":
        return MODEL_HUB_AUVRA
    return MODEL_HUB_GENERIC
