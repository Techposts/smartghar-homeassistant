"""Fake Auvra (schema 1.2 as built in 0.10.0-rc7, station logos from ui2) for a dry run of auvra_announce_radio.py.

The integration talks to port 80, which macOS lets a normal user bind only on
0.0.0.0. Dry run: a copy of the rig config whose smartghar entry points at
127.0.0.1, no discovery integrations, HA on 127.0.0.1:8124 (see docs/auvra-e2e.md).

python fake_auvra.py 0.0.0.0 80 [nosender]   # GET /fake/log lists the commands received
"""
import asyncio
import copy
import json
import sys

from aiohttp import WSMsgType, web

INFO = {
    "schema_version": "1.2", "manufacturer": "SmartGhar", "product": "auvra", "model": "Auvra",
    "fw_version": "0.10.0-fake", "hub_id": "auvra_b81f3fd80848", "hub_name": "Auvra Bedroom",
    "topology": "standalone", "uptime_s": 1, "wifi_rssi": -20,
    "ota": {"current": "0.10.0-fake", "available": False, "latest": "", "channel": "stable"},
    "stream": {"ws_path": "/api/v1/stream"},
    "capabilities": ["media_player", "airplay2", "eq", "channel", "radio", "url_play", "announce"],
}
EQ = {"preset": "flat", "presets": ["flat", "bass", "vocal", "bookshelf", "soundbar", "night"],
      "bass_db": 0, "treble_db": 0, "loudness": False, "range_db": [-12, 12], "bands": [0] * 5}
S = {
    "power": "on", "active": True, "playing": False, "sender": "Mac Studio", "volume": 8, "muted": False,
    "title": "Thunderbird", "artist": "Kevin MacLeod", "album": "", "art_rev": 3, "position_ms": 1000,
    "duration_ms": 360000, "stall": False, "remote": True, "channel": "stereo", "eq": copy.deepcopy(EQ),
    "source": "airplay", "sources": ["airplay", "radio"], "station": None, "stream_title": "",
    "announcing": False,
}
CFG = {"max_vol": 85, "night": True, "sync_trim_ms": 0, "channel": "stereo", "eq": copy.deepcopy(EQ),
       "streams": True, "announce_min_volume": 8, "announce_duck_db": 15}
CAPS_ON = ["media_player", "airplay2", "eq", "channel", "radio", "url_play", "announce"]
CAPS_OFF = ["media_player", "airplay2", "eq", "channel"]
STATIONS: list[dict] = []
NEXT_ID = [1]
LAST_STATION = [None]
SAVED_AIRPLAY = {}
CLIENTS: set = set()
LOG: list[str] = []


def dev():
    return {"devices": [{"id": 1, "kind": "speaker", "name": "Auvra Bedroom", "state": S, "config": CFG}]}


def caps():
    return list(CAPS_ON if CFG["streams"] else CAPS_OFF)


async def push():
    msg = json.dumps({"kind": "snapshot", "hub": {"uptime_s": 1, "wifi_rssi": -20, "capabilities": caps()}, **dev()})
    for ws in list(CLIENTS):
        try:
            await ws.send_str(msg)
        except Exception:
            CLIENTS.discard(ws)


def station_ref(st):
    ref = {"id": st["id"], "name": st["name"]}
    if st.get("favicon"):
        ref["image_url"] = st["favicon"]  # ui2: only while a favourite with a logo plays
    return ref


def set_stream(source, station=None, title="", url_title=""):
    if S["source"] == "airplay" and S["active"]:
        SAVED_AIRPLAY.update({k: S[k] for k in ("sender", "title", "artist", "art_rev", "position_ms", "duration_ms")})
    S.update(source=source, active=False, sender="", art_rev=0, position_ms=None, duration_ms=None,
             playing=True, stall=False, album="", station=station,
             stream_title=title, title=title or url_title, artist=(station or {}).get("name", ""))


def go_idle(take_back=False):
    """Stop radio/url. rc7: with a sender connected, select_source airplay
    sends it MediaRemote play (source airplay, playing); with none, idle."""
    S.update(source="idle", playing=False, station=None, stream_title="", title="", artist="")
    if SAVED_AIRPLAY:
        S.update(source="airplay", active=True, playing=take_back, **SAVED_AIRPLAY)


async def announce_later():
    await asyncio.sleep(0.4)
    S["announcing"] = True
    await push()
    await asyncio.sleep(3)
    S["announcing"] = False
    await push()


async def info(_):
    return web.json_response({**INFO, "capabilities": caps()})


async def devices(_):
    return web.json_response(dev())


async def notfound(_):
    return web.Response(status=404, text="Not found")


async def led(_):
    return web.json_response({"brightness": 100})


async def put_device(req):
    body = await req.json()
    rejected = []
    if isinstance(body.get("config"), dict):
        body = body["config"]
    for k, v in body.items():
        if k == "volume" and isinstance(v, int) and 0 <= v <= 100:
            S["volume"] = v
        elif k == "muted":
            S["muted"] = bool(v)
        elif k == "streams" and (isinstance(v, bool) or v in (0, 1)):
            CFG["streams"] = bool(v)
            S["sources"] = ["airplay", "radio"] if CFG["streams"] else ["airplay"]
            if not CFG["streams"] and S["source"] in ("radio", "url"):
                go_idle()
        elif k == "announce_min_volume" and type(v) is int and 0 <= v <= 100:
            CFG[k] = v
        elif k == "announce_duck_db" and type(v) is int and 0 <= v <= 40:
            CFG[k] = v
        else:
            rejected.append(k)
    await push()
    return web.json_response({"ok": not rejected, "rejected": rejected})


async def command(req):
    b = await req.json()
    c = b.get("cmd")
    LOG.append(json.dumps(b))
    ok, msg = True, None
    if not CFG["streams"] and (c in ("play_url", "radio_play", "radio_next", "radio_prev")
                               or (c == "select_source" and b.get("source") == "radio")):
        await push()
        return web.json_response({"ok": False, "msg": "Radio is switched off"})
    if c == "play_url":
        url = b.get("url", "")
        if not url.startswith(("http://", "https://")) or len(url) > 1024 or " " in url:
            ok, msg = False, "bad url"
        elif b.get("announce"):
            asyncio.create_task(announce_later())
        else:
            if isinstance(b.get("volume"), int):
                S["volume"] = b["volume"]  # rc7: persistent
            set_stream("url", None, "", b.get("title", ""))
    elif c == "radio_play":
        st = next((s for s in STATIONS if str(s["id"]) == str(b.get("station_id"))), None)
        if not st:
            ok, msg = False, "Station not found"
        else:
            LAST_STATION[0] = st
            set_stream("radio", station_ref(st), "Some Song")
    elif c == "select_source":
        if b.get("source") == "radio":
            if LAST_STATION[0] is None:
                ok, msg = False, "No station played yet"
            else:
                st = LAST_STATION[0]
                set_stream("radio", station_ref(st), "Some Song")
        elif b.get("source") == "airplay":
            go_idle(take_back=True)
        else:
            ok, msg = False, "Unknown source"
    elif c in ("next", "previous", "radio_next", "radio_prev"):
        if S["source"] == "url":
            ok, msg = False, "Next/previous need radio"
        elif S["source"] == "radio" and STATIONS:
            i = [s["id"] for s in STATIONS].index(S["station"]["id"])
            i = (i + (1 if c in ("next", "radio_next") else -1)) % len(STATIONS)
            st = STATIONS[i]
            LAST_STATION[0] = st
            set_stream("radio", station_ref(st), "Some Song")
    elif c == "stop":
        if S["announcing"]:
            S["announcing"] = False
        elif S["source"] in ("radio", "url"):
            go_idle()
        else:
            S["playing"] = False
    elif c in ("play", "play_pause"):
        S["playing"] = True
    elif c == "pause":
        S["playing"] = False
    else:
        ok, msg = False, "Unknown command"
    await push()
    return web.json_response({"ok": ok, **({"msg": msg} if msg else {})})


async def stations_get(_):
    return web.json_response(STATIONS)


async def stations_post(req):
    b = await req.json()
    for s in STATIONS:
        if s["url"] == b.get("url"):
            return web.json_response({"ok": True, "id": s["id"]})
    if not b.get("name") or not str(b.get("url", "")).startswith("http"):
        return web.json_response({"ok": False, "msg": "name and url needed"}, status=400)
    st = {"id": NEXT_ID[0], "name": b["name"], "url": b["url"]}
    for k in ("codec", "bitrate"):
        if k in b:
            st[k] = b[k]
    fav = b.get("favicon")
    if isinstance(fav, str) and fav.startswith(("http://", "https://")) and len(fav) <= 512:
        st["favicon"] = fav  # ui2: an invalid favicon is dropped, the station still added
    NEXT_ID[0] += 1
    STATIONS.append(st)
    return web.json_response({"ok": True, "id": st["id"]})


async def stations_delete(req):
    sid = req.query.get("id")
    before = len(STATIONS)
    STATIONS[:] = [s for s in STATIONS if str(s["id"]) != str(sid)]
    if len(STATIONS) == before:
        return web.json_response({"ok": False, "msg": "No such station"}, status=400)
    return web.json_response({"ok": True})


async def stream(req):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    await ws.send_str(json.dumps({"kind": "hello", "schema_version": "1.2", "hub_id": INFO["hub_id"]}))
    await ws.send_str(json.dumps({"kind": "snapshot", "hub": {}, **dev()}))
    CLIENTS.add(ws)
    async for m in ws:
        if m.type == WSMsgType.ERROR:
            break
    CLIENTS.discard(ws)
    return ws


async def cmdlog(_):
    return web.json_response(LOG)


app = web.Application()
app.add_routes([
    web.get("/api/v1/info", info), web.get("/api/v1/devices", devices),
    web.get("/api/v1/hub/led", led), web.get("/api/v1/hub/buzzer", notfound), web.get("/api/switches", notfound),
    web.put("/api/v1/devices/1", put_device), web.post("/api/v1/devices/1/command", command),
    web.get("/api/v1/radio/stations", stations_get), web.post("/api/v1/radio/stations", stations_post),
    web.delete("/api/v1/radio/stations", stations_delete), web.get("/api/v1/stream", stream),
    web.get("/fake/log", cmdlog),
])
if len(sys.argv) > 3 and sys.argv[3] == "nosender":   # start with no AirPlay sender connected
    S.update(active=False, sender="", title="", artist="", art_rev=0, position_ms=None, duration_ms=None,
             source="idle", remote=False)
web.run_app(app, host=sys.argv[1], port=int(sys.argv[2]), print=lambda *_: print("fake auvra up", flush=True))
