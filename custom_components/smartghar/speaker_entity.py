"""Shared base for the Auvra speaker's schema-1.2 config entities.

The EQ / channel entities (select.py, number.py, switch.py) all read one
speaker device's `state` (falling back to `config`), write through the flat
PUT /api/v1/devices/<id>, live on the speaker's device card, and exist only
while the device lists their capability in /api/v1/info.
"""
from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SmartGharApiError
from .const import DEVICE_KIND_SPEAKER, MODEL_SPEAKER
from .coordinator import SmartGharCoordinator
from .device_info import subdevice_device_info
from .speaker_logic import CAP_EQ, capabilities, eq_state, has_channel


def speaker_ids_with(
    coordinator: SmartGharCoordinator, cap: str | None, config_key: str | None = None
) -> list[int]:
    """Speaker device ids when the hub lists `cap` ("channel" = has_channel;
    None = no capability needed) and, if given, the device's v1 `config`
    carries `config_key`."""
    info = coordinator.info
    if cap is not None:
        ok = has_channel(info) if cap == "channel" else cap in capabilities(info)
        if not ok:
            return []
    return [
        dev["id"]
        for dev in coordinator.devices
        if dev.get("kind") == DEVICE_KIND_SPEAKER
        and "id" in dev
        and (config_key is None or config_key in (dev.get("config") or {}))
    ]


class SmartGharSpeakerConfigEntity(CoordinatorEntity[SmartGharCoordinator]):
    """One config knob on one Auvra speaker."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _key: str = ""             # unique_id suffix; subclass sets
    _capability: str | None = CAP_EQ  # "channel" = has_channel(); None = always
    _config_key: str | None = None    # also require this key in v1 config

    def __init__(self, coordinator: SmartGharCoordinator, device_id: int) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_speaker_{device_id}_{self._key}"
        )

    @property
    def _dev(self) -> dict[str, Any] | None:
        return self.coordinator.device_by_id(self._device_id)

    def _value(self, key: str) -> Any:
        """state[key], else config[key] (the contract puts both in state)."""
        dev = self._dev or {}
        for block in (dev.get("state"), dev.get("config")):
            if isinstance(block, dict) and key in block:
                return block[key]
        return None

    def _eq_value(self, key: str) -> Any:
        dev = self._dev or {}
        for block in (dev.get("state"), dev.get("config")):
            eq = eq_state(block)
            if key in eq:
                return eq[key]
        return None

    @property
    def _state(self) -> dict[str, Any]:
        dev = self._dev or {}
        return dev.get("state") or {}

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev or {"kind": DEVICE_KIND_SPEAKER, "id": self._device_id}
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_SPEAKER,
            fallback_name=f"Speaker {self._device_id}",
        )

    @property
    def available(self) -> bool:
        if not super().available or self._dev is None:
            return False
        if self._config_key is not None and self._config_key not in (
            (self._dev or {}).get("config") or {}
        ):
            return False
        info = self.coordinator.info
        if self._capability is None:
            return True
        if self._capability == "channel":
            return has_channel(info)
        return self._capability in capabilities(info)

    async def _put(self, fields: dict[str, Any]) -> None:
        try:
            resp = await self.coordinator.client.update_device(self._device_id, fields)
        except SmartGharApiError as err:
            raise HomeAssistantError(f"Auvra did not accept {fields}: {err}") from err
        if isinstance(resp, dict) and resp.get("rejected"):
            raise HomeAssistantError(
                f"Auvra rejected {', '.join(map(str, resp['rejected']))}: {fields}"
            )
        await self.coordinator.async_request_refresh()
