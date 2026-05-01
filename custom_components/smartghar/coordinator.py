"""DataUpdateCoordinator for SmartGhar Hub polling.

One coordinator per hub (config entry). Polls /api/v1/info and
/api/v1/devices on every refresh. Real-time push lands in v0.2.0
when the firmware exposes the WebSocket stream.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SmartGharApiError, SmartGharCannotConnect, SmartGharHubClient
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SmartGharCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one SmartGhar Hub on a fixed interval."""

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

        return {"info": info, "devices": devices}

    @property
    def info(self) -> dict[str, Any]:
        """Convenience accessor for the latest /info payload."""
        return self.data.get("info", {}) if self.data else {}

    @property
    def devices(self) -> list[dict[str, Any]]:
        """Convenience accessor for the latest /devices payload."""
        return self.data.get("devices", []) if self.data else []

    def device_by_id(self, device_id: int) -> dict[str, Any] | None:
        """Find a device by its hub-side id (LoRa address)."""
        for d in self.devices:
            if d.get("id") == device_id:
                return d
        return None
