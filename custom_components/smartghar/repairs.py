"""Repair flows: remove the stock Wyoming device for an Auvra, on confirmation."""
from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .voice_migration import ISSUE_PREFIX


class StockWyomingRepairFlow(RepairsFlow):
    """Explain, then (only on submit) remove the stock Wyoming entry."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm")

        for wy_id in self._data.get("wyoming_entry_ids", []):
            if self.hass.config_entries.async_get_entry(wy_id) is not None:
                await self.hass.config_entries.async_remove(wy_id)

        self._reuse_old_satellite_id()
        self.hass.config_entries.async_schedule_reload(self._data["entry_id"])
        return self.async_create_entry(data={})

    def _reuse_old_satellite_id(self) -> None:
        """Give our satellite the entity id the stock one had, so automations keep working."""
        ent_reg = er.async_get(self.hass)
        ours = next(
            (
                e
                for e in er.async_entries_for_config_entry(ent_reg, self._data["entry_id"])
                if e.domain == "assist_satellite"
            ),
            None,
        )
        if ours is None:
            return
        for wanted in self._data.get("stock_satellite_entity_ids", []):
            if wanted != ours.entity_id and ent_reg.async_get(wanted) is None:
                ent_reg.async_update_entity(ours.entity_id, new_entity_id=wanted)
                return


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the flow for a fixable SmartGhar issue."""
    if issue_id.startswith(ISSUE_PREFIX):
        return StockWyomingRepairFlow(dict(data or {}))
    raise ValueError(f"Unknown {DOMAIN} issue {issue_id}")
