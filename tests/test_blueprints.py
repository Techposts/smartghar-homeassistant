"""The Auvra blueprints load, validate and call the right services."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint import models
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

BP_DIR = Path(__file__).parent.parent / "blueprints" / "automation" / "smartghar"
SPEAKER = "media_player.auvra_bedroom"
TTS = "tts.google_translate_en_com"


def _automation(name: str, inputs: dict) -> dict:
    path = BP_DIR / name
    bp = models.Blueprint(
        load_yaml_dict(str(path)),
        expected_domain=AUTOMATION_DOMAIN,
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    assert bp.validate() is None
    bi = models.BlueprintInputs(
        bp, {"use_blueprint": {"path": name, "input": inputs}}
    )
    bi.validate()
    return {"id": name, "alias": name, **bi.async_substitute()}


@pytest.mark.parametrize("name", sorted(p.name for p in BP_DIR.glob("*.yaml")))
async def test_every_blueprint_is_valid(name: str) -> None:
    data = load_yaml_dict(str(BP_DIR / name))
    models.Blueprint(data, expected_domain=AUTOMATION_DOMAIN, schema=AUTOMATION_BLUEPRINT_SCHEMA)


async def test_tank_full_announces(hass: HomeAssistant) -> None:
    speak = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("sensor.overhead_tank_level", "80",
                          {"friendly_name": "Overhead Tank Level", "unit_of_measurement": "%"})
    config = _automation("tank-full-announce-auvra.yaml", {
        "tank_sensor": "sensor.overhead_tank_level",
        "speakers": [SPEAKER],
        "tts_engine": TTS,
        "cooldown_minutes": 0,  # a pending delay would stall async_block_till_done
    })
    assert await async_setup_component(hass, AUTOMATION_DOMAIN, {AUTOMATION_DOMAIN: [config]})
    await hass.async_block_till_done()

    hass.states.async_set("sensor.overhead_tank_level", "96",
                          {"friendly_name": "Overhead Tank Level", "unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert speak == []  # debounced for 30 s
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()

    assert len(speak) == 1
    assert speak[0].data["entity_id"] == [TTS]
    assert speak[0].data["media_player_entity_id"] == [SPEAKER]
    assert speak[0].data["message"] == "Overhead Tank is full. You can switch off the pump."
    assert "language" not in speak[0].data


async def test_doorbell_chimes_then_speaks(hass: HomeAssistant) -> None:
    play = async_mock_service(hass, "media_player", "play_media")
    speak = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("binary_sensor.front_door_ding", "off")
    config = _automation("doorbell-chime-announce-auvra.yaml", {
        "doorbell": "binary_sensor.front_door_ding",
        "speakers": [SPEAKER],
        "chime": "media-source://media_source/local/doorbell.mp3",
        "tts_engine": TTS,
        "cooldown_seconds": 0,
    })
    assert await async_setup_component(hass, AUTOMATION_DOMAIN, {AUTOMATION_DOMAIN: [config]})
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.front_door_ding", "on")
    await hass.async_block_till_done()
    # An attribute-only update while still on must not ring again.
    hass.states.async_set("binary_sensor.front_door_ding", "on", {"battery": 80})
    await hass.async_block_till_done()

    assert len(play) == 1
    assert play[0].data["entity_id"] == [SPEAKER]
    assert play[0].data["media_content_id"] == "media-source://media_source/local/doorbell.mp3"
    assert play[0].data["announce"] is True
    assert len(speak) == 1
    assert speak[0].data["message"] == "Someone is at the door."


async def test_doorbell_event_entity_without_chime(hass: HomeAssistant) -> None:
    play = async_mock_service(hass, "media_player", "play_media")
    speak = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("event.doorbell_ding", "2026-11-18T01:00:00+00:00")
    config = _automation("doorbell-chime-announce-auvra.yaml", {
        "doorbell": "event.doorbell_ding",
        "speakers": [SPEAKER],
        "tts_engine": TTS,
        "cooldown_seconds": 0,
    })
    assert await async_setup_component(hass, AUTOMATION_DOMAIN, {AUTOMATION_DOMAIN: [config]})
    await hass.async_block_till_done()

    hass.states.async_set("event.doorbell_ding", "2026-11-18T01:05:00+00:00")
    await hass.async_block_till_done()
    assert play == []
    assert len(speak) == 1


async def test_doorbell_event_type_filter(hass: HomeAssistant) -> None:
    speak = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("event.doorbell", "2026-11-18T01:00:00+00:00", {"event_type": "ring"})
    config = _automation("doorbell-chime-announce-auvra.yaml", {
        "doorbell": "event.doorbell",
        "event_type": "ring",
        "speakers": [SPEAKER],
        "tts_engine": TTS,
        "cooldown_seconds": 0,
    })
    assert await async_setup_component(hass, AUTOMATION_DOMAIN, {AUTOMATION_DOMAIN: [config]})
    await hass.async_block_till_done()

    hass.states.async_set("event.doorbell", "2026-11-18T01:01:00+00:00", {"event_type": "motion"})
    await hass.async_block_till_done()
    assert speak == []
    hass.states.async_set("event.doorbell", "2026-11-18T01:02:00+00:00", {"event_type": "ring"})
    await hass.async_block_till_done()
    assert len(speak) == 1
