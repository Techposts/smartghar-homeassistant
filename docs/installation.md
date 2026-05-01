# Installation guide

This guide installs the SmartGhar integration on Home Assistant. The whole thing happens locally on your home network — no SmartGhar account, no cloud anywhere in the HA path.

## Prerequisites

- A SmartGhar Hub (TankSync) on firmware **rx-v2.7.0** or newer, claimed to a SmartGhar account via the PWA, and connected to your home Wi-Fi.
- Home Assistant 2024.1.0 or newer (any flavor: HAOS, Container, Supervised, Core).
- HACS installed. ([Get HACS →](https://hacs.xyz/docs/setup/prerequisites/))
- HA and the hub on the **same Wi-Fi network**. (If they're on different VLANs, see [docs/multi-hub.md](multi-hub.md) — TL;DR: ensure mDNS + port 80 traffic flows between them.)

## Install via HACS (custom repository)

While the integration is pre-1.0 it's distributed as a HACS custom repository. Once it stabilises we'll submit to the HACS default repo and this step goes away.

1. Open Home Assistant → **HACS** → **Integrations**.
2. Click the three-dots menu (top right) → **Custom repositories**.
3. Paste:
   ```
   https://github.com/Techposts/smartghar-homeassistant
   ```
4. Category: **Integration**. Click **Add**.
5. Search HACS for "SmartGhar" and click **Download**.
6. **Restart Home Assistant** (Settings → System → Restart).

## Add your hub

After the restart, HA's zeroconf scanner will find any SmartGhar Hubs broadcasting on your LAN. You'll see a notification like:

> 🔔 New device discovered: **SmartGhar Hub 'tanksync-a1b2'**

Click **Configure** → **Submit**. That's it.

### If auto-discovery doesn't fire

Maybe your network's mDNS is filtered, or HA started before the hub. Add manually:

1. Settings → **Devices & Services** → **Add Integration** → search "SmartGhar".
2. Enter the hub's IP address (find it on the hub's web UI System tab, or in your router's DHCP table — look for `tanksync-XXXX`).
3. Leave **Local API token** blank unless you've enabled token auth on the hub web UI.
4. Click **Submit**.

### Multiple hubs

Each hub auto-discovers independently. Add them one by one — they appear as separate device cards under **SmartGhar**. Entity IDs are namespaced by the hub's MAC-derived `hub_id`, so there's no risk of collision. See [docs/multi-hub.md](multi-hub.md) for layout examples.

## Verify it's working

1. Settings → **Devices & Services** → **SmartGhar** → click your hub.
2. You should see one device card per hub plus one card per attached tank.
3. Tank cards show level (%), TX battery voltage, LoRa signal, connection state.
4. Tap any tank → expand its name field → rename it → press Enter. Within ~30 seconds the same name should appear in the SmartGhar PWA. (Bidirectional sync working.)
5. Hub card has a **"Check for firmware updates"** button. Press it; the OTA banner in the SmartGhar PWA should reflect the result.

## Troubleshooting

### "Failed to reach the hub"
- Confirm HA can reach the hub: `ping tanksync-XXXX.local` from a terminal in HA's host shell.
- If `ping` works but the integration fails, check if you have a local API token enabled on the hub. Re-copy it.

### Hub appears but entities show "unavailable"
- Most likely the hub rebooted between polls. Wait 30 seconds — it should self-recover on the next coordinator tick.
- If it persists, check HA logs: Settings → System → Logs → search for `smartghar`. Errors there will say exactly which API call failed.

### Auto-discovery doesn't find the hub
- Check that Home Assistant's **Zeroconf** integration is enabled (it usually is by default).
- Run `dns-sd -B _smartghar._tcp` from a Mac or `avahi-browse -r _smartghar._tcp` from Linux on the same network. If the hub doesn't appear there either, the issue is the hub's mDNS, not HA. Restart the hub and try again.

### Bidirectional sync not working
- v0.2.0 polls every 30 seconds. Edits made in HA take up to 30 seconds to reflect in the SmartGhar PWA (and vice versa). Real-time push lands in v0.3.0.
- If after 60 seconds nothing has propagated, check the hub's MQTT connection in the PWA — config-sync requires the hub to be reachable from the SmartGhar cloud.

### Filing a bug
[GitHub issues →](https://github.com/Techposts/smartghar-homeassistant/issues). Include:
- HA version
- Hub firmware version (visible in `sensor.smartghar_<hub_id>_firmware_version`, or from the hub web UI)
- Logs from Settings → System → Logs filtered to `custom_components.smartghar`
