"""Coordinator for a hub connected over USB-CDC serial (the "HA stick").

Wraps SerialCoordinatorLink (proto v1 NDJSON) in a DataUpdateCoordinator so
the entity layer gets the familiar push/poll surface:

  data = {
      "hub":   {device_id, fw, model, transports, max_nodes},
      "nodes": {node_id: Node, ...},          # live objects from serial_link
  }

Telemetry is PUSH (async_set_updated_data on every event); the periodic
"update" is just a ping + node snapshot refresh so a silently-dead port is
detected within a minute. On disconnect the coordinator reconnects with
backoff — a hub reboot (OTA, power blip) self-heals.

The port may be a real device path (/dev/serial/by-id/…) or any pyserial URL
(e.g. socket://host:port for ser2net / remote bridges) — serial_asyncio passes
it through to serial_for_url.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .serial_link import (
    Node,
    SerialCoordinatorLink,
    SerialLinkError,
)

_LOGGER = logging.getLogger(__name__)

RECONNECT_BACKOFF_INITIAL_S = 2.0
RECONNECT_BACKOFF_MAX_S = 60.0
SERIAL_SCAN_INTERVAL = timedelta(seconds=60)


class SmartGharSerialCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One USB-CDC coordinator hub."""

    def __init__(self, hass: HomeAssistant, port: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_serial",
            update_interval=SERIAL_SCAN_INTERVAL,
        )
        self.port = port
        self.link: SerialCoordinatorLink | None = None
        self.hub: dict[str, Any] = {}
        self._runner: asyncio.Task | None = None
        self._stop = False
        self._link_dead = False    # set by on_disconnect; cleared on reopen

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @staticmethod
    def _preimport_serial() -> None:
        """Import pyserial's lazily-loaded pieces off the event loop.

        serial_for_url() imports serial.urlhandler.* on first use — inside the
        loop that trips HA's blocking-import detector. One executor call at
        setup keeps every later open() clean."""
        import serial  # noqa: F401
        try:
            import serial.urlhandler.protocol_socket  # noqa: F401
        except ImportError:
            pass
        try:
            import serial_asyncio_fast  # noqa: F401
        except ImportError:
            try:
                import serial_asyncio  # noqa: F401
            except ImportError:
                pass

    async def async_connect(self) -> None:
        """First connection — raises on failure so the config entry retries."""
        await self.hass.async_add_executor_job(self._preimport_serial)
        await self._open()
        self._runner = self.hass.loop.create_task(self._reconnect_runner())

    async def async_shutdown_link(self) -> None:
        self._stop = True
        if self._runner:
            self._runner.cancel()
        if self.link:
            await self.link.close()
            self.link = None

    async def _open(self) -> None:
        link = await SerialCoordinatorLink.open(self.port)
        link.on_telemetry = self._on_push
        link.on_node = self._on_push
        link.on_disconnect = self._on_disconnect
        info = await link.start()
        await link.refresh_nodes()
        self.link = link
        self.hub = {
            "device_id": info.device_id,
            "fw": info.fw,
            "model": info.model,
            "transports": info.transports,
            "max_nodes": info.max_nodes,
        }
        # Register the coordinator hub itself as a device so per-node devices
        # can point their via_device at it (unregistered, HA logs a
        # "referencing a non existing via_device" warning per node).
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, f"{info.device_id}-coordinator")},
            manufacturer="SmartGhar",
            model=info.model,
            name=f"TankSync Coordinator {info.device_id[-4:]}",
            sw_version=info.fw,
        )
        self.async_set_updated_data(self._snapshot())
        _LOGGER.info(
            "Serial hub %s connected on %s (fw %s, %d nodes)",
            info.device_id, self.port, info.fw, len(link.nodes),
        )

    # ── push + reconnect plumbing ─────────────────────────────────────────────

    def _snapshot(self) -> dict[str, Any]:
        return {"hub": self.hub, "nodes": dict(self.link.nodes) if self.link else {}}

    @callback
    def _on_push(self, _node: Node) -> None:
        self.async_set_updated_data(self._snapshot())

    @callback
    def _on_disconnect(self) -> None:
        # A read-EOF leaves the writer looking healthy, so an explicit flag is
        # the only reliable death signal for the reconnect runner.
        self._link_dead = True
        _LOGGER.warning("Serial hub link on %s dropped — reconnecting", self.port)

    async def _reconnect_runner(self) -> None:
        """Re-open the port whenever the session dies (hub reboot/unplug)."""
        backoff = RECONNECT_BACKOFF_INITIAL_S
        while not self._stop:
            await asyncio.sleep(1.0)
            link = self.link
            if link is not None and link.info is not None and not self._link_dead:
                healthy = True
                try:
                    healthy = not link._writer.is_closing()  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    healthy = False
                if healthy:
                    backoff = RECONNECT_BACKOFF_INITIAL_S
                    continue
            try:
                if self.link:
                    await self.link.close()
                    self.link = None
                await self._open()
                self._link_dead = False
                backoff = RECONNECT_BACKOFF_INITIAL_S
            except (SerialLinkError, OSError, Exception) as err:  # noqa: BLE001
                _LOGGER.debug("Serial reconnect to %s failed: %s", self.port, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)

    # ── periodic health poll ──────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.link:
            raise UpdateFailed(f"Serial hub on {self.port} not connected")
        try:
            await self.link.ping()
            await self.link.refresh_nodes()
        except (SerialLinkError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Serial hub on {self.port}: {err}") from err
        return self._snapshot()
