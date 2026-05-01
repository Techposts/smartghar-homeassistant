<p align="center"><img src="https://raw.githubusercontent.com/Techposts/Techposts/smartghar-homeassistant/main/assets/icon.png" alt="SmartGhar" width="120" height="120"/></p>

# The SmartGhar — Home Assistant Integration Guide

A complete walkthrough of what this integration is, what it does, how it works, and how to get the most out of it.

> **TL;DR**: Local-first Home Assistant integration for SmartGhar's IoT product family — currently TankSync (water tanks), with PowerSync (energy), GasSync, and others on the roadmap. Talks to the hub directly over your LAN; never touches our cloud. WebSocket push for real-time state, bidirectional control, multi-hub native, open protocol.

## Table of contents

- [What is SmartGhar?](#what-is-smartghar)
- [What is TankSync?](#what-is-tanksync)
- [Why this integration exists](#why-this-integration-exists)
- [How it works](#how-it-works)
- [Feature catalogue](#feature-catalogue)
- [Installation](#installation)
- [Setting up Home Assistant's Energy dashboard for water](#setting-up-the-energy-dashboard-for-water)
- [Common use cases](#common-use-cases)
- [Advanced topics](#advanced-topics)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Contributing](#contributing)

---

## What is SmartGhar?

[**SmartGhar**](https://smartghar.org) is an open-core IoT platform for the home. It started with water-tank monitoring (TankSync) and is expanding into a family of accessories — energy meters, gas, environmental, security — that all pair with the same central hub. Customers can buy ready-made kits, or DIY-builders can flash the open firmware on ESP32 hardware they already have.

The brand thesis: **smart, local, no lock-in.** The cloud and PWA exist for away-from-home convenience, but every product works fully offline against open protocols. Home Assistant integration is a first-class part of that promise.

## What is TankSync?

**TankSync** is SmartGhar's first product — a wireless ultrasonic water-level monitoring system designed for the realities of Indian water supply (intermittent municipal supply, rooftop overhead tanks, sump tanks, tanker top-ups) but useful anywhere there's a water tank.

It has two pieces:

| Piece | Description |
|---|---|
| **Transmitter (TX)** | Solar-powered ESP32-C3 + ultrasonic sensor sitting on top of the tank lid. Wakes every 3 minutes, takes a reading, sends it over LoRa to the hub. Battery lives months on solar alone. |
| **Receiver (Hub)** | ESP32-DevKit indoors with Wi-Fi + LoRa + OLED + LED strip. Receives readings from up to 10 TX devices, surfaces them to the Home Assistant integration over LAN, and syncs with the cloud PWA for away-from-home access. |

Each TX is paired to a hub via an over-the-air pairing flow during setup. The hub maintains a registry of paired TXs (the "tanks" that show up in HA).

## Why this integration exists

Most consumer water-tank IoT products require a cloud account, an OAuth dance, and outbound internet access from your Home Assistant install. They don't talk to HA directly — they reach into HA from a vendor cloud, with all the latency, brittleness, and privacy concerns that implies.

SmartGhar is built differently:

- **Local-first.** HA talks to the hub directly over your home network. Never reaches our cloud. Never makes outbound internet requests.
- **Real-time.** WebSocket push delivers state changes within ~3 seconds, not 30s polling.
- **Bidirectional.** You can read state *and* edit configuration (rename tanks, change capacity, control LEDs, trigger OTA, etc.) — and your edits propagate to the SmartGhar PWA via the existing config-sync MQTT pipeline.
- **Multi-hub native.** Have a Rooftop hub and a Garden hub? Both auto-discover. Each becomes its own device in HA.
- **Open protocol.** The HTTP/WebSocket API the integration speaks is [publicly documented](protocol/v1.md), Apache-2.0 licensed. Anyone can write a third-party client.
- **No vendor lock-in.** Even with the integration in place, the SmartGhar cloud is purely opt-in for away-from-home access.

## How it works

### Architecture

```
   ────────────── Local LAN ─────────────────────┐
   │                                             │
   │  ┌──────────────┐         ┌──────────────┐  │
   │  │ TankSync Hub │  mDNS   │ Home         │  │
   │  │   (ESP32)    │◄───────►│ Assistant    │  │
   │  │              │  HTTP   │ + smartghar  │  │
   │  │  /api/v1/*   │◄───────►│ HACS         │  │
   │  │              │  WS     │              │  │
   │  └──────┬───────┘◄───────►│              │  │
   │         │                 └──────────────┘  │
   │         │ (parallel)                        │
   └─────────┼───────────────────────────────────┘
             ▼
       ┌─────────────────┐
       │ tanksync.       │  ← cloud + PWA for away-from-home
       │ smartghar.org   │     (HA never touches this)
       └─────────────────┘
```

The HA integration sits entirely on the LAN side. It never authenticates against, reaches, or relies on the SmartGhar cloud. The cloud + PWA are a parallel access path for users who need away-from-home control — they're not in HA's data flow at all.

### The four protocols inside

1. **mDNS service discovery.** Hub broadcasts `_smartghar._tcp.local.` on port 80. HA's zeroconf scanner picks it up automatically. TXT records carry `hub_id`, firmware version, schema version. See [docs/protocol/v1.md](protocol/v1.md).

2. **HTTP REST.** Versioned `/api/v1/*` endpoints — `info`, `devices`, `devices/<id>`, `devices/<id>/history`, `hub/led`, `hub/display`, `hub/identify`, `hub/reboot`, etc. JSON in/out. Used for initial state fetch, edits, and one-shot actions.

3. **WebSocket** at `/api/v1/stream` — long-lived push channel. The hub sends a `hello` frame on connect followed by `snapshot` frames every ~3 seconds carrying the full hub + devices state. The integration consumes these as the primary source of fresh state, with HTTP polling as the safety-net fallback.

4. **MQTT command channel** (cloud-mediated, used by PWA *only*) — `cmd/<command>` on the cloud broker. Used for things like remote OTA when the user is away from home. The HA integration never speaks MQTT.

### Multi-hub, multi-tank entity model

```
Settings → Devices & Services → SmartGhar
└── Rooftop Hub (manufacturer: SmartGhar, model: TankSync Hub)
    ├── 💧 Drinking Tank   (sub-device, via_device → Rooftop Hub)
    │   ├── sensor.smartghar_<hub_id>_tank_<n>_level
    │   ├── sensor.smartghar_<hub_id>_tank_<n>_voltage
    │   ├── sensor.smartghar_<hub_id>_tank_<n>_water_consumed (Energy)
    │   ├── text.smartghar_<hub_id>_tank_<n>_name
    │   ├── number.smartghar_<hub_id>_tank_<n>_capacity
    │   ├── button.smartghar_<hub_id>_tank_<n>_identify
    │   ├── event.smartghar_<hub_id>_tank_<n>_fill_event
    │   └── … (full list below)
    ├── 💧 Bath Tank       (sub-device, same shape)
    └── ⚙ Hub controls   (LED, identify, reboot, OTA, etc.)
```

Each tank is its own *sub-device* linked to the hub via HA's `via_device`. This means in the HA UI you can navigate from the hub card to each tank and see its specific entities, OR you can assign each tank to a different HA Area (e.g., Rooftop tank → "Roof", Garden tank → "Garden"). Multi-hub homes work identically — each hub broadcasts independently and shows up as a separate device.

## Feature catalogue

### Sensors (read-only)

#### Per tank

| Entity | Unit | Purpose |
|---|---|---|
| `sensor.tank_<n>_level` | % | Current water level percentage (0–100) |
| `sensor.tank_<n>_voltage` | V | TX battery voltage |
| `sensor.tank_<n>_lora_signal` | dBm | LoRa signal strength (closer to 0 = better) |
| `sensor.tank_<n>_connection_state` | online / stale / lost / waiting | Whether the TX is reporting |
| `sensor.tank_<n>_water_volume` | L | Current water volume = capacity × level / 100 |
| `sensor.tank_<n>_water_consumed` | L | **Cumulative consumption — feeds HA Energy dashboard** |

#### Per hub (hidden by default — toggle on for power-user views)

| Entity | Unit | Purpose |
|---|---|---|
| `sensor.uptime` | s | Seconds since hub last booted |
| `sensor.wifi_signal` | dBm | Hub's Wi-Fi signal strength |
| `sensor.firmware_version` | text | Current firmware (e.g. "2.7.2") |

### Binary sensors

| Entity | What it represents |
|---|---|
| `binary_sensor.firmware_update_available` (per hub) | True when newer firmware exists on the OTA channel. Backed by HA's `update` device class. |

### Update entity

| Entity | What it does |
|---|---|
| `update.firmware` (per hub) | Surfaces firmware OTA in HA's Updates section with a native Install button. Same place HACS / ESPHome / Tasmota updates appear. |

### Editable entities

#### Per tank

| Entity | Range | Purpose |
|---|---|---|
| `text.tank_<n>_name` | up to 15 chars | Editable tank name. Propagates to PWA via config-sync. |
| `number.tank_<n>_capacity` | 50–50000 L | Tank capacity. Used to compute volumes + consumption. |
| `number.tank_<n>_tx_sleep_interval` (diagnostic) | 60–3600 s | How often the TX wakes to send a reading. Trade-off: longer = better battery. |
| `number.tank_<n>_tx_samples_per_wake` (diagnostic) | 1–10 | How many sensor reads the TX averages per wake. |
| `number.tank_<n>_lora_tx_power` (diagnostic) | 1–22 dBm | LoRa transmit power. Trade-off: higher = more range, more battery drain. |

#### Per hub

| Entity | Range | Purpose |
|---|---|---|
| `number.led_brightness` | 0–255 | Hub LED strip brightness slider. |

### Buttons

| Entity | What it does |
|---|---|
| `button.check_for_firmware_updates` (per hub) | Trigger an OTA manifest check. |
| `button.identify` (per hub) | Blink the hub's status LED ~5×. Find the hub physically. |
| `button.reboot_hub` (per hub, diagnostic) | Restart the hub cleanly. Unreachable for ~30 s. |
| `button.identify` (per tank) | Blink that tank's specific LED on the hub strip. Find which TX is which. |

### Events

| Entity | When it fires | Payload |
|---|---|---|
| `event.tank_<n>_fill_event` | Tank level rises ≥5 % between two coordinator ticks (auto-detected refill) | `from_pct`, `to_pct`, `delta_pct`, `volume_l`, `tank_name` |

### Services

| Service | Purpose |
|---|---|
| `smartghar.refill_marker` | Manually log a refill (volume, source, cost, note). Fires `smartghar_refill_marker` HA event for automations to act on. Useful when auto-detection misses a fast fill, or when you want to record metadata like vendor/cost paid. |

### Diagnostics

`Settings → Devices & Services → SmartGhar → Download diagnostics` produces a redacted JSON dump of integration state for bug reports. Hub_id, host, and tokens are redacted automatically.

---

## Installation

Detailed installation steps with troubleshooting are in [**docs/installation.md**](installation.md). The fast path:

1. **HACS → Integrations → ⋮ → Custom repositories** → add `https://github.com/Techposts/smartghar-homeassistant` (Integration) → Add → Download → Restart HA
2. After restart, you should see a discovered device notification on `Settings → Devices & Services` — click **Configure**
3. If discovery doesn't fire, add manually with the hub's IP

## Setting up the Energy dashboard for water

The integration's killer feature for global users is **Home Assistant's native Energy dashboard for water**. Once the integration is installed:

1. **Settings → Energy** in HA
2. Scroll to **Water consumption**
3. Click **Add water source**
4. Select `sensor.smartghar_<hub_id>_tank_<n>_water_consumed`
5. Optionally configure a tariff

You'll now get HA's full water-tracking UX:
- Today's / week's / month's consumption charts
- Hour-by-hour heat map
- Comparison across periods
- Tariff cost calculations

This works because the integration exposes a `device_class: water`, `state_class: total_increasing` sensor — exactly what HA's Energy dashboard wants. The cumulative consumption is computed locally from level deltas (with a 0.5% noise floor to filter sensor jitter), persists across HA restarts, and works regardless of whether you have a water meter.

For multi-tank households, add each tank as a separate water source. The Energy dashboard sums them in the totals view.

## Common use cases

### 1. Low water alert via push notification

Use the **`low-water-alert`** blueprint:

1. `Settings → Automations & scenes → Blueprints → Import Blueprint`
2. Paste: `https://github.com/Techposts/smartghar-homeassistant/blob/main/blueprints/automation/smartghar/low-water-alert.yaml`
3. Create automation, choose your tank, threshold (default 20%), notification service
4. Save

Done. The blueprint includes cooldown to prevent spam if level oscillates around the threshold.

### 2. Refill confirmation notification

Same flow with the **`refill-confirmation`** blueprint. Notification fires when the integration auto-detects a fill, with the volume added.

### 3. Pump dry-run protection (off-grid + RV + agricultural)

If you have a pump controlled by a Shelly relay (or any HA `switch`), the **`pump-dry-run-protect`** blueprint (shipping in v0.7.0) auto-disables the pump when sump tank empties.

For now, write directly:

```yaml
alias: Pump dry-run protect
trigger:
  - platform: numeric_state
    entity_id: sensor.smartghar_<hub_id>_tank_<sump_id>_level
    below: 5
action:
  - service: switch.turn_off
    target:
      entity_id: switch.shelly_relay_pump
  - service: notify.persistent_notification
    data:
      message: Pump auto-disabled — sump tank empty
```

### 4. Tracking refill costs (the `refill_marker` service)

When a tanker delivers water and you pay cash, log it in HA so it joins your records:

```yaml
service: smartghar.refill_marker
data:
  tank: sensor.smartghar_<hub_id>_tank_1_level
  volume_l: 5000
  source: tanker
  cost: 450
  note: Sharma Tankers, receipt 4521
```

This fires a `smartghar_refill_marker` HA event. Subscribe to it in an automation to log to a notebook, append to a Google Sheet, send a confirmation push, or anything else:

```yaml
trigger:
  - platform: event
    event_type: smartghar_refill_marker
action:
  - service: notify.persistent_notification
    data:
      title: Refill logged
      message: >
        {{ trigger.event.data.volume_l }} L from {{ trigger.event.data.source }}
        for ₹{{ trigger.event.data.cost }}
```

### 5. Multi-tank dashboard with the wavy water look

See [docs/lovelace-beautification.md](lovelace-beautification.md) for the full guide. The shortest path:

1. Install `lovelace-fluid-level-background-card` from HACS
2. Copy `assets/tank-silhouette.svg` into your `/config/www/smartghar/`
3. Paste the Lovelace YAML from the beautification doc

You get an animated wavy water fill bound to the level percentage, framed by the SmartGhar tank silhouette.

### 6. Multi-hub setups

See [docs/multi-hub.md](multi-hub.md) for naming conventions, area assignment, aggregate template sensors, and Lovelace examples for multi-hub households.

---

## Advanced topics

### Local API token (optional auth)

By default, the hub's local API accepts unauthenticated requests on the LAN — appropriate for trusted home networks. For shared, guest, or restricted networks, you can enable token auth:

1. Open the hub's web UI (`http://<hub-ip>/`)
2. (Token UI shipping in a future firmware release; for now, the default open-LAN mode is fine for nearly all home setups)

### DHCP-resilient hostnames

The integration stores the hub's mDNS hostname (e.g. `tanksync-f6dc.local`), not its IP, in the config entry. When your DHCP server rotates the hub's IP, the OS resolver follows the hostname automatically — no reconfiguration needed.

This was added in v0.3.1 after the rollout.

### Manual entry (when zeroconf doesn't work)

If you're running HA in a network mode that blocks multicast (Docker bridge networking, certain VLAN setups), zeroconf discovery won't fire. In that case:

1. Find your hub's IP from your router or the hub's local web UI
2. `Settings → Devices & Services → Add Integration → SmartGhar`
3. Type the IP (or `tanksync-XXXX.local` if your network supports mDNS resolution)
4. Submit

### Installing on Proxmox HA-as-VM

HA running on Proxmox typically uses HAOS-as-a-VM with bridged networking (`vmbr0`). mDNS discovery works correctly in this setup. If your HA VM is behind NAT or on a separate VLAN from the hub, see the manual entry section above.

### Diagnostics download (for bug reports)

`Settings → Devices & Services → SmartGhar → Download Diagnostics` produces a redacted JSON dump containing the integration's state, the latest hub `info` payload, and the device list. Hub IDs, hostnames, and tokens are auto-redacted. Attach this to GitHub issues when reporting bugs.

### What gets edited where

There are three surfaces where you can change tank/hub configuration. They all flow into the same firmware state and stay in sync:

| Surface | Where it lives | Best for |
|---|---|---|
| **HA integration** (this) | `text` / `number` / `button` entities | Day-to-day tweaks; automations |
| **PWA** | `tanksync.smartghar.org` | Away-from-home; setup wizards; pairing |
| **RX local web UI** | `http://<hub-ip>/` | On-hub diagnostics; advanced config |

Edits made in any one propagate to the other two via the existing config-sync MQTT pipeline.

### Things deliberately NOT exposed in HA

These live in the PWA / RX web UI by design — they're either destructive, sensitive, or workflow-heavy:

- Wi-Fi credential editing (sensitive — would be weird to edit Wi-Fi from HA)
- MQTT credentials (sensitive)
- TX pairing / unpairing flow (workflow with state machine, needs guided UX)
- Tank deletion (destructive — PWA has confirmation flow)
- Factory reset (catastrophic; no "Are you sure?" in HA buttons)

The principle: HA is for monitoring + everyday tweaks. The PWA is for setup, pairing, calibration, and destructive ops.

---

## Troubleshooting

### The integration appears but entities show "unavailable"

Most common cause: the hub rebooted between coordinator ticks. Wait 30 seconds; should self-recover.

If it persists:
1. `Settings → System → Logs` → search `smartghar`
2. Look for the specific failing API call
3. Check the hub is reachable: `curl http://<hub-ip>/api/v1/info` from any other device on the LAN

### Auto-discovery doesn't fire

Make sure HA has restarted *after* the integration was downloaded via HACS. The zeroconf filter is registered at HA startup from the integration's manifest.

If still no discovery:
- HA might be in a network mode that blocks multicast (Docker bridge, certain VLAN setups). Use manual entry instead.
- The hub's mDNS broadcast might be filtered by your switch/router. Test from any other LAN device with `dns-sd -B _smartghar._tcp` (Mac) or `avahi-browse -r _smartghar._tcp` (Linux).

### WS push doesn't seem real-time

If state updates take 30+ seconds, the WebSocket connection might be dropping back to polling.

1. `Settings → System → Logs` → search `smartghar.*WS`
2. Look for `WS connected to hub <id>` (good) vs `WS dropped` (problematic)
3. If WS keeps dropping, your network path between HA and the hub may be unreliable. Polling fallback (30 s) keeps working.

### Bidirectional edits not syncing to PWA

The hub propagates config edits to the cloud via MQTT. If your hub has temporarily lost MQTT connection (cellular outage, cloud server restart), edits stay local-only until MQTT reconnects.

Check: hub web UI → System tab → MQTT status. Should be `connected`.

### "Update available" never goes away

The integration polls the cloud OTA manifest indirectly — through the hub's `/info` endpoint, which the hub fills in from its OTA check. If the hub hasn't done a fresh OTA check yet, press the **Check for firmware updates** button or wait 24h for the auto-check.

### Filing a bug

GitHub Issues: https://github.com/Techposts/smartghar-homeassistant/issues

Please include:
- HA version (`Settings → System → Repairs → System Information`)
- Hub firmware version (`sensor.smartghar_..._firmware_version`)
- Logs filtered to `custom_components.smartghar`
- Diagnostics download (button in the integration's device card)

---

## FAQ

### Does this need internet?
**No.** The integration is purely local. The hub's only outbound internet use is for its own OTA check, which is independent of HA.

### Do I need a SmartGhar account to use this?
**No.** A SmartGhar account is required to use the PWA at `tanksync.smartghar.org` (cloud + away-from-home access). The HA integration is local-LAN only and never authenticates against our cloud.

### What happens if my hub's IP changes?
The integration stores the mDNS hostname, not the IP, so DHCP rotations are transparent. If you've configured the hub by IP manually (instead of hostname), edits propagate via re-discovery.

### Can I use this with multiple hubs?
**Yes.** Each hub broadcasts independently and gets its own HA device. Entities are namespaced by `hub_id` (MAC-derived) so collisions are impossible. See [docs/multi-hub.md](multi-hub.md).

### What firmware is required?
- **rx-v2.7.0** introduces the SmartGhar protocol v1 (mDNS service + `/api/v1/*` REST + WebSocket). This is the minimum for the integration to work fully.
- **rx-v2.7.1+** adds identify + reboot endpoints (HACS v0.5.0 features).
- **rx-v2.7.2+** adds per-tank identify in the local web UI.

The integration silently degrades on older firmware — it'll fall back to polling if WebSocket is unavailable, or hide newer features.

### Is the protocol open?
**Yes.** The full HTTP + WebSocket spec is at [`docs/protocol/v1.md`](protocol/v1.md), Apache-2.0 licensed. Anyone can write a third-party client.

### Why is this v0.x?
We're following SemVer pre-1.0 to allow occasional breaking changes during the early-feedback period. We'll cut **v1.0** after a ~6-week stability soak and a HACS default-repo submission. The protocol itself (firmware-side) is **stable v1**; only HA-side entity shapes might shift in HA-side minor versions.

### How do I report a bug?
GitHub Issues with diagnostics download attached. See [Troubleshooting → Filing a bug](#filing-a-bug).

### Where can I see a list of all entities?
[Feature catalogue](#feature-catalogue) above, or look at `Settings → Devices & Services → SmartGhar → <your hub>` in HA.

### Will this work with PowerSync (and other future products)?
**Yes** — the integration is designed cross-product. When PowerSync ships, it'll auto-discover under the same SmartGhar integration without you needing a second HACS install. The integration's `device_kind` taxonomy already accounts for `power`, `gas`, `pump_relay`, `soil`, `door`, `air`.

### Can I run this on a non-Indian network / 433 MHz region?
**Yes** — the LoRa frequency is configurable in the firmware (default 865 MHz for India/EU; switch to 915 MHz for US/AU). The HA integration is region-agnostic — it speaks to the hub via Wi-Fi LAN regardless of LoRa frequency.

### Is there a native Lovelace card?
**No, and not planned.** We recommend the community [`lovelace-fluid-level-background-card`](https://github.com/swingerman/lovelace-fluid-level-background-card) paired with our `assets/tank-silhouette.svg`. See [docs/lovelace-beautification.md](lovelace-beautification.md). This is a deliberate non-goal.

---

## Contributing

The integration's quality depends on real-user feedback and contributions from the HA community.

### Ways to help

- **File issues** for bugs, missing features, unclear docs
- **Improve the protocol spec** at [docs/protocol/v1.md](protocol/v1.md)
- **Write automation examples** — share useful blueprints / dashboard configs
- **Translate strings** — `custom_components/smartghar/translations/`
- **Submit pull requests** — see [CONTRIBUTING.md](../CONTRIBUTING.md)

### Open in good faith

The protocol spec is intentionally public so that anyone can build a third-party client. We commit to versioned, documented breaking changes and a deprecation window before any incompatible spec change.

If you build something cool with this — a custom Lovelace card, a Node-RED plugin, a Telegram bot — open a GitHub Discussion linking to it. We feature good ones in the README.

---

## License

| Layer | License |
|---|---|
| Code (`custom_components/smartghar/*`, blueprints, services) | [MIT](../LICENSE) |
| Protocol spec (`docs/protocol/`) | Apache-2.0 |

The split is deliberate. The protocol spec gets the patent-grant of Apache-2.0 so third-party implementers have clear legal cover. The reference Python client gets the simpler MIT for contribution friction.

---

## Changelog

See [CHANGELOG.md](../CHANGELOG.md) for full release history.

---

*Built with ❤️ for the HA community by [SmartGhar](https://smartghar.org).*
