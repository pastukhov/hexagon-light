from __future__ import annotations

from hexagon_light import (
    SCENES_TG609,
    HexagonLight,
    _build_command,
)


def test_build_command_checksum_is_ff() -> None:
    frame = _build_command(0x01, b"\x01")
    assert frame[0] == 0x55
    assert frame[1] == 0x01
    assert frame[2] == 0xFF
    assert frame[3] == len(frame)
    assert (sum(frame) & 0xFF) == 0xFF


def test_build_command_length_includes_payload_and_checksum() -> None:
    frame = _build_command(0x03, b"\x00\x01\x00\x02")
    assert frame[3] == 5 + 4
    assert len(frame) == 9


def test_scene_name_mapping_contains_expected_entries() -> None:
    assert SCENES_TG609["symphony"] == 2
    assert SCENES_TG609["energy"] == 3
    assert SCENES_TG609["jump"] == 4
    assert SCENES_TG609["flow"] == 55
    assert SCENES_TG609["forest"] == 13


def test_parse_state_tg609_sync_frame_0x56() -> None:
    lamp = HexagonLight("00:00:00:00:00:00")
    raw = bytes.fromhex("5600ff060100be0008177f0008177f00505009")
    st = lamp._parse_state(raw)
    assert st.is_on is True
    assert st.brightness_percent == 14
    assert st.raw == raw

