<p align="center"><img src="https://raw.githubusercontent.com/Techposts/smartghar-homeassistant/main/assets/icon.png" alt="SmartGhar" width="128" height="128"/></p>

# SmartGhar — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.7.0-blue.svg)](CHANGELOG.md)

Local-first Home Assistant integration for the [SmartGhar](https://smartghar.org) IoT product family — **TankSync** (water-tank monitoring) and **[AmbiSense](https://github.com/Techposts/AmbiSense)** (radar presence + LED follow-me lighting), with **PowerSync** (energy), **RidgeSync** (fingerprint locks), and other products on the roadmap. One integration, every Techposts device on your network auto-discovers.

> **📚 [Read the Wiki →](https://github.com/Techposts/smartghar-homeassistant/wiki)** — full documentation lives there: installation, entities reference, Energy dashboard setup, multi-hub guides, troubleshooting, FAQ.

---

## Why this exists

Most consumer IoT integrations require a vendor cloud account, OAuth dance, and outbound internet from your Home Assistant install. SmartGhar is built differently:

- 🏠 **Local-first.** HA talks to your hub directly over your home network. No cloud account required. Never reaches our cloud.
- ⚡ **Real-time.** WebSocket push delivers state changes within ~3 seconds, not 30s polling.
- ↔️ **Bidirectional.** Read state *and* edit configuration (rename tanks, change capacity, control LEDs, trigger OTA, etc.) — edits propagate to the SmartGhar PWA via the existing config-sync pipeline.
- 🏘️ **Multi-hub native.** Each hub auto-discovers and gets its own HA device.
- 📖 **Open protocol.** The [HTTP/WebSocket spec](docs/protocol/v1.md) is Apache-2.0. Anyone can write a third-party client.
- 💧 **HA Energy dashboard.** Cumulative water-consumption sensor with `device_class: water` slots into HA's native water-tracking UI.

## Quick install

```
1. HACS → Integrations → ⋮ → Custom repositories
2. URL: https://github.com/Techposts/smartghar-homeassistant
   Category: Integration → Add → Download
3. Settings → System → Restart Home Assistant
4. Auto-discovery fires; click "Configure" on the discovered hub
   (or Settings → Devices & Services → Add Integration → SmartGhar)
```

For details + troubleshooting, see the **[Installation page in the wiki →](https://github.com/Techposts/smartghar-homeassistant/wiki/Installation)**.

## Status (v0.7.0)

### TankSync
- Real-time push via WebSocket against hub firmware **rx-v2.7.0+**
- Energy dashboard cumulative consumption sensor
- Bidirectional control: rename, capacity, LED, OTA, identify, reboot
- Per-tank entities for level, voltage, LoRa signal, connection state, water volume, water consumed
- HA-native `update` entity for firmware OTA
- 2 automation blueprints (low-water-alert, refill-confirmation) — install in one click
- `smartghar.refill_marker` service for manual fill logging

### AmbiSense (new in v0.7.0)
- Auto-discovery against AmbiSense firmware **v6.2.0-alpha.2+**
- `binary_sensor: occupancy` with stationary, target_count, nearest_cm, seconds_since_seen as attributes
- `sensor: distance` (cm), `target_count`, `seconds_since_seen` (diagnostic), `rssi_dbm` (diagnostic)
- Hub model dispatch — AmbiSense devices show as "AmbiSense Hub", TankSync as "TankSync Hub"
- Multi-device safe: 5 AmbiSense units = 5 distinct HA devices, no entity-id collisions

For the full entity catalogue, see **[Entities Reference →](https://github.com/Techposts/smartghar-homeassistant/wiki/Entities-Reference)**.

## Cross-product protocol

All Techposts IoT products implement the same wire contract documented in the [SmartGhar protocol spec](https://github.com/Techposts/AmbiSense/blob/v6-idf-rewrite/docs/SMARTGHAR-PROTOCOL.md). Adding a new product (RidgeSync, etc.) to this integration is a small additive PR — new `DEVICE_KIND_*`, new entity classes, new dispatch case. No coordinator or config_flow changes.

## Native Lovelace card — explicit non-goal

A native custom Lovelace card is **not on the roadmap**. The community [`lovelace-fluid-level-background-card`](https://github.com/swingerman/lovelace-fluid-level-background-card) paired with our [`assets/tank-silhouette.svg`](assets/tank-silhouette.svg) covers the wavy-water tank look — see [Lovelace Beautification →](https://github.com/Techposts/smartghar-homeassistant/wiki/Lovelace-Beautification) in the wiki for the setup recipe.

## Project structure

```
smartghar-homeassistant/
├── custom_components/smartghar/   # The HA integration (Python)
├── blueprints/automation/         # Pre-built HA automations
├── docs/
│   ├── GUIDE.md                   # Mirror of wiki for in-repo readers
│   ├── protocol/v1.md             # Apache-2.0 HTTP/WS API spec
│   ├── installation.md, multi-hub.md, lovelace-beautification.md, examples/
└── .github/workflows/             # HACS + hassfest validation
```

The wiki has the richer browsing UX; `docs/` mirrors most of it for in-repo readers.

## Related repos

- [TankSync cloud](https://github.com/Techposts/tanksync-cloud) — private (PWA + server + active firmware)
- [LoRa-Water-Tank-Monitor](https://github.com/Techposts/LoRa-Water-Tank-Monitor) — public open-core firmware reference

## Contributing

See **[Contributing →](https://github.com/Techposts/smartghar-homeassistant/wiki/Contributing)** in the wiki, or [CONTRIBUTING.md](CONTRIBUTING.md).

The protocol spec is intentionally public so anyone can build third-party clients. Versioned, documented breaking changes only.

## License

| Layer | License |
|---|---|
| Code (`custom_components/smartghar/*`, blueprints, services) | [MIT](LICENSE) |
| Protocol spec (`docs/protocol/`) | Apache-2.0 |
