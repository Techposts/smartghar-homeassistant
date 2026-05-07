"""Topology-aware DeviceInfo construction.

The hub firmware advertises its physical topology via /api/v1/info.topology:

  - "standalone" — the hub IS the device. AmbiSense (one ESP32-C3 with an
    integrated radar) is the canonical example. Sub-device entities
    collapse onto the hub's HA device entry — no via_device link, no
    second card in Settings → Devices.

  - "hub" — the hub aggregates N sub-devices over LoRa/ESP-NOW/etc.
    Each sub-device renders as its own HA device with via_device
    pointing back at the hub. TankSync (battery TX nodes), future
    PowerSync (CT clamps).

A missing topology field defaults to "hub" — preserves the original
TankSync rendering for older firmware versions still in the field.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, MANUFACTURER, hub_model_for_product

if TYPE_CHECKING:
    from .coordinator import SmartGharCoordinator


TOPOLOGY_STANDALONE = "standalone"
TOPOLOGY_HUB = "hub"


def hub_device_info(coordinator: SmartGharCoordinator) -> DeviceInfo:
    """DeviceInfo for the hub itself.

    Used by every hub-level entity (OTA-available, LED brightness,
    uptime, wifi_rssi, firmware update). Always exactly one HA device
    per coordinator — keyed by hub_id so reboots, IP changes, and
    re-pairings preserve the same registry entry.
    """
    info = coordinator.info
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.hub_id)},
        manufacturer=MANUFACTURER,
        model=hub_model_for_product(info.get("product")),
        name=info["hub_name"],
        sw_version=info.get("fw_version"),
        configuration_url=f"http://{coordinator.client.host}/",
    )


def subdevice_device_info(
    coordinator: SmartGharCoordinator,
    dev: dict[str, Any],
    *,
    sub_model: str,
    fallback_name: str,
) -> DeviceInfo:
    """DeviceInfo for a sub-device entity.

    For "standalone" topology, returns the hub's DeviceInfo verbatim —
    HA merges the sub-device entities under the hub. The sub-device
    has no independent registry entry. This is correct for AmbiSense:
    the hub *is* the radar; splitting them into two HA devices was a
    TankSync-shaped artifact.

    For "hub" topology, returns a child DeviceInfo with via_device
    linking back to the hub. The sub-device gets its own card with
    its own model + name. Correct for TankSync (each tank is a real
    physical TX node).
    """
    info = coordinator.info
    topology = info.get("topology") or TOPOLOGY_HUB
    if topology == TOPOLOGY_STANDALONE:
        return hub_device_info(coordinator)

    return DeviceInfo(
        identifiers={(DOMAIN, _subdevice_identifier(coordinator, dev))},
        via_device=(DOMAIN, coordinator.hub_id),
        manufacturer=MANUFACTURER,
        model=sub_model,
        name=dev.get("name") or fallback_name,
    )


def _subdevice_identifier(
    coordinator: SmartGharCoordinator, dev: dict[str, Any]
) -> str:
    """Stable HA device-registry identifier for a sub-device.

    Format: "{hub_id}_{kind}_{id}". Kind is included because two
    sub-devices of different kinds could collide on numeric id
    otherwise (lock id=0 + presence id=0 on a hypothetical multi-
    function hub).
    """
    kind = dev.get("kind") or "device"
    return f"{coordinator.hub_id}_{kind}_{dev['id']}"
