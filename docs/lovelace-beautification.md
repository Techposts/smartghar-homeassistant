# Lovelace beautification — make your tanks look great

The SmartGhar integration ships read-only level entities that work with any HA card. This guide shows the **visually polished** options — the ones that turn a sensor reading into something worth screenshotting.

> A native `smartghar-lovelace` custom card is planned for v0.5.0+. Until then, the community cards below are stunning and battle-tested.

## TL;DR — copy/paste recipes

| Look | Card | Effort | Install required |
|---|---|---|---|
| **🌊 Animated wavy water fill** | `lovelace-fluid-level-background-card` | 5 min | One HACS install |
| **🎨 Clean status badges** | `mushroom-cards` | 2 min | One HACS install |
| **📈 Level over time** | Built-in `history-graph` | 30 sec | None |
| **🎚️ Simple gauge** | Built-in `gauge` | 30 sec | None |
| **🗂️ Multi-tank auto-discovery** | `auto-entities` | 5 min | One HACS install |

---

## 🌊 The wavy water tank look (most asked-for)

Animated wavy water filling a tank shape. This is the visual this integration was made for.

### Step 1: Install the community card

```
HACS → Frontend → Search: "fluid-level-background-card"
→ Download → Restart HA
```

Repo: [github.com/swingerman/lovelace-fluid-level-background-card](https://github.com/swingerman/lovelace-fluid-level-background-card)

### Step 2: Drop the SmartGhar tank silhouette into your `www/` folder

```
Copy: smartghar-homeassistant/assets/tank-silhouette.svg
To:   /config/www/smartghar/tank-silhouette.svg
```

(In HAOS: use the File Editor or Studio Code Server addon. The `/config/www/` folder is publicly accessible inside your HA install at `/local/`.)

### Step 3: Add the card to your dashboard

```yaml
type: custom:fluid-level-background-card
entity: sensor.smartghar_004b1299f6dc_tank_29773_level   # ← your tank
sensor_value_lower_threshold: 0
sensor_value_upper_threshold: 100
fill_color: rgba(56, 189, 248, 0.55)                     # rain-blue, semi-transparent
background_color: rgba(255, 255, 255, 0.04)
card_image: /local/smartghar/tank-silhouette.svg
card:
  type: tile
  entity: sensor.smartghar_004b1299f6dc_tank_29773_level
  name: Rooftop Tank
  icon: mdi:water-percent
  vertical: true
  features:
    - type: numeric-input
      style: slider
```

You'll see the wavy water animate in the background, filling to the percentage of the sensor. The tile content (name + icon + value) sits on top.

### Customizing the fill color

| Tank type | Recommended color |
|---|---|
| Drinking water | `rgba(56, 189, 248, 0.55)` (sky blue) |
| Bath / utility | `rgba(99, 102, 241, 0.55)` (deeper indigo) |
| Garden / irrigation | `rgba(34, 197, 94, 0.45)` (greenish) |
| Sump | `rgba(245, 158, 11, 0.55)` (amber) |

### Why the SmartGhar tank silhouette helps

The `card_image` field expects a transparent background image with the tank shape. Without one, the fluid fills a plain rectangle. The included `tank-silhouette.svg` is a clean overhead-tank outline (Indian rooftop style) that frames the wave nicely.

---

## 🎨 Mushroom-cards: clean status badges

If you want a calmer, less animated look, mushroom cards are the gold standard.

### Install
```
HACS → Frontend → "Mushroom" → Download → Restart HA
```

### Multi-tank dashboard
```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-template-card
    primary: "{{ states('sensor.smartghar_004b1299f6dc_tank_29773_level') }}%"
    secondary: "Rooftop Tank · {{ states('sensor.smartghar_004b1299f6dc_tank_29773_water_volume') }} L"
    icon: mdi:water-percent
    icon_color: |
      {% set p = states('sensor.smartghar_004b1299f6dc_tank_29773_level') | float(0) %}
      {% if p < 20 %}red{% elif p < 50 %}orange{% else %}cyan{% endif %}
    fill_container: true
    tap_action:
      action: more-info
      entity: sensor.smartghar_004b1299f6dc_tank_29773_level

  - type: custom:mushroom-template-card
    primary: "Hub: {{ state_attr('update.smartghar_004b1299f6dc_firmware','installed_version') }}"
    secondary: >-
      {% if is_state('update.smartghar_004b1299f6dc_firmware','on') %}
        Update available
      {% else %}
        Up-to-date
      {% endif %}
    icon: mdi:chip
    icon_color: |
      {% if is_state('update.smartghar_004b1299f6dc_firmware','on') %}
        amber
      {% else %}
        green
      {% endif %}
```

The `icon_color` template means the icon turns **red → orange → cyan** as the tank fills, so users get a glance-status without reading the number.

---

## 📈 Built-in history-graph (zero install)

Just paste — no HACS needed:

```yaml
type: history-graph
title: Tank levels — last 24 hours
hours_to_show: 24
entities:
  - entity: sensor.smartghar_004b1299f6dc_tank_29773_level
    name: Rooftop
```

For multiple tanks across multiple hubs, just add more `entities` lines.

---

## 🎚️ Built-in gauge

Simple radial gauge — no HACS needed:

```yaml
type: gauge
entity: sensor.smartghar_004b1299f6dc_tank_29773_level
name: Rooftop
unit: '%'
min: 0
max: 100
severity:
  red: 0
  yellow: 25
  green: 50
needle: true
```

The `severity` block colors the dial: red below 25%, yellow 25-50%, green above. Effective on dashboards meant for at-a-glance reads from across the room.

---

## 🗂️ Auto-entities — works for N hubs without re-editing

If you have multiple hubs, hand-coding entity IDs in YAML gets old fast. `auto-entities` filters by integration:

```
HACS → Frontend → "auto-entities" → Download → Restart HA
```

```yaml
type: custom:auto-entities
card:
  type: entities
  title: All SmartGhar tanks
filter:
  include:
    - integration: smartghar
      attributes:
        device_class: water
sort:
  method: state
  numeric: true
  reverse: true
```

That auto-discovers every level sensor from every SmartGhar hub on your install and sorts them with the fullest tank first. Add a hub later? It auto-appears.

---

## Combined dashboard example

A nice everyday view combining several patterns:

```yaml
title: Water
icon: mdi:water
panel: false
cards:
  - type: vertical-stack
    cards:
      # The hero — animated wavy fill
      - type: custom:fluid-level-background-card
        entity: sensor.smartghar_004b1299f6dc_tank_29773_level
        sensor_value_lower_threshold: 0
        sensor_value_upper_threshold: 100
        fill_color: rgba(56, 189, 248, 0.55)
        card_image: /local/smartghar/tank-silhouette.svg
        card:
          type: tile
          entity: sensor.smartghar_004b1299f6dc_tank_29773_level
          name: Rooftop
          vertical: true

      # 24h trend
      - type: history-graph
        hours_to_show: 24
        entities:
          - sensor.smartghar_004b1299f6dc_tank_29773_level

      # Hub status row
      - type: custom:mushroom-template-card
        primary: 'TankSync Hub'
        secondary: |-
          Wi-Fi: {{ states('sensor.smartghar_004b1299f6dc_wifi_signal') }} dBm ·
          Uptime: {{ (states('sensor.smartghar_004b1299f6dc_uptime') | int / 3600) | round(1) }}h
        icon: mdi:chip
        icon_color: green
```

---

## Where to go from here

- **More cards**: explore HACS Frontend section — search "tank", "water", "fluid"
- **Custom theme**: pair these cards with a calm theme like Catppuccin or Caule for editorial-style dashboards
- **Wallpanel mode**: HA's built-in screensaver mode turns a wall-mounted tablet into a always-on water dashboard

If you build a dashboard you're proud of, share a screenshot in the [GitHub Discussions](https://github.com/Techposts/smartghar-homeassistant/discussions) — we'll feature good ones in the README.
