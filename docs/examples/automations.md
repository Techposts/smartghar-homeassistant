# Automation examples

Copy-pasteable Home Assistant automations for TankSync. Adjust the entity IDs to match your install — yours will be `sensor.smartghar_<your_hub_id>_<tank_n>_level` etc.

## Low water alert via WhatsApp / Telegram

Triggers when the drinking tank drops below 20%.

```yaml
alias: Drinking water below 20%
description: Send WhatsApp message when drinking tank drops below 20%
trigger:
  - platform: numeric_state
    entity_id: sensor.smartghar_a1b2c3d4e5f6_tank_1_level
    below: 20
condition:
  # Only fire once per dip; don't re-fire if level oscillates around 20%.
  - condition: template
    value_template: >
      {{ trigger.from_state.state | float(100) >= 20 }}
action:
  - service: notify.whatsapp_family   # configure your notify service
    data:
      message: >
        💧 Drinking tank at {{ states('sensor.smartghar_a1b2c3d4e5f6_tank_1_level') }}%.
        Time to refill.
mode: single
```

## Refill confirmation

Fires when ANY tank rises 20% within 10 minutes — a refill event.

```yaml
alias: Tank refilled
trigger:
  - platform: state
    entity_id:
      - sensor.smartghar_a1b2c3d4e5f6_tank_1_level
      - sensor.smartghar_a1b2c3d4e5f6_tank_2_level
condition:
  - condition: template
    value_template: >
      {% set old = trigger.from_state.state | float(0) %}
      {% set new = trigger.to_state.state | float(0) %}
      {{ (new - old) >= 20 }}
action:
  - service: notify.mobile_app_my_phone
    data:
      title: Tank refilled
      message: >
        {{ trigger.to_state.attributes.friendly_name }} jumped to {{ trigger.to_state.state }}%.
mode: parallel
```

## Hub offline detection

Trips when the hub stops talking to HA for 5+ minutes.

```yaml
alias: SmartGhar Hub offline
trigger:
  - platform: state
    entity_id: sensor.smartghar_a1b2c3d4e5f6_uptime
    to: unavailable
    for: "00:05:00"
action:
  - service: notify.persistent_notification
    data:
      title: Hub unreachable
      message: >
        SmartGhar Hub hasn't been seen for 5+ minutes — check Wi-Fi or power.
mode: single
```

## Auto-cleanup OTA notification

Press the "Check for firmware updates" button on the first day of every month (so you're never running stale firmware).

```yaml
alias: Monthly OTA check
trigger:
  - platform: time
    at: "03:00:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
action:
  - service: button.press
    target:
      entity_id: button.smartghar_a1b2c3d4e5f6_check_for_firmware_updates
mode: single
```

## Heatmap / energy-style dashboard cards

Once v0.3.0 ships event entities, you can build:

```yaml
type: history-graph
entities:
  - sensor.smartghar_a1b2c3d4e5f6_tank_1_level
hours_to_show: 168    # one week
```

For richer visualizations, the upcoming `smartghar-lovelace` repo will ship a custom capsule card.

## Conditional automations using cross-tank correlation

This is the kind of automation that becomes possible with multi-tank visibility. Example: alert when sump tank empties faster than usual without a corresponding rise in the rooftop tank — implies pump failure.

```yaml
alias: Pump health check
trigger:
  - platform: numeric_state
    entity_id: sensor.smartghar_a1b2c3d4e5f6_tank_1_level     # sump
    below: 30
condition:
  - condition: template
    value_template: >
      {# rooftop tank should be filling if pump is running #}
      {% set rooftop = states('sensor.smartghar_a1b2c3d4e5f6_tank_2_level') | float(0) %}
      {% set rooftop_5min_ago = state_attr('sensor.smartghar_a1b2c3d4e5f6_tank_2_level', 'last_changed') %}
      {{ rooftop < 80 }}
action:
  - service: notify.mobile_app_my_phone
    data:
      title: ⚠️ Possible pump issue
      message: Sump dropping but rooftop not filling — check pump.
mode: single
```

(Real cross-tank correlation insights ship server-side in the SmartGhar PWA later — see the deferred Insights v1 plan.)
