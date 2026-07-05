"""Shared base + dynamic-node plumbing for USB-CDC coordinator entities.

Nodes can join AFTER setup (pairing window) — each platform registers
`async_add_serial_nodes` with a factory; a coordinator listener adds entities
for nodes it hasn't seen yet, so a freshly-paired tank appears in HA without
a reload.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .serial_coordinator import SmartGharSerialCoordinator
from .serial_link import Node


class SmartGharSerialNodeEntity(CoordinatorEntity[SmartGharSerialCoordinator]):
    """Base for entities backed by one node behind a serial coordinator hub."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartGharSerialCoordinator, node_id: int) -> None:
        super().__init__(coordinator)
        self._node_id = node_id

    @property
    def node(self) -> Node | None:
        return (self.coordinator.data or {}).get("nodes", {}).get(self._node_id)

    @property
    def available(self) -> bool:
        node = self.node
        return super().available and self.coordinator.link is not None and node is not None

    @property
    def device_info(self) -> DeviceInfo:
        hub_id = self.coordinator.hub.get("device_id", "unknown")
        node = self.node
        name = node.name if node else f"Node {self._node_id}"
        model = ("SmartGhar Smart Switch" if node and node.device_type == "switch"
                 else "TankSync Sensor")
        return DeviceInfo(
            identifiers={(DOMAIN, f"{hub_id}-node{self._node_id}")},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
            via_device=(DOMAIN, f"{hub_id}-coordinator"),
            sw_version=node.fw if node else None,
        )

    def sensor_value(self, measure: str) -> Any:
        node = self.node
        if not node:
            return None
        entry = node.sensors.get(measure)
        return entry.get("value") if entry else None


def async_add_serial_nodes(
    coordinator: SmartGharSerialCoordinator,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[Node], Iterable[Any]],
) -> None:
    """Add entities for current nodes + any that appear later (pairing)."""
    known: set[int] = set()

    def _sync() -> None:
        nodes = (coordinator.data or {}).get("nodes", {})
        fresh = []
        for node_id, node in nodes.items():
            if node_id in known:
                continue
            known.add(node_id)
            fresh.extend(factory(node))
        if fresh:
            async_add_entities(fresh)

    _sync()
    coordinator.async_add_listener(_sync)
