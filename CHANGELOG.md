# Changelog

All notable changes to the SmartGhar Home Assistant integration. Versions follow [SemVer](https://semver.org).

## v0.2.0 — Bidirectional control (unreleased)

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
