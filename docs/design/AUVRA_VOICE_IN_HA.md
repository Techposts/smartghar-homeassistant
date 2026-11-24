# Auvra voice in Home Assistant: one device, voice-ready

Status: design for branch `feat/auvra-voice-one-device` (24 Nov 2026). Nothing here is installed in anyone's Home
Assistant (HA). Verified against HA core 2026.10.4 (`wyoming==1.10.2`) and the Auvra firmware branch
`feat/voice-assistant` (`docs/notes/voice-progress.md` there).

## Problem

An Auvra unit with voice on shows up twice in HA:

1. The SmartGhar integration creates the Auvra device (media player, EQ, settings).
2. HA's stock Wyoming integration auto-discovers `_wyoming._tcp` and creates a second device, "Auvra Bedroom", with the
   `assist_satellite`, a pipeline select, noise-suppression and auto-gain entities, and a mute switch.

Two cards for one box is confusing to a buyer, and the voice settings (follow-up, wake sensitivity, tone, ducking)
live only on the unit's web page. Separately, a new user who asks "how much water is in the tank?" gets nothing,
because no entity is exposed to Assist and no friendly names exist.

## Approach

### 1. The SmartGhar integration becomes the Wyoming client

The Auvra firmware already runs a standard Wyoming satellite server on TCP 10700 (HA is the TCP client). The
integration now connects to it itself, on the existing Auvra device.

- New platform `assist_satellite` (file `assist_satellite.py`): `SmartGharAssistSatellite(AssistSatelliteEntity)`.
  It is adapted from HA core's `homeassistant/components/wyoming/assist_satellite.py` (Apache-2.0, the attribution
  stays in the file header and in `NOTICE`). It uses the `wyoming` package (`wyoming==1.10.2`, the same pin as core, so
  no resolver conflict on a stock install).
- Same device: `device_info` is `hub_device_info(coordinator)`. Auvra reports `topology: standalone`, so the hub
  device is the speaker, and every voice entity lands on the card that already holds the media player.
- Same connection logic as core: HA connects to host:10700, sends `run-satellite`, then `describe` at the start of each
  pipeline loop; `ping` after every `pong`; reconnect after 3 s (lost) or 10 s (refused). The event flow was verified
  against HA's own source in the firmware notes (push-to-talk `run-pipeline` from `asr`, `transcribe`,
  `voice-started` / `voice-stopped`, `transcript`, `synthesize`, TTS `audio-start` / `audio-chunk` / `audio-stop`,
  `played`; announce; timers).
- Host: the integration's stored host (`coordinator.client.host`). Port: `voice.port` if the unit reports it, else
  10700 (firmware ask below).
- What is deliberately NOT ported from core:
  - the noise-suppression, auto-gain and mic-volume entities. In 2026.10.4 nothing reads them: the base class passes
    only `AudioSettings(silence_seconds=...)` from the VAD select. They would be dead controls.
  - `pause-satellite` on mute (see mute below).
- The entity is `unavailable` while voice is off on the unit (`voice.enabled` false) or the hub is unreachable. The
  connect loop keeps retrying quietly, so turning voice on from the switch makes the satellite come up on its own.
- Timers: `intent.async_register_timer_handler` is registered with the HA device id of the Auvra device, as in core.

### 2. Voice settings as entities on the same device

Voice settings are not on the v1 API (`/api/v1/devices` accepts only eq / streams / announce keys). They live on the
legacy endpoints: `GET /api/state` -> `voice {...}` and `POST /api/set {"key": value}` (reply
`{"ok": bool, "rejected": [...]}`, same shape as the v1 PUT). The client gets `get_state()` and `set_keys()`.

The coordinator reads `/api/state` on each poll and keeps `voice`. A missing endpoint or missing block is not an error
(older firmware, TankSync hubs): `voice` is `{}` and no voice entity is created. The v1 WebSocket does not carry
`voice`, so settings refresh on the 30 s poll and immediately after any write we make.

A unit "reports voice support" when `/api/state` has a `voice` object with `mic: true`. If that flips (a mic added, a
firmware update) the entry reloads, like the existing capability check.

| Entity | Platform | Firmware key (write / read) | Category |
|---|---|---|---|
| Assist satellite (the unit) | assist_satellite | n/a | none |
| Assistant (pipeline) | select | HA side, `AssistPipelineSelect` | config |
| Finished-speaking sensitivity | select | HA side, `VadSensitivitySelect` | config |
| Voice assistant on/off | switch | `voice_enabled` / `enabled` | config |
| Microphone mute | switch | `mic_mute` / `muted` | none (user-facing) |
| Listening tone | switch | `voice_tone` / `tone` | config |
| Echo cancellation | switch | `voice_aec` / `aec` (firmware coming; hidden until the key is present) | config |
| Show to stock Wyoming discovery | switch | `wyoming_advertise` / `advertise` | config, the escape hatch |
| Follow-up | select off / on / question | `follow_up` | config |
| Wake word sensitivity | select low / normal / high | `wake_sens` / `wake_sens` | config |
| Listening tone style | select A / B / C | `voice_tone_style` / `tone_style` | config |
| Music ducking depth | number 0-40 dB | `duck_db` | config |

`voice_talk` (push to talk) is exposed as the satellite's own conversation start path: `assist_satellite.start_conversation`
is not supported by HA for Wyoming in 2026.10.4, so a **Talk** button entity calls `voice_talk: true`. (Stop is the same
button when a turn is running: `voice_talk: false`.)

### 3. Mute

The Microphone mute switch writes `mic_mute` on the unit. The unit already handles it (steady red light, nothing is
heard or sent, announcements still play) and its own dial/menu/web page toggle the same flag, so HA and the box never
disagree. The Wyoming connection stays open while muted. Core's behaviour (HA sends `pause-satellite` and drops the
link) is not used, because it hides the unit's real state from the user.

### 4. One device: how the stock Wyoming entry goes away

1. When the integration first brings the satellite up it sets `wyoming_advertise` off on the unit (only if it is on).
   The stock integration stops discovering it. An existing stock entry keeps working: it already holds host:port and
   needs no mDNS.
2. **Two clients must never drive one satellite.** The firmware sends pipeline events to the connection that sent
   `run-satellite`. So while an enabled stock `wyoming` config entry points at this unit (same host and port 10700, or
   a title equal to the unit name), the SmartGhar satellite does NOT connect. The rest of the integration is unaffected.
3. In that state the integration raises a **repair issue** ("Auvra Bedroom still has a separate Wyoming device"),
   fixable. The fix flow explains what will happen and, only when the user confirms, removes the matching Wyoming config
   entry (`hass.config_entries.async_remove`) and reloads the SmartGhar entry, which then starts the satellite. Nothing
   is ever deleted without that click. Disabling the Wyoming entry by hand also clears the conflict. The poll re-checks
   every 30 s, so a manual removal is picked up too.
4. Rollback: the `Show to stock Wyoming discovery` switch turns advertising back on. Removing the SmartGhar entry (or
   unticking "Voice assistant in this integration" in Options) sets `wyoming_advertise` back on, if we turned it off,
   so the stock integration can discover the unit again.
5. Pipeline assignment: the new pipeline select defaults to "preferred". A repair note tells the user to pick their old
   pipeline (the stock device's select is not migrated: it is HA side state of another integration).

### 5. Voice-ready option

Options flow checkbox **"Voice-ready: let Assist see this device"**, default ON (also shown in the add flow forms). A second
checkbox, **"Voice assistant in this integration"** (default ON, shown only for a unit with voice) controls section 1.
Both live in `entry.options`. A third optional text field, **water arrival times** (e.g. `06:30, 18:00`), feeds the
"next water arrival" sensor (decision below).

Applied after the platforms are set up and on every reload, per entity, once:

- Exposure: `homeassistant.components.homeassistant.exposed_entities.async_expose_entity(hass, "conversation", entity_id, True)`.
  This is the same store the "Voice assistants" UI writes (entity registry `options.conversation.should_expose`), so
  Assist, and any LLM agent using the Assist API, see them.
- Aliases: `entity_registry.async_update_entity(entity_id, aliases=...)`. Existing user aliases are kept; ours are added.
- **Ledger** (HA `Store`, key `smartghar.voice_ready`): for each entity we touched we record whether it was exposed before and
  which aliases we added. An entity already in the ledger is not touched again, so a user who later un-exposes one by
  hand keeps their choice, and a new tank added later is picked up once.
- **Unticking** reverts through the ledger: exposure goes back to what it was (if the entity was not exposed before, it is
  set not exposed), and only our aliases are removed. Removing the integration does the same. Nothing outside the ledger is
  changed.

What is exposed (English and Hindi aliases; names built from the user's tank / unit name):

| Product | Entity | Spoken names (examples) |
|---|---|---|
| TankSync, per tank | Level (sensor, %) | "main tank level", "water level", "tank kitna bhara hai", "टंकी का लेवल", "पानी का स्तर" |
| | Water volume (litres) | "water in the main tank", "टंकी में कितना पानी है" |
| | Water used today (new) | "water used today", "आज कितना पानी खर्च हुआ" |
| | Water used this week (new) | "water used this week", "इस हफ्ते कितना पानी खर्च हुआ" |
| | Last filled (new, text) | "when was the tank last filled", "टंकी आखिरी बार कब भरी" |
| TankSync, per Smart Switch | Pump relay (switch) | "pump", "motor", "पंप", "मोटर" |
| TankSync hub | Next water arrival (new, only if times are set) | "when does the water come next", "पानी कब आएगा" |
| Auvra | Its media player | "Auvra Bedroom" and "speaker", "स्पीकर": "pause", "turn it down", "next" work |

With more than one tank the generic names ("water level") are only added to a tank when it is the only one; otherwise
every alias carries the tank's name, so the answer is never ambiguous.

Never exposed: voltage, RSSI, uptime, firmware, connection state, sensor-error and OTA entities, all settings (the voice
settings above), buttons. They are diagnostic or config; an LLM that can write them can break the unit.

The pump relay is exposed as a switch, so "turn off the pump" works. That is a deliberate product decision: a stuck relay is
visible on the tank card and the pump's own automation can be resumed with the existing button. Ravi can drop it
(decision below).

### 6. Local time (the "02:30 UTC" problem)

HA stores and returns timestamps in UTC. An integration cannot edit a user's LLM prompt, so it makes the data local at
the source:

- Every time the integration wants spoken, it provides a **text sensor whose state is already a local phrase**
  (`dt_util.as_local`, HA's configured time zone): "today at 2:30 PM", "yesterday at 6:40 AM", "Mon 23 Nov at 7:05 PM".
  Attributes carry `date`, `time` (local, 12 h) and `hours_ago`. Those text sensors are the ones exposed to Assist.
- The raw `timestamp` sensors (for automations and history) are kept but **not exposed**, so an LLM never sees a bare UTC value
  for these.
- Next water arrival uses the same phrases ("today at 6:30 PM", "tomorrow at 6:30 AM") and attribute `in_minutes`.

## Every surface (feature-surfaces rule)

| Surface | Show it | Control it | Leave it / undo it |
|---|---|---|---|
| Device screen + dial | Unchanged: the unit already shows listening / thinking / answering and the mic-muted state. HA connection state still shown ("Voice offline / Reconnecting...") | Dial menu Voice submenu and double-press already exist; HA's switches write the same keys, so box and HA agree | Same menu |
| Screenless unit | LED states already exist (white breathing = listening, steady red = muted) | HA entities, the unit's web page, push to talk via the Talk button | HA switch / web page; reset path below |
| Web page | Unit's own Voice card, unchanged. It shows "Ready, Home Assistant connected" when our client is the one connected | Same card | Same card |
| Home Assistant | One device: satellite, mic mute, voice on/off, follow-up, wake sensitivity, tone, tone style, duck depth, Talk button, pipeline select, finished-speaking select | All of those entities | Options: untick "Voice assistant in this integration" (advertising returns), untick "Voice-ready" (exposure and aliases revert) |
| API | `/api/state` voice block is the source; `/api/set` is the only writer. No new firmware endpoint is needed | Same | Same |
| SmartGhar app | N/A in this change: the unified app (separate project) reads the same `voice` block later | | |

Answers to the six questions:

1. **Override.** Automatic behaviour is the follow-up listening and the wake word; the unit's own dial/menu/web page and
   the HA mute switch override them, and HA always shows the real state because the keys are read back from the unit. The
   stock-Wyoming conflict is shown as a repair issue with the way out.
2. **Stuck states.** HA restart or crash: the unit holds its own settings; our client reconnects within 3 s and
   re-runs `run-satellite`. A dead HA link: the unit already shows "Voice offline" and falls back to its own states. A
   wrong pipeline: pick another in the select, no factory reset. A forgotten state of `wyoming_advertise`: the switch.
   Both clients fighting: prevented by the conflict gate.
3. **Hindi.** The product ships English and Hindi. HA entity names come from `strings.json` / `translations`; this change
   adds `translations/hi.json` for the new entities. Aliases carry Hindi names. The Assist pipeline language is HA's
   (the English pipeline understands English only; a Hindi pipeline is a separate HA-side item noted in the firmware
   backlog), so Hindi aliases only help once a Hindi pipeline exists. That is why they are added now but called out.
4. **First-time user.** Zeroconf finds the Auvra, one confirm. "Voice-ready" is ticked, so "what is the tank level?" works
   with no further setup. A user who never reads anything sees one device. The Talk button and satellite state are
   self-describing.
5. **Real-path test.** Unit tests cover the client logic, the entities, the migration and the exposure ledger against
   a mocked unit and a mocked Wyoming link (protocol events driven through the real `on_pipeline_event`). The **real path is
   not covered by unit tests**: a live pairing with a real Auvra (stock entry removed, one device, a spoken turn, announce)
   is a bench test for Ravi (docs/design test plan below). A forced test path skips: mDNS, the real TCP socket, real TTS
   audio, ffmpeg.
6. **Docs.** This file, `docs/notes/voice-one-device-progress.md`, README/CHANGELOG entries, and the bench test below.

## Tests

Automated (pytest-homeassistant-custom-component, run with the repo's `pytest.ini`):

- API: `get_state` / `set_keys` bodies and the `rejected` handling.
- Pure logic: voice detection, firmware key mapping, alias generation, local phrases, next-arrival maths.
- Entities: created only for a unit with a voice block, correct writes, availability.
- Satellite: event translation for a pipeline run, announce, `played`, ping/pong; the conflict gate (no connect
  while a stock entry exists).
- Migration: repair issue is created, never auto-deletes, the fix flow removes only on confirm.
- Voice-ready: exposure + aliases applied, ledger honoured on reload, unticking reverts.
- Time zone: the local phrase for Asia/Kolkata is not the UTC string.

Bench (Ravi, one real unit, after review; not done by this change):

1. Install the branch in a test HA, restart, confirm the zeroconf flow for the Auvra and see the repair issue appear while
   the stock entry is present.
2. Fix the repair (removes the stock entry). Check there is one device, the satellite goes idle, a push-to-talk turn answers.
3. `assist_satellite.announce` plays. Mic mute switch matches the box. Follow-up and wake sensitivity change the box.
4. "What is the main tank level?" and "when was the tank last filled?" answer in local time.
5. Untick Voice-ready and confirm exposure returns to how it was.

## As built (differences from the plan above)

- The satellite connects to the integration's stored host and `voice.port` (default 10700). A real localhost Wyoming server
  test covers `run-satellite` then `describe`; the rest of the loop is core's, with timers forwarded as `timer-*` events.
- `voice_ready.py` also keeps Assist away from the settings the box cannot undo by voice: microphone mute, voice on/off,
  Talk, listening tone, echo cancellation and the advertising switch are set to not-exposed on first apply (HA exposes
  switches and buttons by default) and restored on untick. The ledger records their previous state.
- `wyoming_advertise` is turned off whenever the satellite option is on and the unit reports it on (once; remembered in the
  ledger). It is turned back on when the option is unticked or the entry is removed.
- The Options flow is the only place for the two checkboxes and the arrival times; the add flow is unchanged (both options
  default ON when missing).
- Sensors: `usage_sensor.py` (used today / week with restore, last fill with restore, next arrival). A rise of 3 percent or
  more counts as a fill.
- Not built: Hindi pipeline, a firmware `voice.port` field, the unified app.

## Rollback

- Per feature: the Options checkboxes above.
- Whole change: remove the SmartGhar entry (advertising is turned back on and exposure reverted), re-add or keep the stock
  Wyoming entry, and revert the branch. No firmware change is required for this to work, so nothing needs reflashing.
- The stock entry is never deleted without confirmation, so a user who declines the repair keeps the old behaviour (two
  devices, no SmartGhar satellite).

## Decisions for Ravi

1. **Firmware asks (small, optional):** add `"voice"` to `caps_json()` and a `voice.port` field. Until then, detection is
   "a `voice` object with `mic: true` in `/api/state`" and the port is 10700.
2. **"Next water arrival":** the integration has no data for it (no schedule exists in the hub). This change adds an optional
   list of daily arrival times in Options. Alternative: drop the sensor.
3. **"Water used today / this week" are new sensors**, computed from level drops by the same algorithm as the existing
   consumption sensor (local midnight / Monday reset).
4. **Pump exposed as a switch** ("turn off the pump" works). Drop it if that is too much for a default.
5. **Existing installs:** a missing `voice_ready` option counts as ON, so an upgrade exposes the curated set once. The
   alternative is to treat missing as OFF and ask.
6. **Hindi aliases** are added now, but only help once a Hindi Assist pipeline exists.
7. **Mute meaning:** the HA mute switch is the unit's own mic mute (recommended), not core's `pause-satellite`.
