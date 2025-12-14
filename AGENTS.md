# agents.md — MeRGBW / Hexagon BLE Light reverse engineering agent

## Role
You are a software agent tasked with producing a **working Python script** to control a BLE RGBW hexagon light
(brand Fivemi / app MeRGBW) using **Bluetooth LE (GATT)**.

You are not writing a Home Assistant integration yet.
Your immediate goal is a **standalone, reliable Python controller**.

---

## Target device
- Device name (advertised): `Hexagon Light`
- BLE MAC address: `FF:FF:11:52:AB:BD`
- Control app: **MeRGBW (Android APK provided)**
- Power: USB 5V
- BLE only (no Wi-Fi, no Tuya, no Mesh gateway)

---

## Known BLE details (validated)
- Connection via BLE GATT works (tested with nRF Connect)
- Services discovered:
  - `0x1800` Generic Access
  - `0x1801` Generic Attribute
  - `0xFFF0` (custom LED service, primary control service)
  - `5833ff01-9b8b-5191-6142-22a4536ef123` (vendor-specific)

- Device accepts GATT connections without bonding.
- BLE advertising and connection are stable.
- BLE proxies and Home Assistant can see the device.

---

## Repository contents
You have access to:
- `hexagon_ble.py` — exploratory Python script using `bleak`
  - Dumps services and characteristics
  - Allows writing arbitrary HEX payloads
  - Subscribes to notifications (if any)
- `MeRGBW.apk` — original Android application

You are allowed to:
- Read the Python script
- Assume the APK can be reverse-engineered conceptually (protocol inference),
  but **do not rely on heavy decompilation tools** unless strictly necessary.

---

## Immediate objective (Phase 1)
Produce a **single Python file** that can:

1. Connect to the device via BLE
2. Turn the light ON
3. Turn the light OFF
4. Set RGB color
5. Set brightness

Requirements:
- Python 3.10+
- Use `bleak`
- No GUI
- CLI or simple function calls are acceptable
- Must be robust to reconnects (retry logic)

---

## Expected API (example)
The resulting script should expose something like:

```python
from hexagon_light import HexagonLight

lamp = HexagonLight("FF:FF:11:52:AB:BD")
lamp.connect()

lamp.turn_on()
lamp.set_rgb(255, 100, 50)
lamp.set_brightness(80)
lamp.turn_off()
```
or equivalent CLI usage.

## Protocol discovery strategy (important)

Follow this order:

1. Inspect hexagon_ble.py output:

- Identify writable characteristics, especially under service 0xFFF0
- Prefer write-without-response characteristics

2. Infer command format:

- Many MeRGBW / LED BLE devices use fixed-length frames
- Common patterns:
  - Header byte(s)
  - Command ID
  - Payload (RGB, brightness)
  - Optional checksum or footer

3. Use nRF Connect observations:

- Compare HEX writes when toggling power / color in the official app
- Reproduce the same writes in Python

4. Notifications are optional:

- Device may be “fire-and-forget”
- Do not block on responses unless clearly required

## Constraints

- Do NOT assume Tuya, Xiaomi, or Bluetooth Mesh
- Do NOT require pairing/bonding unless proven necessary
- Do NOT hardcode timing sleeps without justification
- Do NOT use Home Assistant APIs at this stage

## Error handling expectations

The script must:

- Retry connection on failure
- Gracefully handle disconnects
- Log BLE errors clearly
- Fail fast if required characteristics are not found

## Deliverables

Produce:

1. `hexagon_light.py` (or similarly named)

- Clean, readable Python
- Minimal dependencies (bleak only)
- Inline comments explaining protocol assumptions

2. Optional:

- A short README or docstring explaining usage
- Notes about protocol structure (for future HA integration)

---

## Current status (implemented)

### Working controller
- `hexagon_light.py` implements a standalone controller using `bleak` (Python 3.10+).
- Provides both programmatic API (`HexagonLight`) and CLI commands.

CLI examples:
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD on`
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD off`
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD rgb 255 100 50`
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD brightness 80`
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD --wait 2 set --power on --rgb 255 100 50 --brightness 80`
- `python3 hexagon_light.py --mac FF:FF:11:52:AB:BD status`

Notes:
- On Linux, the process must have access to the system Bluetooth adapter (HCI socket). Running in a sandboxed environment may block BLE access.

### Protocol (reverse engineered from MeRGBW app, verified with TG609)
GATT:
- Primary control service: `0000fff0-0000-1000-8000-00805f9b34fb`
- Write characteristic: `0000fff3-0000-1000-8000-00805f9b34fb`
- Notify characteristic: `0000fff4-0000-1000-8000-00805f9b34fb`

Frames:
- Outgoing command frame (app-style):
  - `[0] 0x55` header
  - `[1] cmd`
  - `[2] seq` (usually `0xFF`)
  - `[3] length` (total bytes)
  - `[4..n-2] payload`
  - `[n-1] checksum` such that `(sum(frame) & 0xFF) == 0xFF`
- TG609 sync notifications may start with `0x56` (still checksum-valid); controller parses both.

Known commands:
- `0x00` request sync/status (no payload; response via notify on `FFF4`)
- `0x01` power: `00` off / `01` on
- `0x03` color: HSV payload (`hue_u16_be`, `sat_u16_be` where `sat = saturation * 1000`)
- `0x05` brightness: `value_u16_be`, where app uses `value = (percent + 5) * 10`
- `0x06` built-in scene/effect: `scene_index_u16_be`
- `0x0f` scene speed: 1 byte (`0..255`)

### Scenes (TG609, from app mapping)
- Implemented `scene <name|index>` and `scenes` list output.
- Name mapping is maintained in `SCENES_TG609` in `hexagon_light.py` (examples: `symphony`, `energy`, `jump`, `vitality`, `accumulation`, `chase`, `space-time`, `ephemeral`, `flow`, `forest`, `neon_lights`, `green_jade`, `running`, `pink_light`, `alarm`, `aurora`, `rainbow`, `melody`).

## Future phases (do not implement yet, but keep in mind)

- Convert to async-safe reusable library
- Wrap into Home Assistant custom integration
- ESPHome BLE implementation
- Effect / animation support
- Music sync (if feasible)

## Success criteria

This task is successful when:

- The Python script can reliably control the physical light
- Commands match behavior of the MeRGBW app
- No Home Assistant involvement is required
