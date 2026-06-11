# Changelog

All notable changes to the SmartGhar Home Assistant integration. Versions follow [SemVer](https://semver.org).

## v0.8.1 — Power source, accurate consumption, reading freshness

- **Power Source sensor** (solar / mains) per tank. A mains/USB transmitter has no battery, so the hub reports it as such; HA now surfaces the source and no longer treats an absent battery as a flat one.
- **Consumption fix** — a hub restart used to count as a full-tank drain, adding phantom litres to the water-consumption total on every reboot. Consumption now only accumulates from genuine live readings, so the total stays honest across power cuts and restarts.
- **New `smartghar.reset_consumption` service** — zero a tank's consumption total (e.g. to clear a previously-inflated number) without removing the entity. Re-seeds the baseline from the current level so the reset itself isn't counted.
- **Reading freshness** — the tank level sensor now exposes `last_reading`, `reading_age_s`, and a `stale` attribute, so dashboards and automations can tell a live reading from a last-known one. The value isn't blanked when stale — you still see the last level, plus when it was read.

## v0.8.0 — Buzzer alerts + sensor health binary sensors

Closes the three-surface-parity gap for the buzzer feature shipped on RX firmware in rx-v2.8.0 (May 2026). HA users can now toggle the hub's audible alerts, change the volume profile, and preview alert patterns directly from Home Assistant — same controls the PWA and the hub's own local web UI already had.

Also adds two long-overdue per-tank binary sensors for sensor health: `sensor_error` (the ultrasonic sensor failed to echo) and `sensor_stuck` (the ultrasonic sensor returns a constant reading regardless of actual water level — typical of a defective JSN-SR04M unit). These give automations a clean signal to surface unhealthy sensors instead of trusting a stuck reading.

Requires hub firmware **rx-v2.8.4+** on the buzzer side. Older firmware silently lacks the `/api/v1/hub/buzzer` endpoint and the buzzer entities will not register (no permanent-unavailable clutter — the integration simply skips them). Sensor_error works from rx-v2.8.0+; sensor_stuck from rx-v2.8.3+.

### Added — buzzer

- **`switch.smartghar_<hub>_buzzer_alerts`** (CONFIG category) — master mute. Off silences every alert except the boot tone (which always plays on power-up — that's intentional and matches the local web UI behavior).
- **`select.smartghar_<hub>_buzzer_volume`** (CONFIG category) — global profile Quiet / Standard / Loud. Applies uniformly to every alert. Per-alert profiles are not exposed by design; per-alert enables live on the hub local web UI under "Advanced" for users who want that granularity.
- **`smartghar.test_buzzer` service** — `{entity_id, event, profile?}`. Plays a single alert pattern bypassing master_enable and quiet hours. Useful for "does the buzzer work" smoke tests + confirming volume choice. Event values map to the firmware `buzzer_event_t` enum (4 = Test, 1 = Critical low, 2 = Overflow, 3 = Sensor offline, 6 = Pair success, 7 = OTA success, 8 = OTA failure).

### Added — sensor health binary sensors

- **`binary_sensor.smartghar_<hub>_tank_<n>_sensor_not_responding`** (PROBLEM device class, DIAGNOSTIC category) — true when the TX is alive but the ultrasonic sensor failed to echo on the last read. The level reading shown is the prior good value, not current.
- **`binary_sensor.smartghar_<hub>_tank_<n>_sensor_stuck`** (PROBLEM device class, DIAGNOSTIC category) — true when the sensor has reported a constant value across 20 wake cycles regardless of actual water level. Symptom of a defective ultrasonic module. Different from `sensor_not_responding`: there the read failed; here it returns plausible but meaningless data.

### Architecture

- `coordinator._async_update_data()` now also fetches `/api/v1/hub/buzzer` each 30s tick. Returns empty dict on older firmware → switch + select skip registration.
- `api.py` gains `get_buzzer / put_buzzer / test_buzzer` methods backed by the `/api/v1/hub/buzzer` REST surface added in rx-v2.8.4.
- `binary_sensor.py` reads `state.sensor_error` / `state.sensor_stuck` from each device's payload in `/api/v1/devices` — already exposed by the hub.

### Deferred to a future release

- **MAC-anchored `unique_id` + `async_migrate_entry`** — punted to v0.8.1. Renaming live unique_ids without a careful migration breaks existing HA entity history (entity_id derives from unique_id), so it deserves a release on its own with proper migration paths for users on rx-v2.7.10+ (MAC available) and pre-v2.7.10 (legacy numeric id).
- **Coordinator entity cleanup** when a device disappears from `/api/v1/devices` — also v0.8.1.
- **Optional "Unpair tank" button** — useful but not on the critical path.

### Upgrade notes

- No config migration. Existing entities preserve their `unique_id` and history.
- Buzzer entities register only when the hub responds to `/api/v1/hub/buzzer` (rx-v2.8.4+). OTA-update the hub first if you want the buzzer controls in HA.
- Sensor health binary sensors register for every tank regardless of firmware version. Pre-v2.8.0 firmware doesn't emit the field, so they read as `Off` permanently — no false positives.

## v0.8.0 plan archive (planned items moved out — see "Deferred" above)

The original v0.8.0 plan included MAC-anchored identity work. After scoping, that work moved to v0.8.1 because safe migration of existing unique_ids needs its own focused release with `async_migrate_entry`. v0.8.0 shipped the smaller, lower-risk subset: buzzer entities + sensor health.

## v0.7.3 — Platform setup hotfix

Critical fix for users installing or updating the integration. Five platform files referenced `DOMAIN` without importing it, which caused `async_setup_entry` to raise `NameError: name 'DOMAIN' is not defined` on first config-entry setup. Affected platforms: `button`, `number`, `text`, `event`, `update` — every TankSync interaction surface other than the read-only `sensor` and `binary_sensor` entities.

### Fixed
- **`NameError: name 'DOMAIN' is not defined`** during platform setup — added the missing `from .const import DOMAIN` line (or appended `DOMAIN` to the existing import) in `button.py`, `number.py`, `text.py`, `event.py`, and `update.py`. Users on v0.7.0–v0.7.2 who saw the integration fail to load any controls (only sensors visible) are unblocked.

### Upgrade notes
- No config migration required — pure import fix, no schema or data changes.
- If your install was stuck partially loaded, fully remove the integration in HA UI and re-add it (zeroconf will re-discover the hub).

## v0.7.2 — Honour `info.stream` contract (gate WS startup + use declared path)

Quiet fix for two bugs against schema 1.1's `info.stream` contract that landed in v0.7.1.

### Fixed
- **Hubs declaring a non-default `info.stream.ws_path`** were ignored — the coordinator hardcoded `/api/v1/stream` instead of reading the path the hub advertised. Future products advertising e.g. `/api/v2/events` would never be followed even though the contract names `ws_path` as the source of truth.
- **Hubs that don't declare `info.stream` at all** (TankSync schema 1.0, or future opt-out-of-push products) still triggered WebSocket connection attempts to `/api/v1/stream`, hitting 404 and retrying forever with exponential backoff capped at 60 s — polluting HA logs with one failed-WS-connect per minute.

### Behavior after this release
- Coordinator reads `info.stream.ws_path` at `start_ws()`. If present + valid → spawns the WS task on that path. If absent → logs "polling-only mode" once and stays on the 30-second polling channel (no log noise, no spam, no retry storms).

## v0.7.1 — Topology-aware device rendering + event frames

Two architectural improvements for cross-product fleet support, plus a cleanup sweep of legacy backwards-compat shims.

### Added — topology rendering
- `device_info.py` with `hub_device_info()` and `subdevice_device_info()` helpers. The subdevice helper reads `info["topology"]` and either returns the hub's `DeviceInfo` verbatim (standalone topology — collapse all sub-device entities onto the hub's HA card) or builds a child `DeviceInfo` with `via_device` (hub topology — each sub-device renders as its own HA device).
- **Net visual effect**: AmbiSense now shows as **one** HA device with all presence entities under it, instead of an "AmbiSense Hub" card plus a synthetic "Presence Sensor" child card. TankSync continues to render hub + per-tank cards as before (hub topology preserved).

### Added — event frames
- WebSocket consumer now handles `event` frame type (single-device-state delta) on top of the existing `snapshot` (full hub+devices state) and `hello` (handshake) frames. Real-time push for lock/gas/etc. state changes fires within <100 ms instead of waiting for the next snapshot.

### Refactored
- Every entity file (`binary_sensor`, `sensor`, `button`, `number`, `update`, `event`, `text`) now uses the two device_info helpers — no more hardcoded `MODEL_HUB` or per-class `DeviceInfo` construction. Removes ~80 lines of duplicated boilerplate.

## v0.7.0 — AmbiSense presence support (cross-product fleet)

The integration is no longer TankSync-only. AmbiSense (radar presence + LED follow-me) on firmware v6.2.0-alpha.2+ is auto-discovered and rendered as a fully native HA device alongside any TankSync hubs on the same network.

This is the first product addition since the integration's scaffold landed; the changes deliberately follow the dispatch-by-`kind` pattern designed for exactly this case, so future products (PowerSync, RidgeSync, etc.) ship as small additive PRs without touching the coordinator or config_flow.

### Added
- **`DEVICE_KIND_PRESENCE = "presence"`** — new device kind for AmbiSense's standalone-hub topology (single ESP32 advertising itself as a hub with one virtual sub-device of `kind: "presence"`).
- **`SmartGharPresenceOccupancy`** binary_sensor — `device_class: occupancy`. Stationary, target_count, nearest_cm, seconds_since_seen ride as `extra_state_attributes` so HA automations can compose conditions like "occupied AND stationary > 5 min" without separate entities.
- **`SmartGharPresenceSensor`** entities — `nearest_cm` (distance, cm), `target_count`, `seconds_since_seen` (diagnostic, duration), `rssi_dbm` (diagnostic, signal_strength, disabled by default). nearest_cm normalizes the firmware's -1 sentinel (vacant) to None so HA renders "Unknown" instead of "-1 cm".
- **`hub_model_for_product()`** dispatcher in `const.py` — picks the device-registry model string from `info["product"]`. AmbiSense → "AmbiSense Hub", TankSync → "TankSync Hub", unknown → "SmartGhar Hub".
- New translation keys: `occupancy`, `presence_nearest`, `presence_target_count`, `presence_seconds_since_seen`, `presence_rssi`.

### Multi-device
- Two AmbiSense units on one network → two HA devices, two sets of entities, zero collisions. `hub_id` (derived from MAC) is the integration's primary key.
- AmbiSense + TankSync co-existing → both render correctly with their respective product labels, both fully functional.

### Backward compatibility
- No breaking changes for TankSync. The `MODEL_HUB` constant is preserved as an alias to `MODEL_HUB_TANKSYNC`. Existing entity unique_ids and device identifiers are untouched.
- Older AmbiSense firmware (no `product` field in `/api/v1/info`) falls back to the TankSync label — degrades gracefully but you'll want to update firmware to v6.2.0-alpha.2+ for the full presence experience.

### Fixed
- **`hacs.json` invalid keys**: dropped `iot_class`, `documentation`, `issue_tracker`, `_iot_class_note` from `hacs.json` — those belong in `manifest.json` (where they already correctly are). Pre-existing config issue surfaced by the HACS GitHub Action upgrade; HACS validation now passes the `hacsjson` check.

### Spec reference
- [SmartGhar protocol v1.0](https://github.com/Techposts/AmbiSense/blob/v6-idf-rewrite/docs/SMARTGHAR-PROTOCOL.md) — wire contract, device-kind taxonomy, entity-builder template, "adding a new product" checklist.

## v0.6.1 — Hub address: prefer IP over `.local` hostname

Critical fix for users on HAOS-in-Proxmox, Docker bridge networking, and other setups where `.local` hostname resolution is unreliable.

### Fixed
- **Connection failures with `MDNS lookup failed` / `Timeout while contacting DNS servers`**: zeroconf-discovered hubs now have their **IP address** stored as the host, not the `.local` hostname. aiohttp's per-request OS resolver was timing out for `.local` names in environments like HAOS-on-Proxmox; storing the IP avoids the resolution path entirely.

### Why we changed the v0.3.1 approach
v0.3.1 stored the hostname for "DHCP resilience" — the idea being that if the IP rotates, the OS resolver follows the hostname. In practice, the OS resolver path for `.local` is brittle on common HA install topologies. **DHCP resilience is now achieved a different way**: zeroconf re-discovery fires automatically when the hub re-broadcasts (boot, WiFi reconnect, or periodic mDNS announce — typically within 1 hour). The existing `_abort_if_unique_id_configured(updates={CONF_HOST: ...})` line auto-updates the stored IP without any user action.

### For existing installs that hit this bug
If your hub is unreachable with the `MDNS lookup failed` error:
1. Delete the SmartGhar integration from `Settings → Devices & Services`
2. Restart Home Assistant
3. Re-add via auto-discovery, OR manually with the hub's IP address (e.g. `192.168.0.30`)

The new install will store the IP. DHCP changes update transparently going forward.

## v0.6.0 — Energy dashboard, refill_marker service, automation blueprints

Three additions, all globally relevant: cumulative water consumption sensor that slots into HA's native Energy dashboard, a `refill_marker` service for manual tanker logging, and two automation blueprints for one-click setup of the most-asked alerts.

### Added
- **`sensor.tank_<n>_water_consumed`** per tank — cumulative litres consumed, with `device_class: water` + `state_class: total_increasing`. Slots into HA's Energy dashboard (Settings → Energy → Water consumption → add SmartGhar tank). Persists across HA restarts via `RestoreSensor`.
- **`smartghar.refill_marker` service** — manually log a refill event (volume, source, cost, free-form note). Fires a `smartghar_refill_marker` HA event for automations to act on. Useful when the integration's auto-detection misses fast fills, or when you want to record metadata like vendor/cost.
- **Blueprint: low-water-alert** — one-click "notify when tank below X%" automation, with cooldown to prevent spam.
- **Blueprint: refill-confirmation** — one-click notification when a tank's `fill_complete` event fires.

### Why this matters globally
- **Energy dashboard** — for off-grid (well + storage tank), RV/boat, agricultural, drought-sensitive areas, and sustainability-conscious users. Most water-tank integrations can't do this because they lack continuous metering data; the SmartGhar protocol's `level_pct` + `capacity_l` lets us derive consumption.
- **Blueprints** — newcomers who don't yet write YAML automations get the most-common alerts in one click.

### Algorithm note (consumption sensor)
On each coordinator tick, compares current level to last seen. If level dropped beyond a 0.5% noise floor, accumulates the drained volume into the running total. Fills (level rising) reset the baseline without incrementing. Tiny sub-floor drains accumulate across ticks until they cross the floor, so real consumption isn't lost.

## v0.5.0 — Identify buttons, reboot, diagnostic tuning, copy fixes

Adds the entities power-users actually want: identify buttons (find a hub or tank physically), a reboot button, and diagnostic-category power-tuning numbers. Plus shorter, friendlier zeroconf form copy.

### Added
- **`button` per hub**: Identify (blinks the hub's status LED for ~1.5s) — `device_class: identify`
- **`button` per hub**: Reboot — `device_class: restart`, diagnostic category. Hub unreachable for ~30s.
- **`button` per tank**: Identify (blinks the tank's specific LED) — useful for kits with multiple tanks. Requires hub strip ≥8 LEDs.
- **`number` per tank** (diagnostic): TX sleep interval (60–3600 s) — battery vs. freshness tradeoff
- **`number` per tank** (diagnostic): TX samples per wake (1–10) — readings averaged per cycle
- **`number` per tank** (diagnostic): LoRa TX power (1–22 dBm) — range vs. battery
- API client methods: `identify_hub()`, `identify_device(id)`, `reboot_hub()`

### Changed
- **Zeroconf confirmation form**: dropped MAC/DHCP technical narrative. Just shows "Add this hub to Home Assistant?" — clean, friendly UX.
- **Manual entry form**: tightened copy. Shorter, less wordy.

### Requires
- Hub firmware **rx-v2.7.0 Phase 1.4** (adds `POST /api/v1/hub/identify`, `POST /api/v1/devices/<id>/identify`, `POST /api/v1/hub/reboot`).
- Older firmware: identify/reboot buttons silently no-op or fail; the rest of the integration is unaffected.

### Design decision
- **No native `smartghar-lovelace` custom card planned.** The community [`lovelace-fluid-level-background-card`](https://github.com/swingerman/lovelace-fluid-level-background-card) paired with our `tank-silhouette.svg` covers the visual need today. Building our own custom card is a v1.0+ revisit, gated on (1) PowerSync shipping so we can design a multi-product visual language, or (2) credible user demand for a unified branded card. See `docs/lovelace-beautification.md`.

## v0.4.0 — `update` entity, water volume, Lovelace beautification

Beautification + UX polish release. Adds HA-native firmware update UX, the missing computed sensors that fluid-level cards need, and a comprehensive guide to making tanks *look* great in dashboards.

### Added
- **`update` platform per hub** — firmware updates now show in HA's sidebar Updates section with native Install button, version tracking, and release notes (replaces / complements the `binary_sensor` for OTA-available)
- **`sensor.tank_<n>_water_volume`** (litres) — computed from `capacity_l × level_pct / 100`. Fills the gap visual cards expect and lets users display "X / Y litres" alongside the percentage
- **`tank-silhouette.svg`** in `assets/` — clean cylindrical overhead-tank shape users drop into `/config/www/` to use as a transparent background for fluid-level cards
- **`docs/lovelace-beautification.md`** — comprehensive recipes for the wavy-water-fill look (`lovelace-fluid-level-background-card`), Mushroom status badges, history graphs, multi-tank auto-discovery, plus a combined dashboard example
- API client: `trigger_ota_install()` for the new update entity's Install button

### Changed
- `level_pct` sensor now uses `suggested_display_precision=0` (no fractional percentages clutter)
- LoRa signal sensor gets a `mdi:signal` icon for clarity in entity lists

### Future direction
- **v0.5.0+**: native `smartghar-lovelace` custom card with brand-consistent capsule visualisation (separate repo: `smartghar-lovelace`). Until then, the community fluid-level card paired with our tank silhouette gives the same visual.

## v0.3.1 — DHCP resilience, broken-icon fix, LoRa-signal default-visible

Polish release fixing the things users hit on first install of v0.3.0.

### Fixed
- **Broken icon image** in HACS / GitHub README: switched from relative `assets/icon.svg` to absolute `raw.githubusercontent.com` PNG URLs so HACS's README rendering can resolve the image.
- **DHCP resilience**: zeroconf flow now stores the mDNS **hostname** (`tanksync-XXXX.local`) instead of the resolved IP. The hostname is MAC-derived and stable across DHCP lease renewals — IP changes are now handled transparently by the OS resolver at request time. Manual entries can also use either IP or hostname.
- **`{name}` placeholder substitution**: the zeroconf confirmation form was showing literal `{name}` instead of the discovered hub's name. Form copy rewritten to drop the brittle placeholder and use the more reliable `{host}`, plus added explicit reassurance that DHCP changes are OK.
- **Service-instance name decoding**: `\032` escape sequences (encoded spaces in mDNS instance names) are now properly decoded back to spaces.

### Changed
- **LoRa signal sensor visible by default** per tank — the most useful diagnostic for "is my TX still in range?". Was hidden in v0.3.0; flipped to visible.

### Migration
Existing v0.3.0 installs that stored an IP will continue to work (HA tries the stored host as-is). When zeroconf re-fires after the update, the hostname will replace the stored IP automatically. To force-update immediately, remove and re-add the hub.

## v0.3.0 — Real-time push, event entities, OTA-available indicator

**`iot_class` flips from `local_polling` to `local_push`** — the integration now subscribes to the hub's `/api/v1/stream` WebSocket and receives state updates every ~3 seconds. If the WS connection drops, it falls back to 30s polling and reconnects with exponential backoff. Polling stays as the safety net for static-ish fields (hub_id, fw_version, etc.) that aren't in WS snapshots.

### Added
- **WebSocket consumer** (`api.py::connect_ws`, `coordinator.py::_ws_runner`) — long-lived background task subscribed to `/api/v1/stream`
- **`binary_sensor` per hub**: `firmware update available` (device_class: update)
- **`event` entity per tank**: `fill_complete` — fires when a tank's level rises ≥5% between coordinator ticks
- **`diagnostics.py`** — Download diagnostics from Settings → Devices & Services → SmartGhar (redacts hub_id, host, token)
- Brand icon in `assets/` (SVG + PNG @256/@512) — adapted from the SmartGhar marketing favicon

### Changed
- Coordinator merges WS dynamic fields (`uptime_s`, `wifi_rssi`, `ota_available`) onto the polled `/info` envelope so static fields survive across WS frames
- `iot_class` updated everywhere (`manifest.json`, `hacs.json`)

### Requirements
- Hub firmware **rx-v2.7.0 Phase 1.3** (`CONFIG_HTTPD_WS_SUPPORT=y` + `/api/v1/stream` WebSocket)
- Older firmware silently falls back to polling-only — WS task fails to connect and just retries quietly

## v0.2.0 — Bidirectional control

Adds editable entities so HA users can control + configure the hub without leaving Home Assistant.

### Added
- **`text` entity per tank**: editable tank name (propagates to PWA via existing config-sync)
- **`number` entity per tank**: editable tank capacity (litres)
- **`number` entity per hub**: LED strip brightness slider (0–255)
- **`button` entity per hub**: trigger an on-demand OTA manifest check
- API client: `update_device`, `get_led`, `put_led`, `trigger_ota_check`
- Coordinator now also polls `/api/v1/hub/led` (non-fatal if endpoint missing)

### Requirements
- Hub firmware **rx-v2.7.0 Phase 1.2** (adds `PUT /api/v1/devices/<id>`, `GET/PUT /api/v1/hub/led`, `POST /api/v1/hub/ota/check`)

## v0.1.0 — Polling MVP

First functional release. Read-only sensors via 30-second polling.

### Added
- HTTP client (`api.py`) using HA's shared aiohttp session
- `DataUpdateCoordinator` polling `/api/v1/info` + `/api/v1/devices`
- Config flow probes hub `/info`, captures `hub_id` for unique-id stability
- Multi-hub native via HA's per-config-entry coordinators
- Device hierarchy: hub = primary device, each tank = sub-device via_device-linked
- Sensors per hub: uptime, wifi_rssi, firmware_version (off by default in registry)
- Sensors per tank: level (%), TX battery voltage, LoRa rssi, connection state
- English translations + entity strings catalog
- `iot_class`: `local_polling`

### Requirements
- Hub firmware **rx-v2.7.0 Phase 1.1** (adds mDNS `_smartghar._tcp` service + `/api/v1/info` + `/api/v1/devices`)

## v0.0.1 — Repository scaffold (2026-05-01)

- HACS-compliant directory structure
- Empty Python integration skeleton with config_flow + zeroconf hooks
- Protocol v1 spec published in `docs/protocol/v1.md`
- Multi-hub UX guide
- CI: HACS validation + hassfest workflow
- MIT-licensed code, Apache-2.0 protocol spec
