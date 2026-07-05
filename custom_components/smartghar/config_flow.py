"""Config flow for SmartGhar.

Two paths:
  1. Auto-discovery via zeroconf — hub broadcasts _smartghar._tcp on LAN.
  2. Manual entry — user types hub IP.

Both paths probe /api/v1/info to validate the hub speaks the protocol and
to capture hub_id for unique-id stability across reboots.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    SmartGharApiError,
    SmartGharCannotConnect,
    SmartGharHubClient,
    SmartGharInvalidAuth,
)
from .const import (
    CONF_CONNECTION,
    CONF_HUB_ID,
    CONF_LOCAL_TOKEN,
    CONF_SERIAL_PORT,
    CONNECTION_USB,
    DOMAIN,
)
from .serial_link import (
    SerialCoordinatorLink,
    SerialLinkError,
    SerialProtocolMismatch,
)

_LOGGER = logging.getLogger(__name__)


class SmartGharConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartGhar."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_hub_id: str | None = None
        self._discovered_name: str | None = None

    async def _probe_hub(
        self, host: str, token: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Probe /api/v1/info; returns (info, error_code)."""
        session = async_get_clientsession(self.hass)
        client = SmartGharHubClient(host, session=session, token=token)
        try:
            info = await client.get_info()
        except SmartGharCannotConnect:
            return None, "cannot_connect"
        except SmartGharInvalidAuth:
            return None, "invalid_token"
        except SmartGharApiError as err:
            _LOGGER.warning("SmartGhar probe failed: %s", err)
            return None, "cannot_connect"
        return info, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point: choose how the hub is connected."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["lan", "usb"],
        )

    async def async_step_lan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manual hub entry (WiFi/LAN hub — the original path)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            info, err = await self._probe_hub(
                user_input[CONF_HOST], user_input.get(CONF_LOCAL_TOKEN)
            )
            if err:
                errors["base"] = err
            elif info:
                await self.async_set_unique_id(info["hub_id"])
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(
                    title=info["hub_name"],
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_HUB_ID: info["hub_id"],
                        **(
                            {CONF_LOCAL_TOKEN: user_input[CONF_LOCAL_TOKEN]}
                            if user_input.get(CONF_LOCAL_TOKEN)
                            else {}
                        ),
                    },
                )

        return self.async_show_form(
            step_id="lan",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_LOCAL_TOKEN): str,
                }
            ),
            errors=errors,
        )

    async def async_step_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """USB-CDC coordinator ("HA stick") — hub plugged into the HA machine.

        The port is a device path (prefer /dev/serial/by-id/…, stable across
        re-enumeration) or any pyserial URL — socket://host:port works for
        ser2net / remote-bridge setups. Validated by a real proto-v1 handshake.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_SERIAL_PORT].strip()
            link = None
            try:
                link = await SerialCoordinatorLink.open(port)
                info = await link.start()
            except SerialProtocolMismatch:
                errors["base"] = "proto_mismatch"
            except (SerialLinkError, OSError, ValueError) as err:
                _LOGGER.warning("Serial hub probe on %s failed: %s", port, err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info.device_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_SERIAL_PORT: port})
                return self.async_create_entry(
                    title=f"TankSync Coordinator {info.device_id[-4:]}",
                    data={
                        CONF_CONNECTION: CONNECTION_USB,
                        CONF_SERIAL_PORT: port,
                        CONF_HUB_ID: info.device_id,
                    },
                )
            finally:
                if link:
                    await link.close()

        return self.async_show_form(
            step_id="usb",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_PORT): str}),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf-discovered hub."""
        # Extract hub_id from TXT records — populated by hub firmware mDNS.
        props = discovery_info.properties or {}
        hub_id = props.get("hub_id")
        if not hub_id:
            return self.async_abort(reason="missing_hub_id")

        # Prefer the resolved IP over the mDNS hostname. Reasons:
        #  - aiohttp's hostname resolution uses the OS resolver, which is
        #    unreliable for `.local` names in HAOS-on-Proxmox, Docker bridge
        #    networking, and various corporate VLAN setups. Storing the IP
        #    avoids a per-request mDNS lookup that frequently times out.
        #  - DHCP resilience is preserved a different way: zeroconf re-discovery
        #    fires automatically when the hub re-broadcasts (boot, WiFi
        #    reconnect, or periodic mDNS announce). The handler below
        #    auto-updates the stored host via `_abort_if_unique_id_configured(
        #    updates={CONF_HOST: ...})` — so a rotated IP is handled
        #    transparently within ~1 hour without any user action.
        ip_str = discovery_info.host or (
            str(discovery_info.ip_address) if discovery_info.ip_address else ""
        )
        hostname = (discovery_info.hostname or "").rstrip(".")
        self._discovered_host = ip_str or hostname

        self._discovered_hub_id = hub_id
        # Hub friendly name not in TXT yet; fall back to mDNS instance.
        self._discovered_name = (
            discovery_info.name.split(".")[0].replace("\\032", " ")
            if discovery_info.name
            else None
        )

        await self.async_set_unique_id(hub_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._discovered_host})

        # Show a confirmation step so the user clicks "Submit" once.
        self.context["title_placeholders"] = {
            "name": self._discovered_name or "SmartGhar Hub",
        }
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a zeroconf-discovered hub."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Probe to confirm the hub really speaks the protocol.
            info, err = await self._probe_hub(self._discovered_host or "", None)
            if err:
                errors["base"] = err
            elif info:
                return self.async_create_entry(
                    title=info["hub_name"],
                    data={
                        CONF_HOST: self._discovered_host,
                        CONF_HUB_ID: info["hub_id"],
                    },
                )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._discovered_name or "SmartGhar Hub",
                "host": self._discovered_host or "",
            },
            errors=errors,
        )
