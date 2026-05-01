# SmartGhar

Local-first Home Assistant integration for the SmartGhar IoT product family — TankSync (water tanks), PowerSync (energy, coming soon), and accessories that pair with the SmartGhar Hub.

## Highlights

- **Local-only.** No cloud account required. No outbound internet from HA.
- **Auto-discovery** via mDNS — hubs appear automatically.
- **Bidirectional** — read state and write back (rename tanks, edit thresholds, control LEDs).
- **Multi-hub** — each hub is its own HA device.
- **Open protocol** — the HTTP/WS spec is public.

## Status

Pre-alpha. The integration scaffold is in place; functional v0.1.0 ships alongside hub firmware **rx-v2.8.0** which exposes the local API.

See [the README on GitHub](https://github.com/Techposts/smartghar-homeassistant) for details and roadmap.
