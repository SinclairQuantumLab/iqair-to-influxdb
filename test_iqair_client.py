from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iqair_client import (
    DPRL_RESPONSE,
    IQAirClient,
    IQAirDevice,
    IQAirDeviceNotFoundError,
    IQAirNotConnectedError,
    IQAirSample,
    _FrameStream,
    _build_demo_parser,
    _run_demo,
    build_dprl_request,
    build_frame,
    increment_mac,
    main,
    normalize_mac,
    parse_frame,
)


KNOWN_MEASUREMENT_FRAME = bytes.fromhex(
    "931b0000c50b02002503d10b02000100d00b02000100cf0b020001000728"
)


def test_known_measurement_frame_parses() -> None:
    frame = parse_frame(KNOWN_MEASUREMENT_FRAME)

    assert frame.valid
    assert frame.status == 0
    assert {parameter.key: parameter.value for parameter in frame.parameters} == {
        "fan_rpm": 805,
        "pm10_ugm3": 1,
        "pm1_ugm3": 1,
        "pm25_ugm3": 1,
    }


def test_fragmented_notifications_reassemble() -> None:
    async def run() -> None:
        stream = _FrameStream()
        stream.feed(KNOWN_MEASUREMENT_FRAME[:7])
        stream.feed(KNOWN_MEASUREMENT_FRAME[7:20])
        assert stream.frames == []
        stream.feed(KNOWN_MEASUREMENT_FRAME[20:])
        assert len(stream.frames) == 1
        assert stream.frames[0].valid

    asyncio.run(run())


def test_known_parameter_request_matches_live_fixture() -> None:
    request = build_dprl_request([3023, 3024, 3025, 3013])
    assert request.hex() == "130a00c50bd10bd00bcf0bd187"


def test_serial_number_is_decoded_from_reversed_wire_text() -> None:
    serial_number = "B123456789T"
    wire_value = serial_number.encode("ascii")[::-1]
    payload = (
        b"\x00"
        + (1000).to_bytes(2, "little")
        + len(wire_value).to_bytes(2, "little")
        + wire_value
    )
    frame = parse_frame(build_frame(DPRL_RESPONSE, payload))

    assert frame.valid
    assert frame.parameters[0].key == "serial_number"
    assert frame.parameters[0].value == serial_number


def test_truncated_parameter_payload_is_rejected() -> None:
    payload = b"\x00" + (1000).to_bytes(2, "little") + (20).to_bytes(2, "little") + b"short"
    frame = parse_frame(build_frame(DPRL_RESPONSE, payload))

    assert not frame.valid
    assert frame.error == "parameter 1000 extends past the response payload"


def test_select_device_by_serial_or_mac() -> None:
    first = IQAirDevice(
        mac_address="10:97:BD:09:3A:D2",
        serial_number="B123456789T",
        verified=True,
    )
    second = IQAirDevice(
        mac_address="10:97:BD:09:3A:E2",
        serial_number="B987654321T",
        verified=True,
    )

    assert IQAirClient.select_device([first, second], "b123456789t") is first
    assert IQAirClient.select_device([first, second], "10-97-bd-09-3a-e2") is second
    with pytest.raises(IQAirDeviceNotFoundError):
        IQAirClient.select_device([first, second], "missing")


def test_mac_helpers() -> None:
    assert normalize_mac("1097bd093ad2") == "10:97:BD:09:3A:D2"
    assert increment_mac("10:97:BD:09:3A:D0", 2) == "10:97:BD:09:3A:D2"


def test_disconnected_client_rejects_commands() -> None:
    async def run() -> None:
        client = IQAirClient("10:97:BD:09:3A:D2")
        with pytest.raises(IQAirNotConnectedError):
            await client.read_measurements()

    asyncio.run(run())


def test_sample_exposes_influx_ready_fields() -> None:
    sample = IQAirSample(
        observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        mac_address="10:97:BD:09:3A:D2",
        serial_number="B123456789T",
        fan_rpm=805,
        pm1_ugm3=1,
        pm25_ugm3=1,
        pm10_ugm3=None,
    )

    assert sample.fields == {
        "fan_rpm": 805,
        "pm1_ugm3": 1,
        "pm25_ugm3": 1,
    }
    assert sample.to_dict()["serial_number"] == "B123456789T"


def test_every_client_definition_has_a_docstring() -> None:
    source = Path("iqair_client.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    missing = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.get_docstring(node) is None
    ]

    assert missing == []


def test_module_demo_without_command_only_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0

    output = capsys.readouterr()
    assert "scan" in output.out
    assert "discover" in output.out
    assert "sample" in output.out
    assert output.err == ""


def test_module_scan_demo_emits_device_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_scan_devices(**_kwargs: object) -> list[IQAirDevice]:
        return [
            IQAirDevice(
                mac_address="10:97:BD:09:3A:D2",
                advertised_name="test-advertisement",
                manufacturer_company_id=0x060A,
            )
        ]

    monkeypatch.setattr(IQAirClient, "scan_devices", fake_scan_devices)
    args = _build_demo_parser().parse_args(["scan", "--scan-seconds", "1"])

    assert asyncio.run(_run_demo(args)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "scan"
    assert report["candidate_count"] == 1
    assert report["devices"][0]["mac_address"] == "10:97:BD:09:3A:D2"
