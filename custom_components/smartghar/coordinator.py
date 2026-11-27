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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SmartGharApiError, SmartGharCannotConnect, SmartGharHubClient
from .const import DOMAIN, SCAN_INTERVAL, SWITCH_SCAN_INTERVAL

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
        self._ws_path: str | None = None
        self._stop = False
        # Bumped on every applied WS snapshot/event. A poll that started
        # before a push landed must not overwrite the pushed device state.
        self._ws_generation = 0
        self._switch_unsub: CALLBACK_TYPE | None = None
        self._switch_busy = False
        self._switch_err_logged = False

    async def _async_update_data(self) -> dict[str, Any]:
        generation = self._ws_generation
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

        # Buzzer config — requires hub firmware rx-v2.8.4+. Older firmware
        # returns 404 for /api/v1/hub/buzzer; we treat that as "no buzzer
        # available" and the switch/select entities self-mark unavailable.
        buzzer: dict[str, Any] = {}
        try:
            buzzer = await self.client.get_buzzer()
        except SmartGharApiError as err:
            _LOGGER.debug("Buzzer state unavailable on hub %s: %s", self.hub_id, err)

        # Smart Switches — separate endpoint from tanks. Absent on firmware that
        # predates the feature; treat an API error as "no switches" so older
        # hubs don't fail their whole refresh.
        switches: list[dict[str, Any]] = []
        try:
            switches = await self.client.get_switches()
        except SmartGharApiError as err:
            _LOGGER.debug("Switches unavailable on hub %s: %s", self.hub_id, err)

        # Voice settings (Auvra firmware with a mic): /api/state -> voice {...}.
        # Anything but a voice block (404 on TankSync hubs and older Auvra
        # firmware, an odd reply) just means "no voice".
        voice: dict[str, Any] = {}
        if info.get("product") == "auvra":
            try:
                state = await self.client.get_state()
            except (SmartGharApiError, SmartGharCannotConnect) as err:
                _LOGGER.debug("Voice state unavailable on hub %s: %s", self.hub_id, err)
                voice = self.voice  # a blip must not make the entities vanish
            else:
                block = state.get("voice") if isinstance(state, dict) else None
                if isinstance(block, dict):
                    voice = block

        if generation != self._ws_generation and self.data:
            # A WS push arrived while this poll was in flight (typically the
            # refresh after a command: the GET is answered before the device
            # has acted, the push right after). The push is newer; keep it.
            devices = self.data.get("devices", devices)

        return {
            "info": info,
            "devices": devices,
            "led": led,
            "buzzer": buzzer,
            "switches": switches,
            "voice": voice,
        }

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
    def buzzer(self) -> dict[str, Any]:
        """Hub buzzer config (rx-v2.8.4+). Empty dict when unavailable —
        entities should self-mark as unavailable in that case."""
        return self.data.get("buzzer", {}) if self.data else {}

    @property
    def buzzer_available(self) -> bool:
        """True when the hub responded to /api/v1/hub/buzzer (rx-v2.8.4+)."""
        return bool(self.buzzer)

    @property
    def voice(self) -> dict[str, Any]:
        """The unit's `voice` block from /api/state ({} when it has none)."""
        return self.data.get("voice", {}) if self.data else {}

    @property
    def switches(self) -> list[dict[str, Any]]:
        """Paired Smart Switches (empty on firmware without the feature)."""
        return self.data.get("switches", []) if self.data else []

    def switch_by_addr(self, addr: int) -> dict[str, Any] | None:
        for s in self.switches:
            if s.get("address") == addr:
                return s
        return None

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

    def _ws_path_from_contract(self) -> str | None:
        """Read the WS path from the hub's declared `info.stream`.

        Returns None when the hub doesn't advertise a stream block —
        either because it speaks schema 1.0 (TankSync today) or because
        the firmware author chose polling-only. Caller treats None as
        "don't start a WS task at all" — avoids the 404-reconnect-loop
        that would otherwise pollute HA logs every 60 s forever.
        """
        stream = self.info.get("stream") or {}
        path = stream.get("ws_path")
        if isinstance(path, str) and path.startswith("/"):
            return path
        return None

    def merge_switches(self, switches: list[dict[str, Any]]) -> None:
        """Replace the switch list and notify listeners. Unlike
        async_set_updated_data this does not restart the 30 s poll timer, so
        a 2 s switch tick can never starve the full poll."""
        if not self.data or self.data.get("switches") == switches:
            return
        self.data = {**self.data, "switches": switches}
        self.async_update_listeners()

    async def _switch_tick(self, _now: Any = None) -> None:
        # Only hubs that have paired switches; skip while a fetch is in flight.
        if self._switch_busy or not self.switches:
            return
        self._switch_busy = True
        try:
            self.merge_switches(await self.client.get_switches())
            self._switch_err_logged = False
        except SmartGharApiError as err:
            if not self._switch_err_logged:
                self._switch_err_logged = True
                _LOGGER.debug("Switch refresh on hub %s failed: %s", self.hub_id, err)
        finally:
            self._switch_busy = False

    def start_switch_poll(self) -> None:
        if self._switch_unsub is None:
            self._switch_unsub = async_track_time_interval(
                self.hass, self._switch_tick, SWITCH_SCAN_INTERVAL
            )

    def stop_switch_poll(self) -> None:
        if self._switch_unsub is not None:
            self._switch_unsub()
            self._switch_unsub = None

    def start_ws(self) -> None:
        """Spawn the WS background task if the hub declares a stream.

        Reads `info.stream.ws_path` from the cached /api/v1/info. If the
        hub doesn't declare one (schema 1.0 firmware, or 1.1 firmware
        that opts out of push), stays on polling only — no reconnect
        loop, no log noise. Idempotent.
        """
        self.start_switch_poll()
        if self._ws_task and not self._ws_task.done():
            return
        ws_path = self._ws_path_from_contract()
        if ws_path is None:
            _LOGGER.info(
                "Hub %s does not declare info.stream — staying on polling-only mode",
                self.hub_id,
            )
            return
        self._stop = False
        self._ws_path = ws_path
        self._ws_task = self.hass.async_create_background_task(
            self._ws_runner(), name=f"smartghar_ws_{self.hub_id}"
        )

    async def stop_ws(self) -> None:
        self._stop = True
        self.stop_switch_poll()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None
        self._ws_connected = False

    async def _ws_runner(self) -> None:
        ws_path = self._ws_path or "/api/v1/stream"
        backoff = WS_BACKOFF_INITIAL_S
        while not self._stop:
            try:
                ws = await self.client.connect_ws(ws_path)
            except Exception as err:
                _LOGGER.debug(
                    "WS connect to hub %s at %s failed: %s — retrying in %.1fs",
                    self.hub_id, ws_path, err, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, WS_BACKOFF_MAX_S)
                continue

            self._ws_connected = True
            backoff = WS_BACKOFF_INITIAL_S
            _LOGGER.info("WS connected to hub %s at %s", self.hub_id, ws_path)

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
        """Apply one WS frame to coordinator state and fan out to entities.

        Frame kinds:
          hello     — handshake, validates schema compat
          snapshot  — full hub + devices state, periodic
          event     — single device-state delta, fired on change

        `event` frames let event-driven kinds (lock open/close, gas leak)
        notify HA in <100ms without waiting for the next snapshot tick.
        """
        kind = msg.get("kind")
        if kind == "hello":
            schema = msg.get("schema_version", "1.0")
            if not schema.startswith("1."):
                _LOGGER.warning(
                    "Hub %s announced schema %s — this integration speaks 1.x",
                    self.hub_id, schema,
                )
            return

        if not self.data:
            # Polling hasn't completed yet; let it set the baseline.
            return

        if kind == "snapshot":
            self._ws_generation += 1
            self._apply_snapshot(msg)
            return

        if kind == "event":
            self._ws_generation += 1
            self._apply_event(msg)
            return

        # Unknown frame kind — log once at debug, ignore.
        _LOGGER.debug("Hub %s sent unknown frame kind=%s", self.hub_id, kind)

    def _apply_snapshot(self, msg: dict[str, Any]) -> None:
        # Merge dynamic fields from WS into the existing /info envelope.
        # Static fields (hub_id, fw_version, schema_version, topology,
        # ota.current/channel, claimed, device_kinds) survive from the
        # last poll and aren't re-sent every snapshot.
        hub_dynamic = msg.get("hub") or {}
        new_info = {**self.data.get("info", {})}
        for key in ("uptime_s", "wifi_rssi"):
            if key in hub_dynamic:
                new_info[key] = hub_dynamic[key]
        # Auvra 0.10 pushes its capabilities on every snapshot, so a feature
        # switched off (streams) drops at once instead of on the next poll.
        if isinstance(hub_dynamic.get("capabilities"), list):
            new_info["capabilities"] = list(hub_dynamic["capabilities"])
        if "ota_available" in hub_dynamic:
            ota = {**(new_info.get("ota") or {})}
            ota["available"] = (
                ota.get("current") and hub_dynamic["ota_available"]
            ) or None
            new_info["ota"] = ota

        # Keep every polled block (led, buzzer, switches): dropping them here
        # made buzzer entities flap unavailable between polls.
        new_data = {
            **self.data,
            "info": new_info,
            "devices": msg.get("devices", self.data.get("devices", [])),
        }
        self.async_set_updated_data(new_data)

    def _apply_event(self, msg: dict[str, Any]) -> None:
        """Apply a single-device state delta. Frame shape:
            {"kind":"event","device_id":<int>,"state":{...partial...}}
        Unknown device_ids are dropped — they'll appear on next snapshot.
        """
        device_id = msg.get("device_id")
        delta = msg.get("state") or {}
        if device_id is None or not delta:
            return

        devices = list(self.data.get("devices", []))
        for i, dev in enumerate(devices):
            if dev.get("id") == device_id:
                merged_state = {**(dev.get("state") or {}), **delta}
                devices[i] = {**dev, "state": merged_state}
                break
        else:
            return  # device_id not found in current registry

        new_data = {**self.data, "devices": devices}
        self.async_set_updated_data(new_data)
