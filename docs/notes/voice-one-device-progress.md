# Auvra voice, one device: progress

Branch `feat/auvra-voice-one-device`. Design: `docs/design/AUVRA_VOICE_IN_HA.md`.
Rules: no push, no release, nothing installed in the live HA, no AI mention in commits or docs.

Test env: venv `/Volumes/EVM500/.hf-work/venvs/ha-test` (pytest-homeassistant-custom-component 0.13.375, HA 2026.10.4).
Added to it for this work: wyoming==1.10.2, ruff, hassil, gazetteer-matcher, home-assistant-intents, pymicro-vad,
pyspeex-noise, ha-ffmpeg, mutagen (see requirements_test.txt).
Run: `ha-test/bin/python -m pytest -q` and `ha-test/bin/ruff check custom_components tests`.

| Step | State |
|---|---|
| 0 ruff baseline (ruff.toml, imports sorted) | done |
| 1 design doc | done |
| 2 api + coordinator voice block, pure logic | done |
| 3 satellite entity + Wyoming client | done (localhost wire test) |
| 4 settings entities (switch / select / number / button) | done |
| 5 migration: advertise off, repair issue, fix flow | done |
| 6 voice-ready: exposure ledger, aliases, options flow | done |
| 7 TankSync voice sensors (used today/week, last fill, next arrival) | done |
| 8 translations (en, hi), README, CHANGELOG, NOTICE | done |

Result: 97 tests pass, ruff clean. Not done: bench test with a real unit (design doc, Bench section), manifest version bump,
push, release. Decisions for Ravi are in the design doc (Decisions for Ravi).

## Bench test on the live Home Assistant (25 Nov 2026, Auvra Bedroom, firmware 0.12.0-voice33)

Installed from this branch through HACS (was v0.13.0), HA restarted. Before/after snapshots kept outside the repo.

| Step | Result |
|---|---|
| 1 install, repair issue while the stock entry exists | pass: entry loaded, `stock_wyoming` repair shown, 7 voice entities on the Auvra device |
| 2 fix the repair | pass: stock Wyoming entry removed, one "Auvra Bedroom" device; the satellite took over `assist_satellite.auvra_bedroom` (automations keep working) after the entry settled; Assistant set back to "Auvra (local)" |
| 2 push-to-talk | pass: HA Talk button -> speaker and satellite "listening" |
| 3 announce, mute, follow-up, wake sensitivity | pass: announce played; HA select/switch changes reached the box within seconds (cutoff 215 -> 230 -> 215) |
| 4 tank questions in local time | pass: "The main water tank is at 72%", "last filled on November 25, 2026, at 7:28 AM" (spoken turns through the box mic) |
| 5 voice-ready off/on | pass: off exposes only the two box settings switches again (HA default); on returns exposure identical (103 entities) |

Note: the stock Wyoming device's noise suppression / auto gain settings do not reach the audio for this satellite type in
HA 2026.10 (`AssistSatelliteEntity` passes only `silence_seconds`), so nothing is lost by the move.
Rollback: HACS redownload v0.13.0, restart, re-add the Wyoming entry from discovery.
