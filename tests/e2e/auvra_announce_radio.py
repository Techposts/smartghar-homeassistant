#!/usr/bin/env python3
"""Auvra announce / radio / URL end-to-end run through a throwaway Home Assistant.

Drives the SmartGhar integration in the e2e rig (docs/auvra-e2e.md) against a
real Auvra on firmware 0.10.0-rc7 or later (schema 1.2 with radio, url_play,
announce; v1 config streams / announce_min_volume / announce_duck_db;
hub.capabilities in WS snapshots) and checks both what HA shows and what the
device reports.

Safety:
  - Volume is set to 8 first; every announcement and URL goes with volume 8.
  - A watcher reads every device snapshot: any volume above 8 is a FAIL and is
    set back to 8 at once. volume_up is never used.
  - Favourites the run adds are deleted again at the end; existing ones stay.

Run (hass from docs/auvra-e2e.md must already be up):
  /Volumes/EVM500/.hf-work/venvs/ha-test/bin/python \
      ~/Projects/smartghar-homeassistant/tests/e2e/auvra_announce_radio.py

Exit code 0 = every step passed or was skipped with a reason.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

DEF_TOKEN = "/Volumes/EVM500/.hf-work/ha-e2e/token.txt"
DEF_STATIONS = str(
    Path.home() / "Projects/smartghar-airplay-speaker/docs/notes/radio-test-stations.json"
)
VOLUME_MAX = 8
# Test stations come from radio-test-stations.json and are picked at run time
# from those whose URL is NOT already a device favourite (the device may hold
# Ravi's own favourites; a POST of an existing URL returns that favourite, and a
# play_url of a favourite's URL plays it as radio). Existing favourites are
# never changed or deleted; the run deletes only what it added.
# Logo favourite (ui2): one of these, added with a favicon.
LOGO_CANDIDATES = {
    "Hits Of Bollywood": "https://zeno.fm/favicon.ico",
    "Hits Of Lata Mangeshkar": "https://zeno.fm/favicon.ico",
}
# Favourite without a logo, then the plain-URL station: free MP3 streams.
PLAIN_CANDIDATES = ("Mahakatha Radio", "Oye Radio", "SVBC TTD Radio", "Radio Paradise (MP3)",
                    "Hits Of Lata Mangeshkar", "Radio Swiss Classic", "FIP", "BBC World Service")

# media_player feature bits (homeassistant.components.media_player.const)
F_PLAY_MEDIA = 512
F_SELECT_SOURCE = 2048
F_BROWSE_MEDIA = 131072
F_MEDIA_ANNOUNCE = 1048576


@dataclass
class Result:
    name: str
    status: str  # PASS / FAIL / SKIP
    detail: str = ""


@dataclass
class Run:
    args: argparse.Namespace
    http: aiohttp.ClientSession
    results: list[Result] = field(default_factory=list)
    snapshots: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    volume_violations: list[str] = field(default_factory=list)
    added_station_ids: list[int] = field(default_factory=list)
    url_station: dict[str, Any] | None = None
    sender_connected: bool = False

    # ── reporting ──────────────────────────────────────────────────────────
    def record(self, name: str, ok: bool | None, detail: str = "") -> bool:
        status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        self.results.append(Result(name, status, detail))
        print(f"[{status}] {name}" + (f": {detail}" if detail else ""), flush=True)
        return bool(ok)

    # ── device (read-only except favourites + restore) ─────────────────────
    @property
    def dev_base(self) -> str:
        return f"http://{self.args.device}"

    async def dev_get(self, path: str) -> Any:
        async with self.http.get(self.dev_base + path, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return await r.json(content_type=None)

    async def dev_state(self) -> dict[str, Any]:
        d = await self.dev_get("/api/v1/devices")
        return d["devices"][0]["state"]

    async def dev_stations(self) -> list[dict[str, Any]]:
        d = await self.dev_get("/api/v1/radio/stations")
        return d if isinstance(d, list) else d.get("stations", [])

    async def dev_post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        async with self.http.post(self.dev_base + path, json=body,
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
            return r.status, await r.json(content_type=None)

    async def dev_delete_station(self, sid: int) -> tuple[int, Any]:
        async with self.http.delete(f"{self.dev_base}/api/v1/radio/stations?id={sid}",
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
            return r.status, await r.json(content_type=None)

    async def wait_dev(self, cond, timeout: float, what: str) -> tuple[bool, dict[str, Any], float]:
        t0 = time.monotonic()
        st: dict[str, Any] = {}
        while time.monotonic() - t0 < timeout:
            try:
                st = await self.dev_state()
                if cond(st):
                    return True, st, time.monotonic() - t0
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.5)
        return False, st, time.monotonic() - t0

    # ── Home Assistant ─────────────────────────────────────────────────────
    @property
    def ha_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.args.token}"}

    async def ha_state(self, entity_id: str | None = None) -> dict[str, Any]:
        eid = entity_id or self.args.entity
        async with self.http.get(f"{self.args.ha}/api/states/{eid}", headers=self.ha_headers) as r:
            return await r.json()

    async def ha_call(self, domain: str, service: str, data: dict[str, Any]) -> tuple[int, str]:
        async with self.http.post(f"{self.args.ha}/api/services/{domain}/{service}",
                                  headers=self.ha_headers, json=data,
                                  timeout=aiohttp.ClientTimeout(total=30)) as r:
            return r.status, (await r.text())[:300]

    async def ha_ws(self, msg: dict[str, Any]) -> dict[str, Any]:
        url = self.args.ha.replace("http", "ws", 1) + "/api/websocket"
        async with self.http.ws_connect(url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.args.token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            await ws.send_json({"id": 1, **msg})
            while True:
                r = await ws.receive_json()
                if r.get("id") == 1:
                    return r

    async def wait_ha(self, cond, timeout: float) -> tuple[bool, dict[str, Any], float]:
        t0 = time.monotonic()
        st: dict[str, Any] = {}
        while time.monotonic() - t0 < timeout:
            st = await self.ha_state()
            if cond(st):
                return True, st, time.monotonic() - t0
            await asyncio.sleep(0.25)
        return False, st, time.monotonic() - t0

    async def set_volume_8(self) -> None:
        await self.ha_call("media_player", "volume_set",
                           {"entity_id": self.args.entity, "volume_level": VOLUME_MAX / 100})

    def tts_id(self, message: str) -> str:
        q = urllib.parse.urlencode({"message": message, "language": "en"})
        return f"media-source://tts/{self.args.tts}?{q}"

    async def announce(self, message: str) -> tuple[int, str]:
        return await self.ha_call("media_player", "play_media", {
            "entity_id": self.args.entity,
            "media_content_type": "music",
            "media_content_id": self.tts_id(message),
            "announce": True,
            "extra": {"volume": VOLUME_MAX},
        })

    # ── device WebSocket watcher (push + volume guard) ─────────────────────
    async def watch_device_ws(self, stop: asyncio.Event) -> None:
        url = f"ws://{self.args.device}/api/v1/stream"
        while not stop.is_set():
            try:
                async with self.http.ws_connect(url, heartbeat=20) as ws:
                    while not stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.receive(), 1.0)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        d = json.loads(msg.data)
                        if d.get("kind") != "snapshot":
                            continue
                        st = d["devices"][0]["state"]
                        self.snapshots.append((time.monotonic(), st))
                        vol = st.get("volume")
                        if isinstance(vol, (int, float)) and vol > VOLUME_MAX:
                            self.volume_violations.append(f"volume {vol} at {time.strftime('%T')}")
                            await self.set_volume_8()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(1)

    def pushed_since(self, t0: float, cond) -> float | None:
        for ts, st in self.snapshots:
            if ts >= t0 and cond(st):
                return ts - t0
        return None


# ── steps ──────────────────────────────────────────────────────────────────


async def step_preflight(run: Run) -> bool:
    info = await run.dev_get("/api/v1/info")
    caps = set(info.get("capabilities") or [])
    need = {"radio", "url_play", "announce"}
    ok = run.record("device lists radio, url_play, announce", need <= caps,
                    f"fw {info.get('fw_version')} schema {info.get('schema_version')} caps {sorted(caps)}")
    st = await run.ha_state()
    feats = int(st.get("attributes", {}).get("supported_features", 0))
    want = {"PLAY_MEDIA": F_PLAY_MEDIA, "MEDIA_ANNOUNCE": F_MEDIA_ANNOUNCE,
            "SELECT_SOURCE": F_SELECT_SOURCE, "BROWSE_MEDIA": F_BROWSE_MEDIA}
    missing = [n for n, b in want.items() if not feats & b]
    ok &= run.record("HA media_player has the 1.2 features", not missing,
                     f"missing {missing}" if missing else f"features {feats}")
    await run.set_volume_8()
    okv, dst, _ = await run.wait_dev(lambda s: s.get("volume") == VOLUME_MAX, 10, "volume 8")
    ok &= run.record("volume set to 8", okv, f"device volume {dst.get('volume')}")
    run.sender_connected = bool(dst.get("active")) and dst.get("source") in ("airplay", None)
    print(f"       AirPlay sender connected: {run.sender_connected} ({dst.get('sender')!r})", flush=True)
    return ok


def _eid(domain: str, name: str) -> str:
    return f"{domain}.auvra_bedroom_{name}"


async def step_entities(run: Run) -> None:
    """rc7: v1 config carries streams / announce_min_volume / announce_duck_db."""
    cfg = (await run.dev_get("/api/v1/devices"))["devices"][0].get("config") or {}
    want = {
        _eid("switch", "radio_and_streams"): "on" if cfg.get("streams") else "off",
        _eid("number", "announcement_minimum_volume"): float(cfg.get("announce_min_volume", -1)),
        _eid("number", "announcement_ducking"): float(cfg.get("announce_duck_db", -1)),
    }
    got = {}
    for eid in want:
        st = await run.ha_state(eid)
        val = st.get("state")
        try:
            val = float(val) if eid.startswith("number.") else val
        except (TypeError, ValueError):
            pass
        got[eid] = val
    run.record("streams / announce entities match v1 config", got == want, f"HA {got} device {cfg}")
    floor_eid = _eid("number", "announcement_minimum_volume")
    old = cfg.get("announce_min_volume")
    target = 7 if old != 7 else 6
    await run.ha_call("number", "set_value", {"entity_id": floor_eid, "value": target})
    await asyncio.sleep(1)
    cfg2 = (await run.dev_get("/api/v1/devices"))["devices"][0].get("config") or {}
    await run.ha_call("number", "set_value", {"entity_id": floor_eid, "value": old})
    cfg3 = (await run.dev_get("/api/v1/devices"))["devices"][0].get("config") or {}
    run.record("announcement minimum volume writes through v1 PUT",
               cfg2.get("announce_min_volume") == target and cfg3.get("announce_min_volume") == old,
               f"{old} -> {cfg2.get('announce_min_volume')} -> {cfg3.get('announce_min_volume')}")


async def step_favourites(run: Run, stations: list[dict[str, Any]]) -> dict[str, int]:
    """Add one favourite with a logo and one without, from test stations whose
    URL is not a favourite yet; choose the plain-URL station the same way.
    Returns {device name: id}; checks compare by id and URL, names come from
    the device."""
    existing = await run.dev_stations()
    taken = {s["url"] for s in existing}
    by_name = {s["name"]: s for s in stations}

    def free(name: str) -> bool:
        return name in by_name and by_name[name]["url"] not in taken

    logo_name = next((n for n in LOGO_CANDIDATES if free(n)), None)
    plain_name = next((n for n in PLAIN_CANDIDATES if free(n) and n != logo_name), None)
    picks = [(logo_name, LOGO_CANDIDATES.get(logo_name or "")), (plain_name, None)]
    ids: dict[str, int] = {}
    for name, favicon in picks:
        if name is None:
            run.record("pick a test favourite", False, "every candidate URL is already a favourite")
            continue
        st = by_name[name]
        body_in: dict[str, Any] = {"name": st["name"], "url": st["url"]}
        if st.get("codec"):
            body_in["codec"] = str(st["codec"]).lower()
        if isinstance(st.get("bitrate"), int):
            body_in["bitrate"] = st["bitrate"]
        if favicon:
            body_in["favicon"] = favicon
        status, body = await run.dev_post("/api/v1/radio/stations", body_in)
        if status == 200 and body.get("ok"):
            ids[name] = int(body["id"])
            run.added_station_ids.append(int(body["id"]))
            taken.add(st["url"])
        else:
            run.record(f"add favourite {name}", False, f"HTTP {status} {body}")
    # Names as the device stores them, by id.
    names = {s["id"]: s["name"] for s in await run.dev_stations()}
    ids = {names.get(i, n): i for n, i in ids.items()}
    url_name = next((n for n in PLAIN_CANDIDATES if free(n) and by_name[n]["url"] not in taken), None)
    if url_name is None:
        url_name = next((s["name"] for s in stations if s["url"] not in taken), None)
    run.url_station = by_name.get(url_name) if url_name else None
    run.record("favourites ready", len(ids) == 2 and run.url_station is not None,
               f"added {ids} (logo: {logo_name}); plain URL: {url_name}; "
               f"{len(existing)} existing favourites left untouched")
    return ids


async def step_browse(run: Run, fav_ids: dict[str, int]) -> None:
    root = await run.ha_ws({"type": "media_player/browse_media", "entity_id": run.args.entity})
    children = (root.get("result") or {}).get("children") or []
    titles = [c.get("title") for c in children]
    run.record("browse root has Auvra favourites + HA media sources",
               "Auvra favourites" in titles and len(children) >= 2, f"{titles}")
    fav = await run.ha_ws({"type": "media_player/browse_media", "entity_id": run.args.entity,
                           "media_content_id": "smartghar://radio", "media_content_type": "channels"})
    kids = (fav.get("result") or {}).get("children") or []
    got = {c.get("title"): c.get("media_content_id") for c in kids}
    ok = all(got.get(n) == f"smartghar://radio/{i}" for n, i in fav_ids.items())
    run.record("browse favourites lists the stations", ok, f"{got}")
    favicons = {s["id"]: s.get("favicon") for s in await run.dev_stations()}
    thumbs = {c.get("media_content_id"): c.get("thumbnail") for c in kids}
    bad = {f"smartghar://radio/{i}": (favicons.get(i), thumbs.get(f"smartghar://radio/{i}"))
           for i in fav_ids.values() if thumbs.get(f"smartghar://radio/{i}") != favicons.get(i)}
    run.record("browse thumbnails = favourite favicons", not bad,
               f"mismatch {bad}" if bad else f"{thumbs}")


async def step_announce(run: Run, label: str, keep_source: str | None) -> None:
    before = await run.dev_state()
    t0 = time.monotonic()
    status, text = await run.announce(f"Auvra test announcement {label}.")
    if status != 200:
        run.record(f"announce over {label}", False, f"HTTP {status} {text}")
        return
    on, st_on, t_on = await run.wait_dev(lambda s: s.get("announcing") is True, 10, "announcing")
    push = run.pushed_since(t0, lambda s: s.get("announcing") is True)
    ha_on = (await run.ha_state())["attributes"].get("announcing")
    off, st_off, t_off = await run.wait_dev(lambda s: s.get("announcing") is False, 40, "announce done")
    src_ok = keep_source is None or (st_on.get("source") == keep_source and st_off.get("source") == keep_source)
    resumed = keep_source is None or bool(st_off.get("playing")) == bool(before.get("playing"))
    run.record(f"WS push of announcing=true over {label}", push is not None,
               f"first pushed snapshot with announcing=true after {push and round(push, 2)}s "
               f"(device GET saw it after {t_on:.1f}s; HA attribute then {ha_on})")
    run.record(
        f"announce over {label}",
        on and off and src_ok and resumed,
        f"announcing after {t_on:.1f}s (push {push and round(push, 2)}s, HA attr {ha_on}); "
        f"done after {t_on + t_off:.1f}s; source {before.get('source')}->{st_on.get('source')}->"
        f"{st_off.get('source')}, playing {before.get('playing')}->{st_off.get('playing')}",
    )


async def step_play_favourite(run: Run, sid: int, name: str) -> None:
    t0 = time.monotonic()
    status, text = await run.ha_call("media_player", "play_media", {
        "entity_id": run.args.entity, "media_content_type": "channel",
        "media_content_id": f"smartghar://radio/{sid}",
    })
    ok, st, dt = await run.wait_dev(
        lambda s: s.get("source") == "radio" and (s.get("station") or {}).get("id") == sid
        and s.get("playing") and not s.get("stall"), 25, "radio playing")
    push = run.pushed_since(t0, lambda s: s.get("source") == "radio")
    run.record(f"play_media favourite {name}", status == 200 and ok,
               f"HTTP {status}; radio playing after {dt:.1f}s; WS push of source=radio after "
               f"{push and round(push, 2)}s; station {st.get('station')}")
    fields = {
        "active false": st.get("active") is False,
        'sender ""': st.get("sender") == "",
        "artist = station": st.get("artist") == name,
        "art_rev 0": st.get("art_rev") == 0,
        "position null": st.get("position_ms") is None and st.get("duration_ms") is None,
        "sources": st.get("sources") == ["airplay", "radio"],
        "announcing false": st.get("announcing") is False,
        "stream_title present": "stream_title" in st,
    }
    bad = [k for k, v in fields.items() if not v]
    run.record("radio state fields as built", not bad, f"failed {bad}" if bad else "all match")
    ok_ha, ha, dt_ha = await run.wait_ha(lambda s: s["state"] == "playing", 10)
    a = ha.get("attributes", {})
    run.record("HA shows the radio", ok_ha and a.get("source") == "Radio" and a.get("app_name") == "Radio"
               and a.get("media_channel") == name,
               f"state {ha.get('state')} after {dt_ha:.1f}s, source {a.get('source')}, app {a.get('app_name')}, "
               f"channel {a.get('media_channel')}, title {a.get('media_title')!r}")


async def step_next_previous(run: Run, fav_ids: dict[str, int]) -> None:
    if len(fav_ids) < 2:
        run.record("next/previous step favourites", None, "fewer than 2 favourites")
        return
    st0 = await run.dev_state()
    sid0 = (st0.get("station") or {}).get("id")
    await run.ha_call("media_player", "media_next_track", {"entity_id": run.args.entity})
    ok1, st1, _ = await run.wait_dev(
        lambda s: (s.get("station") or {}).get("id") not in (None, sid0), 15, "next station")
    await run.ha_call("media_player", "media_previous_track", {"entity_id": run.args.entity})
    ok2, st2, _ = await run.wait_dev(lambda s: (s.get("station") or {}).get("id") == sid0, 15, "back")
    run.record("next/previous step favourites on radio", ok1 and ok2,
               f"{sid0} -> {(st1.get('station') or {}).get('id')} -> {(st2.get('station') or {}).get('id')}")


async def step_station_logo(run: Run, fav_ids: dict[str, int]) -> None:
    """ui2: state.station.image_url (and so HA's entity_picture, proxied)
    exists only while a favourite with a logo plays."""
    favicons = {s["id"]: s.get("favicon") for s in await run.dev_stations()}
    if not any(favicons.get(i) for i in fav_ids.values()):
        run.record("station logo as entity_picture", None, "no test favourite has a favicon on the device")
        return
    for name, sid in fav_ids.items():
        await run.ha_call("media_player", "play_media", {
            "entity_id": run.args.entity, "media_content_type": "channel",
            "media_content_id": f"smartghar://radio/{sid}"})
        ok, st, _ = await run.wait_dev(
            lambda s: s.get("source") == "radio" and (s.get("station") or {}).get("id") == sid, 20, "station")
        want = bool(favicons.get(sid))
        img = (st.get("station") or {}).get("image_url")
        okh, ha, _ = await run.wait_ha(
            lambda s: s.get("attributes", {}).get("media_channel") == name
            and bool(s.get("attributes", {}).get("entity_picture")) == want, 10)
        pic = ha.get("attributes", {}).get("entity_picture")
        proxied = pic is None or pic.startswith("/api/media_player_proxy/")
        served = "n/a"
        if pic:
            async with run.http.get(run.args.ha + pic, headers=run.ha_headers) as r:
                body = await r.read()
                served = f"{r.status} {r.content_type} {len(body)} B"
                proxied = proxied and r.status == 200 and r.content_type.startswith("image/")
        run.record(f"station logo {'set' if want else 'unset'} for {name}",
                   ok and okh and bool(img) == want and proxied,
                   f"device image_url {img!r}, HA entity_picture {pic and pic.split('?')[0]!r}, proxy {served}")


async def step_select_source(run: Run) -> None:
    st0 = await run.dev_state()
    sid0 = (st0.get("station") or {}).get("id")
    t0 = time.monotonic()
    # rc7: radio stops; a connected sender gets MediaRemote play (source
    # airplay, playing), with none the source goes idle.
    expect = "airplay" if run.sender_connected else "idle"
    s1, _ = await run.ha_call("media_player", "select_source", {"entity_id": run.args.entity, "source": "AirPlay"})
    ok1, st1, dt1 = await run.wait_dev(
        lambda s: s.get("source") == expect and (expect == "idle" or s.get("playing")), 15, "left radio")
    push = run.pushed_since(t0, lambda s: s.get("source") == expect)
    run.record(f"select_source AirPlay leaves radio ({expect})", s1 == 200 and ok1,
               f"source {st1.get('source')} playing {st1.get('playing')} after {dt1:.1f}s, "
               f"WS push after {push and round(push, 2)}s")
    if expect == "airplay":
        await run.ha_call("media_player", "media_pause", {"entity_id": run.args.entity})
    s2, _ = await run.ha_call("media_player", "select_source", {"entity_id": run.args.entity, "source": "Radio"})
    ok2, st2, dt2 = await run.wait_dev(
        lambda s: s.get("source") == "radio" and (s.get("station") or {}).get("id") == sid0, 20, "radio back")
    run.record("select_source Radio resumes the last favourite", s2 == 200 and ok2,
               f"station {st2.get('station')} after {dt2:.1f}s (expected id {sid0})")


async def step_play_url(run: Run, stations: list[dict[str, Any]]) -> None:
    st = run.url_station
    if st is None:
        run.record("play_media plain URL", None, "no test station URL outside the favourites")
        return
    status, text = await run.ha_call("media_player", "play_media", {
        "entity_id": run.args.entity, "media_content_type": "music",
        "media_content_id": st["url"], "extra": {"title": "E2E URL", "volume": VOLUME_MAX},
    })
    ok, dst, dt = await run.wait_dev(
        lambda s: s.get("source") == "url" and s.get("playing") and not s.get("stall"), 25, "url playing")
    run.record("play_media plain URL", status == 200 and ok,
               f"HTTP {status} {text if status != 200 else ''}; source {dst.get('source')} after {dt:.1f}s; "
               f"title {dst.get('title')!r}, station {dst.get('station')}")
    # The stream may re-buffer right after starting; give HA's state the
    # same chance the device check had.
    _, ha, _ = await run.wait_ha(
        lambda s: s["attributes"].get("app_name") == "URL" and s["state"] == "playing", 15)
    run.record("HA shows the URL", ha["state"] == "playing" and ha["attributes"].get("app_name") == "URL",
               f"state {ha['state']}, title {ha['attributes'].get('media_title')!r}")
    s_next, _ = await run.ha_call("media_player", "media_next_track", {"entity_id": run.args.entity})
    await asyncio.sleep(2)
    after = await run.dev_state()
    run.record("next on a URL is refused quietly", s_next == 200 and after.get("source") == "url",
               f"HTTP {s_next}, source still {after.get('source')}")


async def step_streams_toggle(run: Run) -> None:
    """Streams off drops radio/url_play/announce: rc7 pushes hub.capabilities,
    so HA's features go at once (the entry then reloads); on brings them back."""
    sw = _eid("switch", "radio_and_streams")
    t0 = time.monotonic()
    s_off, _ = await run.ha_call("switch", "turn_off", {"entity_id": sw})
    push = None
    for _ in range(40):
        push = run.pushed_since(t0, lambda s: True)
        if push is not None:
            break
        await asyncio.sleep(0.1)
    ok, ha, dt = await run.wait_ha(
        lambda s: s.get("state") not in ("unavailable", None)
        and not int(s.get("attributes", {}).get("supported_features", 0)) & F_PLAY_MEDIA, 10)
    info = await run.dev_get("/api/v1/info")
    dst = await run.dev_state()
    run.record("streams off drops the features at once",
               s_off == 200 and ok and "radio" not in (info.get("capabilities") or [])
               and dst.get("source") not in ("radio", "url"),
               f"HA features without PLAY_MEDIA after {dt:.1f}s (first push {push and round(push, 2)}s); "
               f"device caps {info.get('capabilities')}, source {dst.get('source')}")
    await asyncio.sleep(3)  # let the entry reload settle
    s_on, _ = await run.ha_call("switch", "turn_on", {"entity_id": sw})
    ok2, ha2, dt2 = await run.wait_ha(
        lambda s: int(s.get("attributes", {}).get("supported_features", 0)) & F_PLAY_MEDIA, 15)
    run.record("streams on brings them back", s_on == 200 and ok2, f"after {dt2:.1f}s")
    await asyncio.sleep(3)


async def step_announce_over_airplay(run: Run) -> None:
    st = await run.dev_state()
    if not st.get("active"):
        run.record("announce over AirPlay", None,
                   "no AirPlay sender connected: start Music.app to Auvra Bedroom and rerun with --only-airplay")
        return
    await run.ha_call("media_player", "media_play", {"entity_id": run.args.entity})
    ok, st, _ = await run.wait_dev(lambda s: s.get("source") == "airplay" and s.get("playing"), 15, "airplay")
    if not ok:
        run.record("announce over AirPlay", False, f"AirPlay did not play: source {st.get('source')}")
        return
    await step_announce(run, "AirPlay", "airplay")
    await run.ha_call("media_player", "media_pause", {"entity_id": run.args.entity})


async def step_stop(run: Run) -> None:
    await run.ha_call("media_player", "media_stop", {"entity_id": run.args.entity})
    ok, st, dt = await run.wait_dev(lambda s: s.get("source") in ("idle", "airplay") and not
                                    (s.get("source") == "airplay" and s.get("playing")), 15, "stopped")
    run.record("media_stop ends the stream", ok, f"source {st.get('source')} after {dt:.1f}s")


async def restore(run: Run) -> None:
    await run.ha_call("media_player", "media_stop", {"entity_id": run.args.entity})
    await run.set_volume_8()
    for sid in run.added_station_ids:
        status, body = await run.dev_delete_station(sid)
        run.record(f"remove test favourite {sid}", status == 200 and body.get("ok"), f"HTTP {status} {body}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ha", default="http://localhost:8123")
    ap.add_argument("--token-file", default=DEF_TOKEN)
    ap.add_argument("--device", default="192.168.0.130")
    ap.add_argument("--entity", default="media_player.auvra_bedroom")
    ap.add_argument("--tts", default="tts.google_translate_en_com")
    ap.add_argument("--stations", default=DEF_STATIONS)
    ap.add_argument("--only-airplay", action="store_true", help="run only the announce-over-AirPlay step")
    args = ap.parse_args()
    args.token = Path(args.token_file).read_text().strip()
    stations = json.loads(Path(args.stations).read_text())["stations"]

    async with aiohttp.ClientSession() as http:
        run = Run(args, http)
        stop = asyncio.Event()
        watcher = asyncio.create_task(run.watch_device_ws(stop))
        try:
            if not await step_preflight(run):
                print("preflight failed; not continuing", flush=True)
                return 1
            if args.only_airplay:
                await step_announce_over_airplay(run)
            else:
                await step_entities(run)
                fav_ids = await step_favourites(run, stations)
                await step_browse(run, fav_ids)
                await step_stop(run)
                await step_announce(run, "idle", None)
                if fav_ids:
                    name, sid = next(iter(fav_ids.items()))
                    await step_play_favourite(run, sid, name)
                    await step_announce(run, "radio", "radio")
                    await step_next_previous(run, fav_ids)
                    await step_station_logo(run, fav_ids)
                    await step_select_source(run)
                await step_play_url(run, stations)
                await step_streams_toggle(run)
                await step_stop(run)
                await step_announce_over_airplay(run)
        finally:
            await restore(run)
            await asyncio.sleep(1)
            stop.set()
            await watcher
        run.record("volume never above 8", not run.volume_violations,
                   "; ".join(run.volume_violations) or f"{len(run.snapshots)} snapshots checked")
        fails = [r for r in run.results if r.status == "FAIL"]
        print(f"\n{sum(r.status == 'PASS' for r in run.results)} pass, {len(fails)} fail, "
              f"{sum(r.status == 'SKIP' for r in run.results)} skip", flush=True)
        return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
