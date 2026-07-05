"""Serial (USB-CDC) coordinator link — TankSync hub proto v1.

The hub in coordinator mode is a Zigbee-stick-style local device: NDJSON over
a serial port (see the hub repo's docs/coordinator-serial-protocol.md). This
module is deliberately Home-Assistant-free and transport-injectable:

  * In HA, open with `SerialCoordinatorLink.open(port)` (pyserial-asyncio).
  * In tests, construct with any (StreamReader, StreamWriter) pair — a PTY,
    a socket, or in-memory pipes — no hardware, no pyserial.

Push model: telemetry/node events invoke callbacks; commands are futures
matched on the echoed `id`. Reconnect is the owner's job (HA coordinator
re-opens on disconnect) — this class handles exactly one session.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

PROTO_SUPPORTED = 1
LINE_MAX = 512
HELLO_TIMEOUT_S = 5.0
CMD_TIMEOUT_S = 5.0


class SerialLinkError(Exception):
    """Base error for the coordinator serial link."""


class SerialProtocolMismatch(SerialLinkError):
    """Hub speaks a proto major we don't support."""


class SerialCommandError(SerialLinkError):
    """Hub answered a command with an error message."""

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


@dataclass
class HubInfo:
    proto: int
    fw: str
    device_id: str
    model: str
    transports: list[str]
    max_nodes: int


@dataclass
class Node:
    node_id: int
    device_type: str
    name: str
    transport: str = ""
    fw: str = ""
    online: bool = False
    last_seen_s: int = -1
    rssi: int = 0
    battery_pct: int = 0
    power: str = "battery"
    # last telemetry: measure -> {"unit": str, "value": float}
    sensors: dict[str, dict[str, Any]] = field(default_factory=dict)


class SerialCoordinatorLink:
    """One NDJSON session with a hub over a byte-stream pair."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self.info: HubInfo | None = None
        self.nodes: dict[int, Node] = {}
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._hello_evt = asyncio.Event()
        self._nodes_done_evt = asyncio.Event()
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        # subscriber callbacks
        self.on_telemetry: Callable[[Node], None] | None = None
        self.on_node: Callable[[Node], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    async def open(cls, port: str, baudrate: int = 115200) -> "SerialCoordinatorLink":
        """Open a real serial port (HA path; requires pyserial-asyncio)."""
        import serial_asyncio  # lazy: tests don't need it

        reader, writer = await serial_asyncio.open_serial_connection(
            url=port, baudrate=baudrate)
        return cls(reader, writer)

    async def start(self) -> HubInfo:
        """Start the read loop and complete the hello handshake."""
        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop())
        # DTR on open makes a real hub emit hello unsolicited; a PTY (tests/sim)
        # has no DTR, so nudge with get_info — harmless duplication on hardware.
        await self._send({"type": "get_info", "id": self._new_id()})
        try:
            await asyncio.wait_for(self._hello_evt.wait(), HELLO_TIMEOUT_S)
        except asyncio.TimeoutError as err:
            raise SerialLinkError("no hello from hub (is this a coordinator port?)") from err
        assert self.info is not None
        if self.info.proto != PROTO_SUPPORTED:
            raise SerialProtocolMismatch(
                f"hub proto {self.info.proto}, integration supports {PROTO_SUPPORTED}")
        return self.info

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        try:
            self._writer.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass

    # ── commands ──────────────────────────────────────────────────────────────

    async def refresh_nodes(self) -> dict[int, Node]:
        """Snapshot the registry (get_nodes)."""
        self._nodes_done_evt.clear()
        await self._command("get_nodes")
        await asyncio.wait_for(self._nodes_done_evt.wait(), CMD_TIMEOUT_S)
        return self.nodes

    async def ping(self) -> None:
        await self._command("ping")

    async def set_pump(self, node_id: int, on: bool) -> None:
        await self._command("pump", node_id=node_id, on=on)

    async def rename(self, node_id: int, name: str) -> None:
        await self._command("rename", node_id=node_id, name=name)

    # ── internals ─────────────────────────────────────────────────────────────

    def _new_id(self) -> int:
        self._next_id = (self._next_id % 0xFFFFFFFF) + 1
        return self._next_id

    async def _send(self, obj: dict) -> None:
        self._writer.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        await self._writer.drain()

    async def _command(self, cmd: str, **args: Any) -> dict:
        cid = self._new_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        try:
            await self._send({"type": cmd, "id": cid, **args})
            return await asyncio.wait_for(fut, CMD_TIMEOUT_S)
        finally:
            self._pending.pop(cid, None)

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                raw = await self._reader.readline()
                if not raw:
                    break  # EOF — port gone
                if len(raw) > LINE_MAX:
                    continue  # oversize/garbage; resync at next newline
                txt = raw.decode("utf8", "replace").strip()
                if not txt:
                    continue
                try:
                    msg = json.loads(txt)
                except json.JSONDecodeError:
                    _LOGGER.debug("discarding unparseable line: %r", txt[:80])
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — reader dies = session over
            _LOGGER.exception("serial read loop failed")
        finally:
            if not self._closed and self.on_disconnect:
                self.on_disconnect()

    def _dispatch(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "hello":
            self.info = HubInfo(
                proto=int(msg.get("proto", 0)),
                fw=str(msg.get("fw", "")),
                device_id=str(msg.get("device_id", "")),
                model=str(msg.get("model", "")),
                transports=list(msg.get("transports", [])),
                max_nodes=int(msg.get("max_nodes", 0)),
            )
            self._hello_evt.set()
        elif mtype == "node":
            node = self._upsert_node(msg)
            node.name = str(msg.get("name", node.name))
            node.transport = str(msg.get("transport", node.transport))
            node.fw = str(msg.get("fw", node.fw))
            node.online = bool(msg.get("online", node.online))
            node.last_seen_s = int(msg.get("last_seen_s", node.last_seen_s))
            node.power = str(msg.get("power", node.power))
            if self.on_node:
                self.on_node(node)
        elif mtype == "nodes_done":
            self._nodes_done_evt.set()
        elif mtype == "telemetry":
            node = self._upsert_node(msg)
            for s in msg.get("sensors", []):
                measure = s.get("measure")
                if measure:
                    node.sensors[measure] = {"unit": s.get("unit", ""),
                                             "value": s.get("value")}
            # Health flags ride as top-level telemetry fields; fold them into
            # the sensors map as pseudo-measures so consumers have one lookup.
            for extra in ("suspect", "sensor_error"):
                if extra in msg:
                    node.sensors[extra] = {"unit": "bool", "value": bool(msg[extra])}
            node.online = True
            node.last_seen_s = 0
            if self.on_telemetry:
                self.on_telemetry(node)
        elif mtype in ("ack", "error"):
            fut = self._pending.get(int(msg.get("id", 0)))
            if fut and not fut.done():
                if mtype == "ack":
                    fut.set_result(msg)
                else:
                    fut.set_exception(SerialCommandError(
                        str(msg.get("code", "internal")), str(msg.get("msg", ""))))
        elif mtype == "event":
            _LOGGER.debug("hub event: %s", msg)
        # unknown types are ignored by spec (forward compatibility)

    def _upsert_node(self, msg: dict) -> Node:
        nid = int(msg.get("node_id", 0))
        node = self.nodes.get(nid)
        if node is None:
            node = Node(node_id=nid,
                        device_type=str(msg.get("device_type", "unknown")),
                        name=f"Node {nid}")
            self.nodes[nid] = node
        else:
            node.device_type = str(msg.get("device_type", node.device_type))
        if "rssi" in msg:
            node.rssi = int(msg["rssi"])
        if "battery_pct" in msg:
            node.battery_pct = int(msg["battery_pct"])
        return node
