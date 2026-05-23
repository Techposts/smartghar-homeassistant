# Changelog

All notable changes to the SmartGhar Home Assistant integration. Versions follow [SemVer](https://semver.org).

## v0.8.0 (planned) — MAC-anchored identity + migration hook + buzzer entities

Aligns the integration with the hub-side pair identity redesign that shipped in **rx-v2.7.10 / tx-v2.0.11 / rx-v2.7.11** (cloud repo, 2026-05-20+21) and brings the new buzzer-alerts feature (rx-v2.8.0, RX local web UI + PWA already ship buzzer controls) into HA. See the full hub-side rationale in [`PAIRING_IDENTITY.md`](https://github.com/Techposts/tanksync-cloud/blob/main/cloud/docs/PAIRING_IDENTITY.md) (private repo).

### Why this release matters

Two themes:
1. **TX MAC** is the new stable identity. The hub now exposes a stable **TX MAC address** in `/api/v1/devices` responses (12-char lowercase hex, empty string for entries paired with pre-v2.0.11 TX firmware). MAC is immutable across re-pairs, hub firmware upgrades, and address reassignment. This integration should anchor `unique_id` on MAC for proper history continuity.
2. **Audible alerts** — the hub now has a physical buzzer that beeps on boot, critical-low water, overflow at fill-completion, sensor offline, and a handful of opt-in events. Local web UI + PWA already control it; HACS gets full parity here.

### Planned changes — identity / migration

- **`async_migrate_entry`** hook in `config_flow.py` — detects firmware version change and logs an upgrade warning if existing tanks would re-key
- **`device_info.py::_subdevice_identifier()`** — fallback chain: `dev.get("mac")` first, then `device["id"]` for legacy entries
- **Coordinator entity cleanup** — when a device disappears from `/api/v1/devices` (deleted on the hub via PWA or Web UI), unregister the entities from HA instead of leaving them `unavailable` forever
- **Optional: "Unpair tank" button** — calls the cloud's `DELETE /api/devices/:id` (which now propagates to the hub via MQTT `remove_tx` in cloud release 2026-05-21)

### Planned changes — buzzer alerts (RX 2.8.0+)

- **`switch.tanksync_buzzer_enabled`** — master mute toggle, mirrors hub local web UI master_enable
- **Per-alert `switch` entities** — one per essential alert (critical-low, overflow, sensor-offline) + optional alerts behind diagnostic category (refill, drain, etc.)
- **`select.tanksync_buzzer_volume`** — Quiet / Standard / Loud (mirrors the global profile)
- **`tanksync.test_buzzer` service** — `{tank: entity_id, event: "critical_low" | "overflow" | ...}` to preview alert patterns
- **Quiet-hours `number` entities** — start/end hours as configurable HA numbers (optional, may defer)

### Migration story (for users)

If you upgrade hub firmware to rx-v2.7.10+ and re-pair existing tanks **with TX firmware ≥ 2.0.11**, the new pairing assigns a small-int address (e.g. 1, 2, 3…) replacing the old random 16-bit. To preserve HA entity history across the transition: don't delete the integration; we ship a migration hook in this release.

The buzzer feature requires rx-v2.8.0+ on the hub. Older firmware silently lacks the `/api/buzzer` endpoint and the entities will report `unavailable` until you OTA-update.

## v0.7.3 — Platform setup hotfix

Critical fix for users installing or updating the integration. Five platform files referenced `DOMAIN` without importing it, which caused `async_setup_entry` to raise `NameError: name 'DOMAIN' is not defined` on first config-entry setup. Affected platforms: `button`, `number`, `text`, `event`, `update` — every TankSync interaction surface other than the read-only `sensor` and `binary_sensor` entities.

### Fixed
- **`NameError: name 'DOMAIN' is not defined`** during platform setup — added the missing `from .const import DOMAIN` line (or appended `DOMAIN` to the existing import) in `button.py`, `number.py`, `text.py`, `event.py`, and `update.py`. Users on v0.7.0–v0.7.2 who saw the integration fail to load any controls (only sensors visible) are unblocked.

### Upgrade notes
- No config migration required — pure import fix, no schema or data changes.
- If your install was stuck partially loaded, fully remove the integration in HA UI and re-add it (zeroconf will re-discover the hub).

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
