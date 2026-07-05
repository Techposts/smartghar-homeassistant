"""Button entities — momentary actions on the hub and tanks.

  - Per hub: OTA check, identify (blink status LED), reboot
  - Per tank: identify (blink the tank's LED)
"""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.exceptions import HomeAssistantError
from .const import DEVICE_KIND_TANK, DOMAIN, MODEL_TANK
from .const import CONF_CONNECTION, CONNECTION_USB  # serial coordinator entries
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info, subdevice_device_info, switch_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get(CONF_CONNECTION) == CONNECTION_USB:
        _async_setup_serial_buttons(hass, entry, async_add_entities)
        return

    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [
        SmartGharHubOtaCheck(coordinator),
        SmartGharHubIdentify(coordinator),
        SmartGharHubReboot(coordinator),
    ]
    for dev in coordinator.devices:
        if dev.get("kind") == DEVICE_KIND_TANK:
            entities.append(SmartGharTankIdentify(coordinator, dev["id"]))
    # Per Smart Switch — hand manual control back to the hub's pump rule.
    for sw in coordinator.switches:
        if "address" in sw:
            entities.append(SmartGharSwitchResumeAuto(coordinator, sw["address"]))
    async_add_entities(entities)


# ─── Hub buttons ──────────────────────────────────────────────────────────────


class _HubButtonBase(CoordinatorEntity[SmartGharCoordinator], ButtonEntity):
    """Common base for buttons attached to the hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        return hub_device_info(self.coordinator)


class SmartGharHubOtaCheck(_HubButtonBase):
    """Trigger an OTA manifest check on demand.

    Hub also auto-checks every OTA_CHECK_INTERVAL_H hours; this is for users
    who want to verify a fresh release immediately.
    """

    _attr_translation_key = "ota_check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-download-outline"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_ota_check"

    async def async_press(self) -> None:
        await self.coordinator.client.trigger_ota_check()
        await self.coordinator.async_request_refresh()


class SmartGharHubIdentify(_HubButtonBase):
    """Blink the hub's status LED for ~1.5 seconds."""

    _attr_translation_key = "identify"
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_identify"

    async def async_press(self) -> None:
        await self.coordinator.client.identify_hub()


class SmartGharHubReboot(_HubButtonBase):
    """Reboot the hub. Unreachable for ~30 seconds."""

    _attr_translation_key = "reboot"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: SmartGharCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_reboot"

    async def async_press(self) -> None:
        await self.coordinator.client.reboot_hub()


# ─── Per-tank button ──────────────────────────────────────────────────────────


class SmartGharTankIdentify(CoordinatorEntity[SmartGharCoordinator], ButtonEntity):
    """Blink the LED associated with a specific tank.

    Most useful when a hub serves multiple tanks and the user wants to know
    which physical tank corresponds to which entity in HA. Requires a hub
    LED strip with at least 8 LEDs (the per-tank slot only physically
    exists from index 2 onward).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "identify"
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: SmartGharCoordinator, tank_id: int) -> None:
        super().__init__(coordinator)
        self._tank_id = tank_id
        self._attr_unique_id = (
            f"smartghar_{coordinator.hub_id}_tank_{tank_id}_identify"
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.device_by_id(self._tank_id) or {
            "kind": "tank", "id": self._tank_id,
        }
        return subdevice_device_info(
            self.coordinator, dev,
            sub_model=MODEL_TANK,
            fallback_name=f"Tank {self._tank_id}",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.device_by_id(self._tank_id) is not None

    async def async_press(self) -> None:
        await self.coordinator.client.identify_device(self._tank_id)


class SmartGharSwitchResumeAuto(CoordinatorEntity[SmartGharCoordinator], ButtonEntity):
    """Resume the hub's pump automation for a Smart Switch.

    Turning the switch on/off (in HA or the web UI) engages manual-hold, which
    pauses the level-based pump rule. Pressing this clears the hold so the hub
    takes over again.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "switch_resume_auto"
    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator: SmartGharCoordinator, addr: int) -> None:
        super().__init__(coordinator)
        self._addr = addr
        self._attr_unique_id = f"smartghar_{coordinator.hub_id}_switch_{addr}_resume_auto"

    @property
    def _sw(self) -> dict[str, Any] | None:
        return self.coordinator.switch_by_addr(self._addr)

    @property
    def device_info(self) -> DeviceInfo:
        sw = self._sw or {}
        return switch_device_info(self.coordinator, self._addr, sw.get("name"))

    @property
    def available(self) -> bool:
        return super().available and self._sw is not None

    async def async_press(self) -> None:
        await self.coordinator.client.resume_switch_auto(self._addr)
        await self.coordinator.async_request_refresh()


# ─── USB-CDC coordinator ("HA stick") buttons ────────────────────────────────

class SmartGharSerialPairButton(ButtonEntity):
    """Open the coordinator's pairing window (LoRa + ESP-NOW) from HA.

    Lives on the coordinator hub device — press, then put the sensor in
    pairing mode within 60 seconds; its entities appear automatically once
    adopted. This replaces the stick's phone→AP→portal pairing dance.
    """

    _attr_has_entity_name = True
    _attr_name = "Start pairing"
    _attr_icon = "mdi:link-plus"

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator
        hub_id = coordinator.hub.get("device_id", "unknown")
        self._attr_unique_id = f"{hub_id}-pair"

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo
        hub_id = self._coordinator.hub.get("device_id", "unknown")
        return DeviceInfo(identifiers={(DOMAIN, f"{hub_id}-coordinator")})

    @property
    def available(self) -> bool:
        return self._coordinator.link is not None

    async def async_press(self) -> None:
        link = self._coordinator.link
        if link is None:
            raise HomeAssistantError("Coordinator is not connected")
        await link.open_pairing(60)


def _async_setup_serial_buttons(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SmartGharSerialPairButton(coordinator)])
