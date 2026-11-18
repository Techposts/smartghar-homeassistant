# Auvra end-to-end test rig (throwaway Home Assistant)

A disposable Home Assistant core on the Mac, used to test this integration against a real Auvra.
It is **not** the production Home Assistant and must never be pointed at it: it has its own venv, its own config dir
and its own (test-only) user.

| What | Where |
|---|---|
| Home Assistant core 2026.10.4 | venv `/Volumes/EVM500/.hf-work/venvs/ha-core` (Python 3.14) |
| Config dir | `/Volumes/EVM500/.hf-work/ha-e2e/config` |
| This integration | `config/custom_components/smartghar` → symlink to `~/Projects/smartghar-homeassistant/custom_components/smartghar` |
| Media folder (chimes) | `/Volumes/EVM500/.hf-work/ha-e2e/media` (`media-source://media_source/local/...`) |
| Device under test | Auvra Bedroom, `auvra-0848.local` / 192.168.0.130 |

The integration is symlinked, so a restart of this HA picks up the working tree of whichever branch is checked out
(`feat/auvra-media` for the 1.2 work).

## Configuration

`config/configuration.yaml` (already in place):

```yaml
homeassistant:
  name: Auvra e2e (throwaway)
  country: IN
  time_zone: Asia/Kolkata
  unit_system: metric
  # The speaker fetches TTS / media from this URL: it must be the Mac's LAN IP.
  internal_url: http://192.168.0.183:8123
  media_dirs:
    local: /Volumes/EVM500/.hf-work/ha-e2e/media

default_config:

logger:
  default: info
  logs:
    custom_components.smartghar: debug
```

`internal_url` matters: TTS and media URLs are built from it, and the speaker has to reach them over Wi-Fi. Check the
Mac's address before a run (`ipconfig getifaddr en0`) and edit the line if it changed. HA listens on all interfaces
(`*:8123`); if macOS asks whether Python may accept incoming connections, allow it, or the speaker cannot fetch the
clips.

## Start and stop

Start (foreground; logs also go to `config/home-assistant.log`):

```sh
UV_CACHE_DIR=/Volumes/EVM500/.hf-work/uv-cache PIP_CACHE_DIR=/Volumes/EVM500/.hf-work/uv-cache/pip \
  /Volumes/EVM500/.hf-work/venvs/ha-core/bin/hass -c /Volumes/EVM500/.hf-work/ha-e2e/config
```

- The cache variables keep HA's on-demand package installs off the internal SSD.
- The first start (18 Nov 2026) installed the `default_config` packages (frontend included) in about a minute.
  The second start logged "Home Assistant initialized in 7.27s" with no errors, and the onboarding page served 200.
- Ready check: wait for `Home Assistant initialized in` in `config/home-assistant.log`. Port 8123 answers earlier,
  before `default_config` has finished.
- Then `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8123/` gives `302` (onboarding) or `200`.
- Before starting, `lsof -nP -iTCP:8123 -sTCP:LISTEN` should print nothing. If something holds 8123, add
  `http: {server_port: 8124}` to the config and use 8124 everywhere below (including `internal_url`).

Stop: Ctrl-C in the foreground, or `kill <pid>` (SIGTERM shuts HA down cleanly).

Reset to a fresh install: stop HA and delete everything in `config/` except `configuration.yaml` and
`custom_components/`.

## Onboarding (once per fresh config dir)

Open http://localhost:8123 and:

1. **Create the user**: name `Auvra Test`, username `auvratest`, a throwaway password (local only, never reused).
2. Location: Asia/Kolkata, metric. Skip analytics (everything off).
3. Skip the discovered devices page.
4. **TTS**: Settings → Devices & services → Add integration → *Google Translate text-to-speech* (free, no account;
   it is a UI config flow, not YAML). API onboarding already added it with language `en`, TLD `com`:
   entity `tts.google_translate_en_com`.
5. **Auvra**: it is usually discovered by zeroconf (`_smartghar._tcp`) under *Discovered*; otherwise Add integration →
   *SmartGhar* → host `192.168.0.130`, no token.
6. Optional chime: copy an MP3 to `/Volumes/EVM500/.hf-work/ha-e2e/media/doorbell.mp3`.
7. Optional radio: Add integration → *Radio Browser* (HA's own station directory; it shows up in the media browser).

## What to check against the device (firmware 0.10.0, schema 1.2)

Keep volume ≤ 10 (someone sleeps near "Auvra Bedroom"); judge results from device state and `/api/log`, not loudness.

1. **1.1 baseline (firmware 0.9.x)**: media_player only, no Channel / EQ preset / Bass / Treble / Loudness entities;
   media_player attributes have no source or sound-mode lists.
2. **After the OTA to 0.10.0**: within one poll (30 s) the entry reloads by itself and the new entities appear.
3. Controls: Channel select, EQ preset select and sound mode, Bass/Treble sliders, Loudness switch; each change is
   visible in `GET /api/v1/devices` `state`.
4. Source: select *Radio* / *AirPlay*; browse media → *Auvra favourites* (empty until stations are added on the
   device) and HA media sources (Radio Browser, local media); play one.
5. **Announce**: Developer tools → Actions → `tts.speak` with `entity_id: tts.google_translate_en_com`,
   `media_player_entity_id: media_player.auvra_bedroom`, message "Test announcement".
   - Expect `announcing: true` during the clip.
   - AirPlay or radio keeps its `source`, and playback is restored afterwards.
   - Idle: the clip plays alone.
6. Blueprints: import `blueprints/automation/smartghar/*.yaml` (Settings → Blueprints → Import, or copy them to
   `config/blueprints/automation/smartghar/`), create one automation from each, and trigger with Developer tools →
   States (set the tank level sensor above the threshold; set a made-up `binary_sensor.test_doorbell` from off to on).

## Announce / radio / URL run (firmware 0.10.0-rc7 or later)

One script runs every check: the streams / announcement entities against v1 `config` (and one write); TTS
announcements over idle, radio and AirPlay (`announce: true`, volume 8); play_media of a favourite and of a plain URL;
next/previous; select_source (AirPlay must end in `airplay` + playing when a sender is connected, else `idle`);
browsing the favourites (thumbnails = favicons); the station logo as a proxied `entity_picture` (ui2: one test
favourite gets a favicon, the other none); the radio state fields; the WebSocket push on source changes; and streams off/on dropping
and restoring HA's features through the pushed `hub.capabilities`. It adds two favourites from the radio test set, uses a third station as the
plain URL, and deletes only the favourites it added. Volume stays at 8: a watcher on the device's WebSocket fails the
run and sets 8 again if it ever sees more.

```sh
# HA must be up first (Start and stop, above). For the AirPlay step, play something from Music.app to the speaker
# and pause it, so a sender is connected; otherwise that step is skipped.
/Volumes/EVM500/.hf-work/venvs/ha-test/bin/python \
  ~/Projects/smartghar-homeassistant/tests/e2e/auvra_announce_radio.py
# only the AirPlay announcement:  ... auvra_announce_radio.py --only-airplay
```

Each check prints `[PASS]`, `[FAIL]` or `[SKIP]` with the measured times. The exit code is 0 when nothing failed.

Dry run without the board: `tests/e2e/fake_auvra.py 0.0.0.0 80 [nosender]` plays the rc7 device, and a second config dir
`/Volumes/EVM500/.hf-work/ha-e2e-dry/config` (smartghar entry on 127.0.0.1, only `api` and `media_source`, HA on
127.0.0.1:8124) runs HA against it. Then run the script with `--ha http://127.0.0.1:8124 --device 127.0.0.1`.
With rc7 + ui2 behaviour (18 Nov 2026): 30 pass, 0 fail with a sender.

## Rig state (after the 18 Nov 2026 Tier-1 run)

- Onboarded through the onboarding API. Test user `auvratest`; password in
  `/Volumes/EVM500/.hf-work/ha-e2e/test-user.txt`, a 30-day long-lived token in `token.txt` (both mode 600, local only).
- The Auvra config entry exists (added from zeroconf discovery). Google Translate TTS (`tts.google_translate_en_com`)
  and Radio Browser were added by the onboarding step.
- Scripted calls: `curl -H "Authorization: Bearer $(cat /Volumes/EVM500/.hf-work/ha-e2e/token.txt)"
  http://localhost:8123/api/services/<domain>/<service> -d '{...}'`.

## Unit tests (no device)

```sh
export UV_CACHE_DIR=/Volumes/EVM500/.hf-work/uv-cache
# once: uv venv -p /opt/homebrew/bin/python3.14 /Volumes/EVM500/.hf-work/venvs/ha-test
#       VIRTUAL_ENV=/Volumes/EVM500/.hf-work/venvs/ha-test uv pip install pytest-homeassistant-custom-component
cd ~/Projects/smartghar-homeassistant
/Volumes/EVM500/.hf-work/venvs/ha-test/bin/python -m pytest -q
python3 tests/test_speaker_state_map.py   # the script-style checks run on their own
```
