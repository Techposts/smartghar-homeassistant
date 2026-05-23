<p align="center"><img src="https://raw.githubusercontent.com/Techposts/smartghar-homeassistant/main/assets/icon.png" alt="SmartGhar" width="96" height="96"/></p>

# SmartGhar

Local-first Home Assistant integration for the SmartGhar IoT product family — TankSync (water tanks), PowerSync (energy, coming soon), and accessories that pair with the SmartGhar Hub.

## Highlights

- **Local-only.** No cloud account required. No outbound internet from HA.
- **Auto-discovery** via mDNS — hubs appear automatically.
- **Bidirectional** — read state and write back (rename tanks, edit thresholds, control LEDs).
- **Multi-hub** — each hub is its own HA device.
- **Open protocol** — the HTTP/WS spec is public.

## Status

**v0.8.0** — stable feature surface. Real-time WebSocket push, bidirectional configuration (rename, capacity, LED, OTA, identify, reboot), Energy dashboard cumulative consumption, per-tank sensor-health binary sensors (`sensor_not_responding`, `sensor_stuck`), hub buzzer master switch + volume select + `test_buzzer` service. AmbiSense radar-presence support included.

Requires hub firmware **rx-v2.7.0+** for the core integration; buzzer entities require **rx-v2.8.4+**.

See [the README](https://github.com/Techposts/smartghar-homeassistant) and the **[Wiki](https://github.com/Techposts/smartghar-homeassistant/wiki)** for installation, entity reference, and troubleshooting.
