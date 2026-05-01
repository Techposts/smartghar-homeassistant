"""HTTP client for the SmartGhar Hub local API.

Implements a thin async wrapper around the protocol v1 spec:
  https://github.com/Techposts/smartghar-homeassistant/blob/main/docs/protocol/v1.md

Bearer-token auth is optional (off by default on hubs). When the user
provides a token in the config flow, every request includes it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import DEFAULT_PORT, DEFAULT_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


class SmartGharApiError(Exception):
    """Base exception for hub API errors."""


class SmartGharCannotConnect(SmartGharApiError):
    """Raised when the hub is unreachable on the LAN."""


class SmartGharInvalidAuth(SmartGharApiError):
    """Raised when the local token is rejected."""


class SmartGharHubClient:
    """Async HTTP client for one SmartGhar Hub on the local network.

    Reuses the supplied aiohttp session (HA's shared client session) so we
    don't churn TCP connections.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        token: str | None = None,
        port: int = DEFAULT_PORT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base = URL.build(scheme="http", host=host, port=port)
        self._session = session
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    @property
    def host(self) -> str:
        """Return the hub's host (IP or .local hostname)."""
        return self._base.host or ""

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _get(self, path: str) -> dict[str, Any]:
        url = self._base / path.lstrip("/")
        try:
            async with self._session.get(
                url, headers=self._headers(), timeout=self._timeout
            ) as resp:
                if resp.status == 401:
                    raise SmartGharInvalidAuth(f"Token rejected: {url}")
                if resp.status >= 400:
                    text = await resp.text()
                    raise SmartGharApiError(
                        f"HTTP {resp.status} for {url}: {text[:200]}"
                    )
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SmartGharCannotConnect(f"Could not reach {url}: {err}") from err

    async def get_info(self) -> dict[str, Any]:
        """Hub identity, firmware, OTA status. Maps to GET /api/v1/info."""
        data = await self._get("/api/v1/info")
        if "hub_id" not in data:
            raise SmartGharApiError(
                "Response missing 'hub_id' — is this really a SmartGhar Hub?"
            )
        return data

    async def get_devices(self) -> list[dict[str, Any]]:
        """List of attached devices (tanks, eventually power meters etc.)."""
        data = await self._get("/api/v1/devices")
        return data.get("devices", [])
