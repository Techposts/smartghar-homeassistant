# Multi-hub setups

The SmartGhar integration is designed for households with multiple hubs — a Rooftop hub for the main building, a Garden hub for irrigation, a Farmhouse hub at a second property, etc. Each hub appears as its own Home Assistant device, so management stays clean as you scale.

## How it works

Each hub broadcasts its own `_smartghar._tcp.local.` mDNS service with a unique `hub_id`. Home Assistant's zeroconf scanner fires a discovery event per hub independently. You'll see something like:

```
🔔 New devices discovered:
   • SmartGhar Hub 'Rooftop'    (192.168.1.42)  [Configure]
   • SmartGhar Hub 'Garden'     (192.168.1.51)  [Configure]
   • SmartGhar Hub 'Farmhouse'  (192.168.0.30)  [Configure]
```

Click each one to add it. Each becomes a separate config entry under the SmartGhar integration. No coordination logic in the integration — HA handles multi-device hubs natively.

## Entity naming

Entity unique IDs always include the hub's MAC-derived `hub_id`, so multiple hubs never collide:

| Hub | Tank | Entity ID |
|---|---|---|
| Rooftop (`a1b2c3d4`) | Drinking | `sensor.smartghar_a1b2c3d4_tank_1_level` |
| Rooftop (`a1b2c3d4`) | Bath | `sensor.smartghar_a1b2c3d4_tank_2_level` |
| Garden (`5e6f7a8b`) | Irrigation | `sensor.smartghar_5e6f7a8b_tank_1_level` |
| Farmhouse (`9c0d1e2f`) | Sump | `sensor.smartghar_9c0d1e2f_tank_1_level` |

The visible entity name is derived from the hub's user-set name plus the device's user-set name, so in HA's UI you'll see "Rooftop Drinking Level", "Garden Irrigation Level", etc.

When you rename a hub or tank in the SmartGhar PWA or hub web UI, the change propagates to HA via the WebSocket `config_changed` event — no manual sync needed.

## Areas

When configuring each hub, HA suggests a default area. We recommend mapping each hub to a distinct HA area:

- `Rooftop Hub` → "Roof" or "Rooftop" area
- `Garden Hub` → "Garden" or "Outdoor" area
- `Farmhouse Hub` → "Farmhouse" area

This keeps your `Overview` dashboard naturally organized when you have automations for different sites.

## Lovelace dashboard examples

### Single-hub default (HA auto-generates)

Each hub gets a device card under `Settings → Devices & Services → SmartGhar`. The auto-generated dashboard shows all entities for that hub. No custom cards needed.

### Multi-hub vertical stack

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Rooftop Hub
    entities:
      - sensor.smartghar_a1b2c3d4_tank_1_level
      - sensor.smartghar_a1b2c3d4_tank_2_level
      - sensor.smartghar_a1b2c3d4_uptime
  - type: entities
    title: Garden Hub
    entities:
      - sensor.smartghar_5e6f7a8b_tank_1_level
      - sensor.smartghar_5e6f7a8b_uptime
  - type: entities
    title: Farmhouse Hub
    entities:
      - sensor.smartghar_9c0d1e2f_tank_1_level
      - sensor.smartghar_9c0d1e2f_uptime
```

### Aggregate "total water across all hubs" (template sensor)

Add to `configuration.yaml`:

```yaml
template:
  - sensor:
      - name: "Total household water"
        unit_of_measurement: "%"
        state: >
          {% set tanks = [
            states('sensor.smartghar_a1b2c3d4_tank_1_level') | float(0),
            states('sensor.smartghar_5e6f7a8b_tank_1_level') | float(0),
            states('sensor.smartghar_9c0d1e2f_tank_1_level') | float(0)
          ] %}
          {{ (tanks | sum / tanks | length) | round(1) }}
```

Then expose it on Lovelace:

```yaml
type: gauge
entity: sensor.total_household_water
min: 0
max: 100
severity:
  red: 0
  yellow: 25
  green: 50
```

### Multi-hub overview (custom card, v0.2.0+)

Once `smartghar-lovelace` ships:

```yaml
type: custom:smartghar-overview-card
# auto-discovers all SmartGhar hubs and renders grouped capsule visualizations
```

## Tips

- **Different time zones**: if your Farmhouse is in a different time zone than your main home, set up two HA areas with their respective time zones. Insights/automations scoped per area can then respect local time.
- **Different Wi-Fi networks**: each hub talks only to HA on its own LAN. If your Farmhouse hub is on a separate VLAN, ensure HA can reach that subnet (or run a satellite HA at the Farmhouse and federate).
- **Identifying the right hub physically**: use the `smartghar.identify_hub` service — it blinks the hub's status LED for 5 seconds. Useful when "is this Rooftop or Garden?" comes up while debugging.
