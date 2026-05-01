# Contributing to SmartGhar — Home Assistant Integration

Thanks for considering a contribution. This integration is open-source on purpose: the goal is to keep SmartGhar genuinely no-lock-in and to let the community shape how it integrates with Home Assistant.

## Ways to help

- **File issues** for bugs, missing features, unclear docs
- **Improve the protocol spec** at [docs/protocol/v1.md](docs/protocol/v1.md) — clarifications, edge cases, missing fields
- **Write automation examples** in [docs/examples/](docs/examples/) — real-world automations users can copy/paste
- **Translate strings** — `custom_components/smartghar/translations/` (English exists; Hindi planned for v0.2.0)
- **Submit pull requests** — see "Development setup" below

## Development setup (once v0.1.0 ships)

```bash
git clone https://github.com/Techposts/smartghar-homeassistant.git
cd smartghar-homeassistant

# Install in your HA dev environment
ln -s "$(pwd)/custom_components/smartghar" \
      ~/.homeassistant/custom_components/smartghar

# Restart HA
```

Tests run with `pytest`; HACS + hassfest validation runs in CI on every push.

## Protocol-spec changes

The hub-side HTTP/WebSocket API is the contract this integration depends on. Breaking spec changes need:

1. A GitHub Discussion thread proposing the change
2. A 2-week comment window
3. A new schema version bump (e.g. v1 → v2) with a deprecation window for v1

Non-breaking additions (new fields, new endpoints) can go straight to a PR.

## Code style

- Follow [Home Assistant's development guidelines](https://developers.home-assistant.io/docs/development_guidelines)
- Type hints everywhere
- `ruff` + `black` for formatting (config in `pyproject.toml` once v0.1.0 lands)

## Code of conduct

Be kind. Discuss ideas, not people. We're building this for water-tank owners — most of them aren't programmers and we owe their patience back to each other.
