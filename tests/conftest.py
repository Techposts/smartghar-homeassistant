"""Shared fixtures: a mocked Auvra on the v1 HTTP API.

The device is mocked at the HTTP layer (aioclient_mock), so api.py's request
paths and bodies are part of what the tests check.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartghar.const import CONF_HUB_ID, DOMAIN

from .fixtures import BASE, HOST, HUB_ID


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/smartghar in every test."""


@pytest.fixture(autouse=True)
def no_websocket():
    """info.stream.ws_path would start a real WS loop to the device."""
    with patch(
        "custom_components.smartghar.coordinator.SmartGharCoordinator.start_ws"
    ):
        yield


def mock_device(
    aioclient_mock: AiohttpClientMocker,
    info: dict[str, Any],
    devices: dict[str, Any],
    stations: Any = None,
    state: dict[str, Any] | None = None,
) -> None:
    """Register the GET endpoints an Auvra answers, plus PUT/POST acks."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/v1/info", json=info)
    aioclient_mock.get(f"{BASE}/api/v1/devices", json=devices)
    aioclient_mock.get(f"{BASE}/api/v1/hub/led", json={"brightness": 100})
    aioclient_mock.get(f"{BASE}/api/v1/hub/buzzer", status=404, text="Not found")
    aioclient_mock.get(f"{BASE}/api/switches", status=404, text="Not found")
    if stations is not None:
        aioclient_mock.get(f"{BASE}/api/v1/radio/stations", json=stations)
    else:
        aioclient_mock.get(f"{BASE}/api/v1/radio/stations", status=404, text="Not found")
    if state is not None:
        aioclient_mock.get(f"{BASE}/api/state", json=state)
    else:
        aioclient_mock.get(f"{BASE}/api/state", status=404, text="Not found")
    aioclient_mock.post(f"{BASE}/api/set", json={"ok": True, "rejected": []})
    aioclient_mock.put(f"{BASE}/api/v1/devices/1", json={"ok": True, "rejected": []})
    aioclient_mock.post(f"{BASE}/api/v1/devices/1/command", json={"ok": True})


def calls(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list[Any]:
    """JSON bodies sent with METHOD to path, in order."""
    return [
        data
        for (m, url, data, _headers) in aioclient_mock.mock_calls
        if m.upper() == method.upper() and str(url) == f"{BASE}{path}"
    ]


@pytest.fixture
def setup_auvra(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> Callable[..., Any]:
    """Factory: mock the device with the given payloads and set up the entry."""

    async def _setup(
        info, devices, stations=None, state=None, options=None
    ) -> MockConfigEntry:
        mock_device(aioclient_mock, info, devices, stations, state)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=HUB_ID,
            title="Auvra Bedroom",
            data={CONF_HOST: HOST, CONF_HUB_ID: HUB_ID},
            options=options or {},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup
