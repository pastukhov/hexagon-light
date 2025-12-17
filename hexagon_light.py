#!/usr/bin/env python3
"""
Hexagon Light BLE controller (MeRGBW / Fivemi).

Programmatic usage:
  from hexagon_light import HexagonLight
  lamp = HexagonLight("FF:FF:11:52:AB:BD")
  lamp.connect()
  lamp.turn_on()
  lamp.set_rgb(255, 100, 50)
  lamp.set_brightness(80)
  lamp.turn_off()
  lamp.disconnect()

CLI usage:
  python3 hexagon_light.py --mac FF:FF:11:52:AB:BD on
  python3 hexagon_light.py --mac FF:FF:11:52:AB:BD rgb 255 100 50
  python3 hexagon_light.py --mac FF:FF:11:52:AB:BD brightness 80
  python3 hexagon_light.py --mac FF:FF:11:52:AB:BD --wait 2 set --power on --rgb 255 100 50 --brightness 80

Protocol (from MeRGBW app):
  - Write to service 0000fff0-0000-1000-8000-00805f9b34fb
  - Characteristic 0000fff3-0000-1000-8000-00805f9b34fb
  - Frame:
      [0] 0x55 (header)
      [1] cmd
      [2] seq (0xFF for non-sequenced commands)
      [3] length (total frame length)
      [4..n-2] payload
      [n-1] checksum = (0xFF - (sum(frame[0..n-2]) & 0xFF)) & 0xFF

Known commands:
  - 0x00: request sync/status (no payload; device responds via notify on 0xFFF4)
  - 0x01: power (payload: 1 byte, 0x00 off / 0x01 on)
  - 0x03: HSV color (payload: hue_u16_be + sat_u16_be where sat = saturation * 1000)
  - 0x05: brightness (payload: value_u16_be; app uses value = (pct + 5) * 10)
  - 0x06: scene/effect (payload: scene_index_u16_be)
  - 0x0f: scene speed (payload: 1 byte)
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
import threading
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient


DEFAULT_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
DEFAULT_WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
DEFAULT_NOTIFY_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"

SCENES_TG609 = {
    "symphony": 2,
    "energy": 3,
    "jump": 4,
    "vitality": 7,
    "accumulation": 16,
    "chase": 23,
    "space-time": 45,
    "space_time": 45,
    "ephemeral": 35,
    "flow": 55,
    "forest": 13,
    "neon_lights": 48,
    "neon-lights": 48,
    "green_jade": 71,
    "green-jade": 71,
    "running": 91,
    "pink_light": 109,
    "pink-light": 109,
    "alarm": 113,
    "aurora": 59,
    "rainbow": 26,
    "melody": 32,
}


class HexagonLightError(RuntimeError):
    pass


@dataclass(frozen=True)
class _WriteConfig:
    service_uuid: str
    char_uuid: str
    response: bool


@dataclass(frozen=True)
class HexagonState:
    is_on: Optional[bool] = None
    brightness_percent: Optional[int] = None
    raw: Optional[bytes] = None


def _clamp_int(value: int, lo: int, hi: int) -> int:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _checksum_ff(sum_without_checksum: int) -> int:
    return (0xFF - (sum_without_checksum & 0xFF)) & 0xFF


def _build_command(cmd: int, payload: Optional[bytes]) -> bytes:
    if payload is None:
        payload = b""
    cmd = cmd & 0xFF
    seq = 0xFF
    length = 5 + len(payload)
    if length > 0xFF:
        raise ValueError(f"Command too long: {length} bytes")
    frame = bytearray(length)
    frame[0] = 0x55
    frame[1] = cmd
    frame[2] = seq
    frame[3] = length & 0xFF
    frame[4 : 4 + len(payload)] = payload
    sum_ = sum(frame[:-1])
    frame[-1] = _checksum_ff(sum_)
    return bytes(frame)


def _u16_be(value: int) -> bytes:
    value = value & 0xFFFF
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def _rgb_to_hue_sat_payload(r: int, g: int, b: int) -> bytes:
    r = _clamp_int(r, 0, 255)
    g = _clamp_int(g, 0, 255)
    b = _clamp_int(b, 0, 255)
    h, s, _v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue_deg = int(h * 360.0) % 360
    sat_1000 = _clamp_int(int(s * 1000.0), 0, 1000)
    return _u16_be(hue_deg) + _u16_be(sat_1000)


class HexagonLight:
    def __init__(
        self,
        address: str,
        *,
        service_uuid: str = DEFAULT_SERVICE_UUID,
        write_uuid: str = DEFAULT_WRITE_UUID,
        notify_uuid: str = DEFAULT_NOTIFY_UUID,
        timeout: float = 15.0,
        connect_retries: int = 5,
        retry_delay_s: float = 0.7,
    ) -> None:
        self.address = address
        self._service_uuid = service_uuid
        self._write_uuid = write_uuid
        self._notify_uuid = notify_uuid
        self._timeout = timeout
        self._connect_retries = connect_retries
        self._retry_delay_s = retry_delay_s

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[BleakClient] = None
        self._write: Optional[_WriteConfig] = None
        self._closing = False
        self._state_lock = threading.Lock()
        self._notify_event = threading.Event()
        self._last_notify: Optional[bytes] = None

    def connect(self) -> None:
        self._start_loop_thread()
        connect_timeout = (
            (self._timeout * self._connect_retries)
            + (
                self._retry_delay_s
                * (self._connect_retries * (self._connect_retries + 1) / 2.0)
            )
            + 10.0
        )
        self._run(self._connect_async(), timeout=connect_timeout)

    def disconnect(self) -> None:
        with self._state_lock:
            self._closing = True
        try:
            self._run(self._disconnect_async())
        finally:
            self._stop_loop_thread()

    def turn_on(self) -> None:
        self._run(self._send_simple_power(True))

    def turn_off(self) -> None:
        self._run(self._send_simple_power(False))

    def set_brightness(self, percent: int) -> None:
        percent = _clamp_int(int(percent), 0, 100)
        self._run(self._set_brightness_async(percent))

    def set_rgb(self, r: int, g: int, b: int) -> None:
        self._run(self._set_rgb_async(int(r), int(g), int(b)))

    def set_scene(self, scene: int, *, speed: Optional[int] = None) -> None:
        self._run(self._set_scene_async(int(scene), speed=speed))

    def set_scene_by_name(self, name: str, *, speed: Optional[int] = None) -> None:
        key = name.strip().lower().replace(" ", "_")
        scene = SCENES_TG609.get(key)
        if scene is None:
            key2 = name.strip().lower().replace("_", "-")
            scene = SCENES_TG609.get(key2)
        if scene is None:
            raise HexagonLightError(f"Unknown scene name: {name!r}")
        self.set_scene(scene, speed=speed)

    def get_state(
        self, *, wait_s: float = 2.0, request_sync: bool = True
    ) -> HexagonState:
        """
        Best-effort state read via notifications on 0xFFF4.

        Many devices only emit notifications after changes; if nothing arrives within
        wait_s, returns unknown state (None fields).
        """
        self._notify_event.clear()
        try:
            self._run(self._ensure_connected())
            if request_sync:
                self._run(self._request_sync_async())
        except Exception:
            return HexagonState()

        if not self._notify_event.wait(timeout=max(0.0, float(wait_s))):
            with self._state_lock:
                raw = self._last_notify
            return HexagonState(raw=raw)

        with self._state_lock:
            raw = self._last_notify
        return self._parse_state(raw)

    def _start_loop_thread(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._closing = False

        ready = threading.Event()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._state_lock:
                self._loop = loop
            ready.set()
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

        thread = threading.Thread(target=_runner, name="hexagon-light-ble", daemon=True)
        self._thread = thread
        thread.start()
        if not ready.wait(timeout=2.0):
            raise HexagonLightError("Failed to start BLE event loop thread")

    def _stop_loop_thread(self) -> None:
        with self._state_lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None

        if loop is None or thread is None:
            return

        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)

    def _run(self, coro, *, timeout: Optional[float] = None) -> None:
        with self._state_lock:
            loop = self._loop
        if loop is None:
            raise HexagonLightError("Not connected; call connect() first")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        if timeout is None:
            timeout = self._timeout + 5.0
        try:
            fut.result(timeout=timeout)
        except TimeoutError as e:
            raise HexagonLightError(f"Operation timed out after {timeout:.1f}s") from e

    def _on_disconnect(self, _client: BleakClient) -> None:
        with self._state_lock:
            self._client = None
            self._write = None
            self._last_notify = None
        self._notify_event.clear()

    async def _disconnect_async(self) -> None:
        client = None
        with self._state_lock:
            client = self._client
            self._client = None
            self._write = None
            self._last_notify = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _connect_async(self) -> None:
        last_err: Optional[BaseException] = None
        for attempt in range(1, self._connect_retries + 1):
            with self._state_lock:
                if self._closing:
                    raise HexagonLightError("Connect aborted (closing)")
                existing = self._client
            if existing is not None and existing.is_connected:
                return

            try:
                client = BleakClient(
                    self.address,
                    timeout=self._timeout,
                    disconnected_callback=self._on_disconnect,
                )
                await client.connect()
                svcs = getattr(client, "services", None)
                if svcs is None or getattr(svcs, "services", None) in (None, [], {}):
                    backend = getattr(client, "_backend", None)
                    get_services = getattr(backend, "_get_services", None)
                    if callable(get_services):
                        await get_services()
                    svcs = getattr(client, "services", None)

                response = False
                if svcs is not None and hasattr(svcs, "get_service"):
                    svc = svcs.get_service(self._service_uuid)
                    if svc is None:
                        await client.disconnect()
                        raise HexagonLightError(
                            f"Service not found: {self._service_uuid}"
                        )
                    ch = svc.get_characteristic(self._write_uuid)
                    if ch is None:
                        await client.disconnect()
                        raise HexagonLightError(
                            f"Write characteristic not found: {self._write_uuid}"
                        )
                    props = set(ch.properties or [])
                    response = "write-without-response" not in props

                def _notify_handler(_sender: int, data: bytearray) -> None:
                    with self._state_lock:
                        self._last_notify = bytes(data)
                    self._notify_event.set()

                try:
                    await client.start_notify(self._notify_uuid, _notify_handler)
                except Exception:
                    pass

                with self._state_lock:
                    self._client = client
                    self._write = _WriteConfig(
                        service_uuid=self._service_uuid,
                        char_uuid=self._write_uuid,
                        response=response,
                    )
                    self._notify_event.clear()
                return
            except BaseException as e:
                last_err = e
                await asyncio.sleep(self._retry_delay_s * attempt)

        raise HexagonLightError(
            f"Failed to connect after {self._connect_retries} attempts: {last_err}"
        )

    async def _ensure_connected(self) -> None:
        with self._state_lock:
            client = self._client
        if client is not None and client.is_connected:
            return
        await self._connect_async()

    async def _write_frame(self, frame: bytes) -> None:
        await self._ensure_connected()
        with self._state_lock:
            client = self._client
            write = self._write
        if client is None or write is None:
            raise HexagonLightError("Not connected")

        try:
            await client.write_gatt_char(
                write.char_uuid, frame, response=write.response
            )
        except Exception:
            self._on_disconnect(client)
            await self._ensure_connected()
            with self._state_lock:
                client = self._client
                write = self._write
            if client is None or write is None:
                raise HexagonLightError("Reconnect failed")
            await client.write_gatt_char(
                write.char_uuid, frame, response=write.response
            )

    async def _send_simple_power(self, on: bool) -> None:
        payload = bytes([0x01 if on else 0x00])
        frame = _build_command(0x01, payload)
        await self._write_frame(frame)

    async def _request_sync_async(self) -> None:
        frame = _build_command(0x00, None)
        await self._write_frame(frame)

    async def _set_scene_async(self, scene: int, *, speed: Optional[int]) -> None:
        scene = _clamp_int(scene, 0, 0xFFFF)
        frame = _build_command(0x06, _u16_be(scene))
        await self._write_frame(frame)
        if speed is not None:
            speed_b = _clamp_int(int(speed), 0, 255) & 0xFF
            await self._write_frame(_build_command(0x0F, bytes([speed_b])))

    async def _set_brightness_async(self, percent: int) -> None:
        value = (percent + 5) * 10
        value = _clamp_int(value, 0, 0xFFFF)
        payload = _u16_be(value)
        frame = _build_command(0x05, payload)
        await self._write_frame(frame)

    async def _set_rgb_async(self, r: int, g: int, b: int) -> None:
        payload = _rgb_to_hue_sat_payload(r, g, b)
        frame = _build_command(0x03, payload)
        await self._write_frame(frame)

    def _parse_state(self, raw: Optional[bytes]) -> HexagonState:
        if not raw:
            return HexagonState()
        if len(raw) < 6:
            return HexagonState(raw=raw)
        if (sum(raw) & 0xFF) != 0xFF:
            return HexagonState(raw=raw)

        # Some firmwares notify with 0x55 (same as outgoing), others use 0x56 for sync frames.
        if raw[0] == 0x55:
            length = raw[3]
            if length != len(raw):
                return HexagonState(raw=raw)

            # For common MeRGBW devices, payload[0] is power flag (0/1) in sync frames.
            is_on = raw[4] != 0

            brightness_percent: Optional[int] = None
            if len(raw) >= 7:
                b = int(raw[5]) - 5
                if 0 <= b <= 100:
                    brightness_percent = b

            return HexagonState(
                is_on=is_on, brightness_percent=brightness_percent, raw=raw
            )

        if raw[0] != 0x56:
            return HexagonState(raw=raw)

        # Observed on TG609-class devices: power at [4], brightness u16 at [5:7] (same encoding as cmd 0x05).
        is_on = raw[4] != 0
        brightness_percent: Optional[int] = None
        if len(raw) >= 8:
            value = (raw[5] << 8) | raw[6]
            b = (value // 10) - 5
            if 0 <= b <= 100:
                brightness_percent = b
        return HexagonState(is_on=is_on, brightness_percent=brightness_percent, raw=raw)


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Control Hexagon Light over BLE (MeRGBW protocol)."
    )
    parser.add_argument(
        "--mac",
        default="FF:FF:11:52:AB:BD",
        help="BLE MAC address (default: %(default)s)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="If >0, wait this many seconds and print status",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("on", help="Turn on")
    sub.add_parser("off", help="Turn off")
    sub.add_parser("status", help="Best-effort: read state from notifications")
    sub.add_parser("scenes", help="Print known scene names for TG609")

    p_rgb = sub.add_parser("rgb", help="Set RGB color")
    p_rgb.add_argument("r", type=int)
    p_rgb.add_argument("g", type=int)
    p_rgb.add_argument("b", type=int)

    p_b = sub.add_parser("brightness", help="Set brightness percent (0-100)")
    p_b.add_argument("percent", type=int)

    p_scene = sub.add_parser("scene", help="Set built-in scene/effect by index or name")
    p_scene.add_argument("scene", help="Scene index (int) or name (e.g. symphony)")
    p_scene.add_argument("--speed", type=int, help="Optional speed 0-255")

    p_set = sub.add_parser("set", help="Set multiple properties in one run")
    p_set.add_argument("--power", choices=["on", "off", "keep"], default="keep")
    p_set.add_argument("--rgb", nargs=3, metavar=("R", "G", "B"), type=int)
    p_set.add_argument("--brightness", dest="brightness_percent", type=int)
    p_set.add_argument("--scene", help="Scene index (int) or name (e.g. symphony)")
    p_set.add_argument(
        "--scene-speed", type=int, help="Optional speed 0-255 for --scene"
    )

    args = parser.parse_args()

    if args.cmd == "scenes":
        for name in sorted(SCENES_TG609):
            print(f"{name}={SCENES_TG609[name]}")
        return 0

    lamp = HexagonLight(args.mac)
    try:
        lamp.connect()
        if args.cmd == "on":
            lamp.turn_on()
        elif args.cmd == "off":
            lamp.turn_off()
        elif args.cmd == "status":
            st = lamp.get_state(wait_s=2.0)
            raw = st.raw.hex() if st.raw else ""
            print(f"is_on={st.is_on} brightness={st.brightness_percent} raw={raw}")
            return 0
        elif args.cmd == "rgb":
            lamp.set_rgb(args.r, args.g, args.b)
        elif args.cmd == "brightness":
            lamp.set_brightness(args.percent)
        elif args.cmd == "scene":
            scene_str = str(args.scene).strip()
            if scene_str.isdigit():
                lamp.set_scene(int(scene_str), speed=args.speed)
            else:
                lamp.set_scene_by_name(scene_str, speed=args.speed)
        elif args.cmd == "set":
            if args.power == "on":
                lamp.turn_on()
            elif args.power == "off":
                lamp.turn_off()

            if args.rgb is not None:
                r, g, b = args.rgb
                lamp.set_rgb(r, g, b)

            if args.brightness_percent is not None:
                lamp.set_brightness(args.brightness_percent)

            if args.scene is not None:
                scene_str = str(args.scene).strip()
                if scene_str.isdigit():
                    lamp.set_scene(int(scene_str), speed=args.scene_speed)
                else:
                    lamp.set_scene_by_name(scene_str, speed=args.scene_speed)
        else:
            raise HexagonLightError(f"Unknown command: {args.cmd}")

        if args.wait and args.wait > 0:
            st = lamp.get_state(wait_s=args.wait)
            raw = st.raw.hex() if st.raw else ""
            print(f"is_on={st.is_on} brightness={st.brightness_percent} raw={raw}")
        return 0
    finally:
        try:
            lamp.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(_main())
