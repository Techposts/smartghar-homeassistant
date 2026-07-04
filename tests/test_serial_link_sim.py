#!/usr/bin/env python3
"""End-to-end test: SerialCoordinatorLink ↔ the TankSync hub simulator.

Runs WITHOUT hardware and WITHOUT pyserial: the simulator exposes a PTY and
we wrap its fd into asyncio streams directly. Exercises the full proto-v1
session: hello handshake, get_nodes, telemetry push, pump command round-trip
(including the state echo), rename, and error handling.

Usage:
  python3 tests/test_serial_link_sim.py /path/to/coord-hub-sim.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "smartghar"))
from serial_link import SerialCoordinatorLink, SerialCommandError  # noqa: E402

SIM = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Projects/TankSync/cloud/scripts/coord-hub-sim.py")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


async def open_pty_streams(path: str):
    """Wrap a tty path into (StreamReader, StreamWriter) without pyserial."""
    import termios
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)   # raw-ish: no echo, no line buffering
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader),
                                 os.fdopen(fd, "rb", buffering=0))
    wfd = os.dup(fd)
    wtransport, wprotocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, os.fdopen(wfd, "wb", buffering=0))
    writer = asyncio.StreamWriter(wtransport, wprotocol, reader, loop)
    return reader, writer


async def main() -> int:
    proc = subprocess.Popen([sys.executable, SIM], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    try:
        pty_path = proc.stdout.readline().strip()
        print(f"[sim] {pty_path}")
        reader, writer = await open_pty_streams(pty_path)
        link = SerialCoordinatorLink(reader, writer)

        telemetry_seen: list[int] = []
        link.on_telemetry = lambda n: telemetry_seen.append(n.node_id)

        # 1. handshake
        info = await link.start()
        check("hello handshake", info.proto == 1 and info.device_id == "aabbccddeeff",
              f"info={info}")

        # 2. registry snapshot
        nodes = await link.refresh_nodes()
        check("get_nodes count", len(nodes) == 2, f"nodes={list(nodes)}")
        check("tank parsed", nodes.get(3) is not None and nodes[3].device_type == "tank"
              and nodes[3].name == "Sim Rooftop Tank")
        check("switch parsed", nodes.get(5) is not None and nodes[5].device_type == "switch"
              and nodes[5].power == "mains")

        # 3. ping
        await link.ping()
        check("ping ack", True)

        # 4. telemetry push (sim emits every 5s)
        for _ in range(80):
            if 3 in telemetry_seen and 5 in telemetry_seen:
                break
            await asyncio.sleep(0.1)
        check("telemetry push both nodes", 3 in telemetry_seen and 5 in telemetry_seen,
              f"seen={telemetry_seen}")
        check("tank level sensor", "level" in nodes[3].sensors
              and nodes[3].sensors["level"]["unit"] == "%")

        # 5. pump command → ack + state echo
        before = len(telemetry_seen)
        await link.set_pump(5, True)
        for _ in range(30):
            if len(telemetry_seen) > before and nodes[5].sensors.get("relay", {}).get("value") == 1:
                break
            await asyncio.sleep(0.1)
        check("pump on + echo", nodes[5].sensors.get("relay", {}).get("value") == 1,
              f"relay={nodes[5].sensors.get('relay')}")
        check("pump current live", (nodes[5].sensors.get("current", {}).get("value") or 0) > 1)

        # 6. rename round-trip
        await link.rename(3, "Renamed Tank")
        await asyncio.sleep(0.3)
        check("rename applied", nodes[3].name == "Renamed Tank", f"name={nodes[3].name!r}")

        # 7. error path
        try:
            await link.set_pump(999, True)
            check("error surfaced", False, "no exception")
        except SerialCommandError as e:
            check("error surfaced", e.code == "not_found", f"code={e.code}")

        await link.close()
    finally:
        proc.terminate()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
