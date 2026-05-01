# Changelog

All notable changes to the SmartGhar Home Assistant integration. Versions follow [SemVer](https://semver.org).

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
