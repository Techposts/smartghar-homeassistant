# SmartGhar — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Local-first Home Assistant integration for the [SmartGhar](https://smartghar.org) IoT product family — **TankSync** (water-tank monitoring), **PowerSync** (energy meter, coming soon), and other accessories that pair with the SmartGhar Hub.

> **Status: Pre-alpha (v0.0.1).** Repository scaffolded; integration logic ships with hub firmware **`rx-v2.8.0`** which exposes the local HTTP/WebSocket API this integration consumes. Watch this repo for v0.1.0 release.

## Why this exists

Most consumer IoT integrations require a cloud account, an OAuth dance, and outbound internet access from your Home Assistant instance. SmartGhar is built differently:

- **Local-first**: HA talks to your hub directly over your home network. No cloud account required.
- **Zero outbound**: HA never reaches `smartghar.org` or any of our servers.
- **Auto-discovery**: hubs broadcast on mDNS — HA finds them automatically.
- **Bidirectional**: read tank levels in real time *and* write back (rename tanks, change LED brightness, trigger OTA, etc.)
- **Multi-hub native**: each hub appears as its own HA device — works whether you have one or ten.
- **Open protocol**: the [HTTP/WebSocket spec](docs/protocol/v1.md) is published. Anyone can write a third-party client.

The TankSync cloud and PWA continue to operate alongside this integration — they're for away-from-home access. They are *not* a dependency for HA users.

## What you'll get (once v0.1.0 ships)

For each SmartGhar Hub on your LAN:

- **Tank sensors**: level (%), TX battery voltage, LoRa signal strength, last-seen timestamp
- **Fill events** (HA event entities): `fill_complete`, `low_threshold_crossed`, `leak_detected`
- **Editable entities**: tank names, low-water thresholds, capacity values
- **Hub controls**: identify (blink LED), OTA check, WiFi forget, uptime/firmware sensors
- **LED strip**: full HA `light` entity (brightness, color, mode)
- **Display config**: brightness, rotation
- **Real-time**: WebSocket-pushed state changes — no polling

Future products (PowerSync, GasSync, etc.) auto-discover under the same integration.

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
