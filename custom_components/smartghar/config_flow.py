"""Config flow for SmartGhar.

Two paths:
  1. Auto-discovery via zeroconf — hub broadcasts _smartghar._tcp on LAN.
  2. Manual entry — user types hub IP.

This is a v0.0.1 scaffold. Full validation against the local hub API is
implemented in v0.1.0 alongside hub firmware rx-v2.8.0.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_HUB_ID, CONF_LOCAL_TOKEN, DOMAIN


class SmartGharConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartGhar."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._discovered_host: str | None = None
        self._discovered_hub_id: str | None = None
        self._discovered_name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual hub entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # TODO (v0.1.0): probe hub at user_input[CONF_HOST]/api/v1/info
            # and capture hub_id, then await self.async_set_unique_id(hub_id)
            return self.async_create_entry(
                title=f"SmartGhar Hub ({user_input[CONF_HOST]})",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_LOCAL_TOKEN): str,
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info) -> FlowResult:
        """Handle zeroconf-discovered hub."""
        # TODO (v0.1.0): extract hub_id from TXT records, set unique_id,
        # show confirmation form with hub friendly-name pre-filled.
        self._discovered_host = discovery_info.host if hasattr(discovery_info, "host") else None
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a zeroconf-discovered hub."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name or "SmartGhar Hub",
                data={
                    CONF_HOST: self._discovered_host,
                    CONF_HUB_ID: self._discovered_hub_id,
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._discovered_name or "SmartGhar Hub",
                "host": self._discovered_host or "",
            },
        )
