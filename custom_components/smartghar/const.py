"""Constants for the SmartGhar integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "smartghar"
MANUFACTURER = "SmartGhar"

# Spec version this integration speaks. Hub firmware advertises its
# schema_version on /api/v1/info; if it ever bumps to 2.x, this integration
# would need updating.
PROTOCOL_VERSION = "1.0"
ZEROCONF_TYPE = "_smartghar._tcp.local."

DEFAULT_PORT = 80
DEFAULT_TIMEOUT_S = 5.0

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

# Config keys
CONF_HUB_ID = "hub_id"
CONF_LOCAL_TOKEN = "local_token"

# Model strings for HA device registry — visible in Settings → Devices.
# Hub model is dispatched by the `product` field from /api/v1/info so a
# single integration shows the right name for each Techposts product.
MODEL_HUB_TANKSYNC = "TankSync Hub"
MODEL_HUB_AMBISENSE = "AmbiSense Hub"
MODEL_HUB_GENERIC = "SmartGhar Hub"
# Backward-compat alias — older code paths in this integration import
# MODEL_HUB directly. Kept pointing at the TankSync model since that's
# what was running in the field before AmbiSense joined.
MODEL_HUB = MODEL_HUB_TANKSYNC

MODEL_TANK = "TankSync TX (Tank)"
MODEL_PRESENCE = "AmbiSense Sensor"


def hub_model_for_product(product: str | None) -> str:
    """Pick the device-registry model string for the hub based on the
    `product` field returned by /api/v1/info. Older firmware that
    doesn't include a product field falls through to the TankSync
    label since that was the original product."""
    return {
        "tanksync": MODEL_HUB_TANKSYNC,
        "ambisense": MODEL_HUB_AMBISENSE,
    }.get(product or "tanksync", MODEL_HUB_GENERIC)
