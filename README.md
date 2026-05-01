# SmartGhar — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Local-first Home Assistant integration for the [SmartGhar](https://smartghar.org) IoT product family — **TankSync** (water-tank monitoring), **PowerSync** (energy meter, coming soon), and other accessories that pair with the SmartGhar Hub.

> **Status: v0.2.0 — bidirectional control via polling.** Functional end-to-end against hub firmware **rx-v2.7.0** (Phase 1.1 + 1.2). WebSocket-based real-time push is on track for v0.3.0 + firmware Phase 1.3. See [CHANGELOG.md](CHANGELOG.md).

## Why this exists

Most consumer IoT integrations require a cloud account, an OAuth dance, and outbound internet access from your Home Assistant instance. SmartGhar is built differently:

- **Local-first**: HA talks to your hub directly over your home network. No cloud account required.
- **Zero outbound**: HA never reaches `smartghar.org` or any of our servers.
- **Auto-discovery**: hubs broadcast on mDNS — HA finds them automatically.
- **Bidirectional**: read tank levels in real time *and* write back (rename tanks, change LED brightness, trigger OTA, etc.)
- **Multi-hub native**: each hub appears as its own HA device — works whether you have one or ten.
- **Open protocol**: the [HTTP/WebSocket spec](docs/protocol/v1.md) is published. Anyone can write a third-party client.

The TankSync cloud and PWA continue to operate alongside this integration — they're for away-from-home access. They are *not* a dependency for HA users.

## What you get today (v0.2.0)

For each SmartGhar Hub on your LAN:

**Sensors (read-only, per tank)** — `level (%)`, `TX battery voltage`, `LoRa signal`, `connection state`  
**Sensors (per hub, hidden by default)** — `uptime`, `wifi_rssi`, `firmware_version`  
**Editable entities** — `tank name` (text), `tank capacity` (number, litres), `LED brightness` (number, 0–255)  
**Buttons** — `Check for firmware updates` (per hub)

State updates every 30 seconds via polling. Edits propagate to the SmartGhar PWA via the hub's existing config-sync MQTT pipeline, so a rename in HA shows up in the PWA seconds later.

## What's coming next

| Version | Adds | Requires |
|---|---|---|
| **v0.3.0** | Real-time push, `light` entity for full LED control, `event` entities for `fill_complete` / `leak_detected` | Hub firmware Phase 1.3 (WebSocket `/api/v1/stream`) |
| **v0.4.0** | Cross-product (PowerSync, GasSync etc. auto-discover) | Ecosystem device-kind protocol expansion |
| **v0.5.0+** | Custom Lovelace tank capsule card, Hindi translations, more languages | — |

## Installation (planned)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Techposts/smartghar-homeassistant` (category: Integration)
3. Install "SmartGhar"
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → SmartGhar
6. Either accept auto-discovery prompt OR enter hub IP manually

> **Submission to HACS default repo is planned after v0.1.0 stabilises** — at which point the manual "custom repository" step goes away.

## Multi-hub setups

Have a Rooftop Hub and a Garden Hub? Both auto-discover independently. Each appears as its own device card under SmartGhar. Entities are namespaced by hub_id so there's no collision.

See [docs/multi-hub.md](docs/multi-hub.md) for Lovelace dashboard examples.

## Project structure

```
smartghar-homeassistant/
├── custom_components/smartghar/   # The HA integration (Python)
├── docs/
│   ├── protocol/v1.md             # The hub HTTP/WS API spec
│   ├── multi-hub.md               # Multi-hub UX guide
│   └── examples/                  # Automation + Lovelace examples
└── .github/workflows/             # HACS + hassfest validation
```

## Related repos

- [TankSync cloud](https://github.com/Techposts/tanksync-cloud) — private (PWA + server + active firmware)
- [LoRa-Water-Tank-Monitor](https://github.com/Techposts/LoRa-Water-Tank-Monitor) — public open-core firmware reference

## Contributing

Issues, ideas, and pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

The protocol spec is intentionally public so that anyone can build a third-party client. We commit to versioned, documented breaking changes and a deprecation window before any incompatible spec change.

## License

Code: [MIT](LICENSE).  
Protocol spec (`docs/protocol/`): Apache-2.0.
