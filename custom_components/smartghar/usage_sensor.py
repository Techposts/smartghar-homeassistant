"""Spoken-friendly TankSync sensors: water used today / this week, last fill, next arrival.

Drains are counted from level drops with the same noise floor and hub-restart
guard as the cumulative consumption sensor. A rise of FILL_MIN_PCT or more is a
fill. Anything meant to be read aloud is a text sensor whose state is already a
LOCAL phrase ("today at 2:30 PM"), because Home Assistant hands out UTC and a
voice assistant cannot be told the user's time zone by an integration.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import MODEL_TANK
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info
from .voice_logic import day_start, local_phrase, next_arrival, week_start

NOISE_FLOOR_PCT = 0.5
FILL_MIN_PCT = 3.0


class _TankWatcher(CoordinatorEntity[SmartGharCoordinator]):
    """Tracks one tank's level between coordinator ticks."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int, suffix: str) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_tank_{tank_id}_{suffix}"
        self._baseline_pct: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Seed from the current level so the first tick after a restart is not
        # counted as a drain or a fill.
        dev = self.coordinator.device_by_id(self._tank_id)
        level = ((dev or {}).get("state") or {}).get("level_pct")
        if level is not None:
            self._baseline_pct = float(level)

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._tank_id) or {"kind": "tank", "id": self._tank_id}
        return subdevice_device_info(
            self.coordinator, dev, sub_model=MODEL_TANK, fallback_name=f"Tank {self._tank_id}"
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    def _observe(self) -> tuple[float, float] | None:
        """(drained litres, risen percent) since the last tick, or None."""
        dev = self.coordinator.device_by_id(self._tank_id)
        if not dev:
            return None
        level = (dev.get("state") or {}).get("level_pct")
        capacity = (dev.get("config") or {}).get("capacity_l")
        conn = (dev.get("state") or {}).get("conn_state")
        if level is None or (conn is not None and conn not in ("online", "stale")):
            return None
        level = float(level)
        if self._baseline_pct is None:
            self._baseline_pct = level
            return None
        drop = self._baseline_pct - level
        if drop >= NOISE_FLOOR_PCT:
            self._baseline_pct = level
            return (float(capacity) * drop / 100.0 if capacity is not None else 0.0, 0.0)
        if level > self._baseline_pct:
            rise = level - self._baseline_pct
            self._baseline_pct = level
            return (0.0, rise)
        return None


class SmartGharTankUsagePeriod(_TankWatcher, RestoreSensor):
    """Litres drained today or this week (resets at local midnight / Monday)."""

    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water-minus"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int, period: str) -> None:
        super().__init__(coordinator, tank_id, f"used_{period}")
        self._period = period
        self._attr_translation_key = f"tank_used_{period}"
        self._total_l = 0.0
        self._start: datetime = self._period_start(dt_util.now())

    def _period_start(self, now: datetime) -> datetime:
        return day_start(now) if self._period == "today" else week_start(now)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        state = await self.async_get_last_state()
        if last is not None and state is not None:
            started = dt_util.parse_datetime(state.attributes.get("period_start", ""))
            try:
                if started is not None and dt_util.as_local(started) >= self._start:
                    self._total_l = float(last.native_value or 0)
            except (TypeError, ValueError):
                self._total_l = 0.0
        # Roll over at the boundary even when nothing drains.
        self.async_on_remove(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=5)
        )

    @callback
    def _midnight(self, _now: datetime) -> None:
        self._roll()
        self.async_write_ha_state()

    def _roll(self) -> None:
        start = self._period_start(dt_util.now())
        if start > self._start:
            self._start = start
            self._total_l = 0.0

    @callback
    def _handle_coordinator_update(self) -> None:
        self._roll()
        obs = self._observe()
        if obs:
            self._total_l += obs[0]
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total_l, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"period_start": self._start.isoformat()}


class SmartGharTankLastFill(_TankWatcher, RestoreEntity, SensorEntity):
    """When the tank was last filled, as a local phrase."""

    _attr_translation_key = "tank_last_fill"
    _attr_icon = "mdi:water-plus"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator, tank_id, "last_fill")
        self._at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            self._at = dt_util.parse_datetime(state.attributes.get("timestamp", ""))

    @callback
    def _handle_coordinator_update(self) -> None:
        obs = self._observe()
        if obs and obs[1] >= FILL_MIN_PCT:
            self._at = dt_util.utcnow()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str:
        if self._at is None:
            return "not seen yet"
        return local_phrase(dt_util.as_local(self._at), dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._at is None:
            return {}
        local = dt_util.as_local(self._at)
        return {
            "timestamp": self._at.isoformat(),
            "date": local.date().isoformat(),
            "time": local.strftime("%I:%M %p").lstrip("0"),
            "hours_ago": round((dt_util.utcnow() - self._at).total_seconds() / 3600, 1),
        }


class SmartGharNextArrival(CoordinatorEntity[SmartGharCoordinator], SensorEntity):
    """Next daily water arrival, from the times the user typed in Options."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_water_arrival"
    _attr_icon = "mdi:water-clock"

    def __init__(self, coordinator: SmartGharCoordinator, times: list) -> None:
        super().__init__(coordinator)
        self._times = times
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_next_arrival"

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The phrase depends on the clock, not on hub data.
        self.async_on_remove(
            async_track_time_change(self.hass, self._tick, minute=range(0, 60, 15), second=0)
        )

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        nxt = next_arrival(self._times, dt_util.now())
        return None if nxt is None else local_phrase(nxt, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        nxt = next_arrival(self._times, dt_util.now())
        if nxt is None:
            return {}
        return {"in_minutes": int((nxt - dt_util.now()).total_seconds() // 60)}
