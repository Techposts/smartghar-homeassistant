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
