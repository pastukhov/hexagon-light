#!/usr/bin/env python3
import asyncio
import sys
from dataclasses import dataclass

from bleak import BleakClient

MAC = "FF:FF:11:52:AB:BD"


@dataclass
class CandidateChar:
    uuid: str
    props: list[str]
    service_uuid: str


def _hex_to_bytes(s: str) -> bytes:
    s = s.strip().lower().replace("0x", "").replace(" ", "")
    if len(s) % 2 != 0:
        raise ValueError("HEX string must have even length")
    return bytes.fromhex(s)


def _fmt_props(props: list[str]) -> str:
    return ",".join(props)


async def dump_gatt(client: BleakClient) -> list[CandidateChar]:
    svcs = await client.get_services()
    candidates: list[CandidateChar] = []

    print("\n=== Services / Characteristics ===")
    for s in svcs:
        print(f"\nService: {s.uuid}")
        for c in s.characteristics:
            props = list(c.properties) if c.properties else []
            print(f"  Char: {c.uuid}  props=[{_fmt_props(props)}]")
            if "write" in props or "write-without-response" in props:
                candidates.append(CandidateChar(uuid=c.uuid, props=props, service_uuid=s.uuid))

    print("\n=== Writable characteristic candidates ===")
    for i, c in enumerate(candidates):
        print(f"[{i}] {c.uuid}  props=[{_fmt_props(c.props)}]  (service={c.service_uuid})")

    # Подсказка: многие LED-контроллеры используют сервис FFF0 и write-char FFF3/FFF1,
    # но у разных производителей это отличается. Мы просто показываем кандидатов.
    return candidates


async def try_enable_notifications(client: BleakClient) -> None:
    svcs = await client.get_services()

    async def _handler(sender: int, data: bytearray) -> None:
        print(f"\n[NOTIFY] from handle=0x{sender:04x}: {data.hex()}")

    print("\n=== Notification candidates ===")
    for s in svcs:
        for c in s.characteristics:
            props = list(c.properties) if c.properties else []
            if "notify" in props or "indicate" in props:
                print(f"  notify char: {c.uuid} props=[{_fmt_props(props)}] (service={s.uuid})")
                try:
                    await client.start_notify(c.uuid, _handler)
                    print("    -> subscribed OK")
                except Exception as e:
                    print(f"    -> subscribe failed: {e}")

    print("\nNotifications subscribed (where possible). Press Ctrl+C to stop.\n")


async def main() -> int:
    mac = MAC
    if len(sys.argv) > 1:
        mac = sys.argv[1]

    print(f"Connecting to {mac} ...")

    async with BleakClient(mac, timeout=15.0) as client:
        if not client.is_connected:
            print("Failed to connect.")
            return 2

        print("Connected.")
        candidates = await dump_gatt(client)

        # Попробуем подписаться на уведомления (если есть notify chars)
        # Это полезно, чтобы увидеть ответы устройства.
        await try_enable_notifications(client)

        # Выбор writable characteristic
        chosen: CandidateChar | None = None

        # Небольшая эвристика: если есть сервис FFF0 — предпочитаем write-char из него.
        for c in candidates:
            if (
                c.service_uuid.lower().endswith("fff0")
                or c.service_uuid.lower() == "0000fff0-0000-1000-8000-00805f9b34fb"
            ):
                chosen = c
                break
        if chosen is None and candidates:
            chosen = candidates[0]

        if chosen is None:
            print("No writable characteristics found.")
            return 3

        print(
            f"\nUsing writable characteristic: {chosen.uuid} "
            f"(service={chosen.service_uuid}, props={chosen.props})"
        )

        print(
            "\nInteractive mode:\n"
            "  - type HEX payload to write (example: 7e00050301ff00ef)\n"
            "  - commands:\n"
            "      :c <index>   choose writable char by index from the list\n"
            "      :q           quit\n"
        )

        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            except KeyboardInterrupt:
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            if line == ":q":
                break
            if line.startswith(":c"):
                parts = line.split()
                if len(parts) != 2:
                    print("Usage: :c <index>")
                    continue
                try:
                    idx = int(parts[1])
                    chosen = candidates[idx]
                    print(f"Chosen: {chosen.uuid} (service={chosen.service_uuid})")
                except Exception as e:
                    print(f"Bad index: {e}")
                continue

            try:
                payload = _hex_to_bytes(line)
            except Exception as e:
                print(f"HEX parse error: {e}")
                continue

            try:
                # write-without-response обычно устойчивее для подобных контроллеров,
                # но если характеристика поддерживает только write, bleak сам обработает.
                without_response = "write-without-response" in chosen.props
                await client.write_gatt_char(chosen.uuid, payload, response=not without_response)
                print(f"[WRITE] {chosen.uuid} <= {payload.hex()} (response={not without_response})")
            except Exception as e:
                print(f"Write failed: {e}")

    print("Disconnected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
