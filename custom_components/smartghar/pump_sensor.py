"""Pump history sensors, one set per Smart Switch (the pump relay).

All of them read the coordinator's single shared /api/history/pump fetch;
none fetches anything itself. Created only when the hub has that endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTime, UnitOfVolume
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SmartGharCoordinator
from .device_info import switch_device_info
from .pump_history import clock_known, dry_stops_7d, run_attributes, run_end, today_totals

PUMP_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="pump_energy_today",
        translation_key="pump_energy_today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Back to 0 at the hub's local midnight: HA reads that as a meter reset.
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="pump_runtime_today",
        translation_key="pump_runtime_today",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pump_starts_today",
        translation_key="pump_starts_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="pump_water_today",
        translation_key="pump_water_today",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pump_dry_stops_7d",
        translation_key="pump_dry_stops_7d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-off",
    ),
    SensorEntityDescription(
        key="pump_last_run",
        translation_key="pump_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


def pump_sensors_for(coordinator: SmartGharCoordinator) -> list[SmartGharPumpSensor]:
    """History sensors for every paired switch; none on a hub without the
    history endpoint. Water only where the bound tank has a known capacity."""
    if not coordinator.pump_history_available:
        return []
    out: list[SmartGharPumpSensor] = []
    for sw in coordinator.switches:
        addr = sw.get("address")
        if addr is None:
            continue
        for desc in PUMP_SENSORS:
            if desc.key == "pump_water_today" and coordinator.tank_capacity_l(addr) is None:
                continue
            out.append(SmartGharPumpSensor(coordinator, desc, addr))
    return out


class SmartGharPumpSensor(CoordinatorEntity[SmartGharCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SmartGharCoordinator, description: SensorEntityDescription, addr: int
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._addr = addr
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_switch_{addr}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        sw = self.coordinator.switch_by_addr(self._addr) or {}
        return switch_device_info(self.coordinator, self._addr, sw.get("name"))

    @property
    def available(self) -> bool:
        hist = self.coordinator.pump_history
        if not (super().available and self.coordinator.pump_history_available):
            return False
        if self.entity_description.key == "pump_last_run":
            return True  # unknown until a run has finished
        return clock_known(hist)

    @property
    def native_value(self) -> float | int | datetime | None:
        hist = self.coordinator.pump_history
        key = self.entity_description.key
        if key == "pump_last_run":
            run = self.coordinator.last_pump_run(self._addr)
            return run_end(run) if run else None
        if not clock_known(hist):
            return None
        if key == "pump_dry_stops_7d":
            return dry_stops_7d(hist, self._addr)
        if key == "pump_water_today":
            return self.coordinator.pump_water_today_l(self._addr)
        totals = today_totals(hist, self._addr)
        if key == "pump_energy_today":
            return round(totals["wh"] / 1000, 3)
        if key == "pump_runtime_today":
            return round(totals["run_s"] / 60, 1)
        return totals["starts"]  # pump_starts_today

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "pump_last_run":
            return None
        run = self.coordinator.last_pump_run(self._addr)
        if not run:
            return None
        return run_attributes(run, self.coordinator.tank_capacity_l(self._addr))
