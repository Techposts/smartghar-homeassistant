"""DataUpdateCoordinator for SmartGhar Hub.

Hybrid push + polling:
  - Background WebSocket task (`_ws_runner`) subscribes to /api/v1/stream and
    fires async_set_updated_data() on every snapshot frame (~3s).
  - Periodic poll (every 30s) catches up if the WS dropped, and refreshes the
    static-ish fields that aren't in WS snapshots (hub_id, fw_version, claimed,
    device_kinds, ota.current/channel).
  - On WS disconnect, exponential backoff up to 60s.

This pairs with hub firmware rx-v2.7.0 Phase 1.3 (WebSocket /api/v1/stream).
Older firmware versions silently fall back to polling-only — the WS task
fails to connect and just retries; nothing breaks.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SmartGharApiError, SmartGharCannotConnect, SmartGharHubClient
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

WS_BACKOFF_INITIAL_S = 2.0
WS_BACKOFF_MAX_S = 60.0


class SmartGharCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one SmartGhar Hub on a fixed interval, plus optional WS push."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SmartGharHubClient,
        hub_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{hub_id}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.hub_id = hub_id
        self._ws_task: asyncio.Task | None = None
        self._ws_connected: bool = False
        self._stop = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            info, devices = await asyncio.gather(
                self.client.get_info(),
                self.client.get_devices(),
            )
        except SmartGharCannotConnect as err:
            raise UpdateFailed(f"Hub {self.hub_id} unreachable: {err}") from err
        except SmartGharApiError as err:
            raise UpdateFailed(f"Hub {self.hub_id} API error: {err}") from err

        led: dict[str, Any] = {}
        try:
            led = await self.client.get_led()
        except SmartGharApiError as err:
            _LOGGER.debug("LED state unavailable on hub %s: %s", self.hub_id, err)

        return {"info": info, "devices": devices, "led": led}

    @property
    def info(self) -> dict[str, Any]:
        return self.data.get("info", {}) if self.data else {}

    @property
    def devices(self) -> list[dict[str, Any]]:
        return self.data.get("devices", []) if self.data else []

    @property
    def led(self) -> dict[str, Any]:
        return self.data.get("led", {}) if self.data else {}

    @property
    def ws_connected(self) -> bool:
        """True when the real-time push channel is live."""
        return self._ws_connected

    def device_by_id(self, device_id: int) -> dict[str, Any] | None:
        for d in self.devices:
            if d.get("id") == device_id:
                return d
        return None

    # ── WebSocket push ────────────────────────────────────────────────────────

    def start_ws(self) -> None:
        """Spawn the WS background task. Idempotent."""
        if self._ws_task and not self._ws_task.done():
            return
        self._stop = False
        self._ws_task = self.hass.async_create_background_task(
            self._ws_runner(), name=f"smartghar_ws_{self.hub_id}"
        )

    async def stop_ws(self) -> None:
        self._stop = True
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None
        self._ws_connected = False

    async def _ws_runner(self) -> None:
        backoff = WS_BACKOFF_INITIAL_S
        while not self._stop:
            try:
                ws = await self.client.connect_ws()
            except Exception as err:
                _LOGGER.debug(
                    "WS connect to hub %s failed: %s — retrying in %.1fs",
                    self.hub_id, err, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, WS_BACKOFF_MAX_S)
                continue

            self._ws_connected = True
            backoff = WS_BACKOFF_INITIAL_S
            _LOGGER.info("WS connected to hub %s", self.hub_id)

            try:
                async for raw in ws:
                    if raw.type == aiohttp.WSMsgType.TEXT:
                        try:
                            msg = raw.json()
                        except ValueError:
                            continue
                        self._handle_ws_msg(msg)
                    elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except Exception as err:
                _LOGGER.debug("WS read loop ended for hub %s: %s", self.hub_id, err)
            finally:
                self._ws_connected = False
                try:
                    await ws.close()
                except Exception:
                    pass

            if not self._stop:
                _LOGGER.info("WS dropped for hub %s — reconnecting in %.1fs", self.hub_id, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, WS_BACKOFF_MAX_S)

    def _handle_ws_msg(self, msg: dict[str, Any]) -> None:
        """Apply one WS frame to coordinator state and fan out to entities."""
        kind = msg.get("kind")
        if kind == "hello":
            # Validate schema_version compatibility (purely advisory for now).
            schema = msg.get("schema_version", "1.0")
            if not schema.startswith("1."):
                _LOGGER.warning(
                    "Hub %s announced schema %s — this integration speaks 1.x",
                    self.hub_id, schema,
                )
            return

        if kind != "snapshot":
            # Future kinds (device_state, fill_event, etc.) — ignore for now.
            return

        if not self.data:
            # Polling hasn't completed yet; let it set the baseline.
            return

        # Merge dynamic fields from WS into the existing /info envelope.
        # Static fields (hub_id, fw_version, schema_version, ota.current,
        # ota.channel, claimed, device_kinds) survive from the last poll.
        hub_dynamic = msg.get("hub") or {}
        new_info = {**self.data.get("info", {})}
        for key in ("uptime_s", "wifi_rssi"):
            if key in hub_dynamic:
                new_info[key] = hub_dynamic[key]
        if "ota_available" in hub_dynamic:
            ota = {**(new_info.get("ota") or {})}
            ota["available"] = (
                ota.get("current") and hub_dynamic["ota_available"]
            ) or None
            new_info["ota"] = ota

        new_data = {
            "info": new_info,
            "devices": msg.get("devices", self.data.get("devices", [])),
            "led": self.data.get("led", {}),
        }
        self.async_set_updated_data(new_data)
