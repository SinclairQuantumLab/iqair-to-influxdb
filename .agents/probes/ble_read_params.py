# /// script
# dependencies = ["bleak>=0.22"]
# ///
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner


WRITE_UUID = "55340670-4e1c-471a-bd05-1891775a1f64"
NOTIFY_UUID = "6f5e9f58-ed60-47a2-bbe4-ec93545b94b6"

MESSAGE_CODES = {
    "CONN_REQUEST": 0x01,
    "CONN_RESPONSE": 0x81,
    "DPR_REQUEST": 0x12,
    "DPR_RESPONSE": 0x92,
    "DPRL_REQUEST": 0x13,
    "DPRL_RESPONSE": 0x93,
}

PARAM_CODES = {
    "SENSOR_AMBIENTTEMPERATURE": 3000,
    "SENSOR_AMBIENTHUMIDITY": 3001,
    "SENSOR_FANRPM": 3013,
    "SENSOR_PM25": 3023,
    "SENSOR_PM1": 3024,
    "SENSOR_PM10": 3025,
    "SENSOR_CADR": 3030,
    "UI_FANSPEEDVALUE": 5000,
    "UI_FANSPEEDMANUAL": 5010,
    "UI_UIAUTO": 5022,
    "UI_UIRADIO": 5023,
}
PARAM_NAMES_BY_CODE = {code: name for name, code in PARAM_CODES.items()}


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def int_bytes_be(value: int, size: int) -> bytes:
    return int(value).to_bytes(size, "big", signed=False)


def crc16_ccitt_iqair(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        for bit in range(8):
            data_bit = (byte >> (7 - bit)) & 1
            crc_bit = (crc >> 15) & 1
            crc = ((crc << 1) & 0xFFFF)
            if data_bit ^ crc_bit:
                crc ^= 0x1021
    return crc & 0xFFFF


def build_frame(message_code: int, payload_be: bytes) -> bytes:
    # The Android app builds multi-byte values in big-endian order, then its
    # frame writer reverses each payload region before copying it into the
    # outgoing buffer. For DPRL dynamic payloads the region is the whole list.
    payload_on_wire = payload_be[::-1]
    length = len(payload_on_wire) + 2
    frame_without_crc = bytes([message_code]) + length.to_bytes(2, "little") + payload_on_wire
    crc = crc16_ccitt_iqair(frame_without_crc)
    return frame_without_crc + crc.to_bytes(2, "little")


def build_conn_request() -> bytes:
    # CONN_REQUEST has fixed fields; the app reverses each field separately.
    field_payload = b"".join(
        [
            int_bytes_be(2, 1)[::-1],
            int_bytes_be(8192, 2)[::-1],
            int_bytes_be(0, 1)[::-1],
            int_bytes_be(int(time.time()), 4)[::-1],
        ]
    )
    length = len(field_payload) + 2
    frame_without_crc = bytes([MESSAGE_CODES["CONN_REQUEST"]]) + length.to_bytes(2, "little") + field_payload
    crc = crc16_ccitt_iqair(frame_without_crc)
    return frame_without_crc + crc.to_bytes(2, "little")


def build_dpr_request(param_name: str) -> bytes:
    payload = int_bytes_be(PARAM_CODES[param_name], 2)
    return build_frame(MESSAGE_CODES["DPR_REQUEST"], payload)


def build_dprl_request(param_names: list[str]) -> bytes:
    payload = b"".join(int_bytes_be(PARAM_CODES[name], 2) for name in param_names)
    return build_frame(MESSAGE_CODES["DPRL_REQUEST"], payload)


def parse_frame(data: bytes) -> dict[str, object]:
    parsed: dict[str, object] = {"raw_hex": data.hex(), "len": len(data)}
    if len(data) < 5:
        return parsed
    declared = int.from_bytes(data[1:3], "little")
    parsed.update(
        {
            "message_code": data[0],
            "declared_tail_len": declared,
            "expected_total_len": declared + 3,
            "payload_hex": data[3:-2].hex() if len(data) >= 5 else "",
            "crc_hex": data[-2:].hex(),
            "crc_ok": crc16_ccitt_iqair(data[:-2]).to_bytes(2, "little") == data[-2:],
        }
    )
    if parsed.get("crc_ok"):
        parsed["payload"] = parse_payload(data[0], data[3:-2])
    return parsed


def parse_payload(message_code: int, payload: bytes) -> dict[str, object]:
    if message_code == MESSAGE_CODES["CONN_RESPONSE"]:
        return {"status": payload[0] if payload else None, "raw_hex": payload.hex()}

    if message_code not in (MESSAGE_CODES["DPR_RESPONSE"], MESSAGE_CODES["DPRL_RESPONSE"]):
        return {"raw_hex": payload.hex()}

    if not payload:
        return {"status": None, "items": []}

    status = payload[0]
    cursor = 1
    items = []
    while cursor + 4 <= len(payload):
        code = int.from_bytes(payload[cursor : cursor + 2], "little")
        size = int.from_bytes(payload[cursor + 2 : cursor + 4], "little")
        cursor += 4
        value = payload[cursor : cursor + size]
        cursor += size
        item = {
            "param_code": code,
            "param_name": PARAM_NAMES_BY_CODE.get(code),
            "size": size,
            "value_hex": value.hex(),
            "value_uint_le": int.from_bytes(value, "little") if value else None,
            "value_text": value.decode("utf-8", errors="replace") if value else "",
        }
        items.append(item)

    return {"status": status, "items": items, "remaining_hex": payload[cursor:].hex()}


async def resolve(address: str, seconds: float):
    devices = await BleakScanner.discover(timeout=seconds, return_adv=True)
    for key, (device, adv) in devices.items():
        if device.address.upper() == address.upper() or key.upper() == address.upper():
            emit("ble_resolved", address=device.address, name=device.name or adv.local_name, rssi=adv.rssi)
            return device
    emit("ble_resolve_miss", address=address)
    return address


async def write_frame(client: BleakClient, name: str, frame: bytes, response: bool) -> None:
    emit("ble_write", name=name, hex=frame.hex(), parsed=parse_frame(frame), response=response)
    await client.write_gatt_char(WRITE_UUID, frame, response=response)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Send IQAir BLE read-like requests and print notifications.")
    parser.add_argument("address")
    parser.add_argument("--scan-seconds", type=float, default=5.0)
    parser.add_argument("--listen-seconds", type=float, default=8.0)
    parser.add_argument("--pair", action="store_true")
    parser.add_argument("--no-conn", action="store_true")
    parser.add_argument("--single", action="append", choices=sorted(PARAM_CODES), help="Send one DPR_REQUEST per named parameter.")
    parser.add_argument("--params", default="SENSOR_PM25,SENSOR_PM1,SENSOR_PM10,SENSOR_AMBIENTTEMPERATURE,SENSOR_AMBIENTHUMIDITY,SENSOR_FANRPM")
    parser.add_argument("--without-response", action="store_true")
    args = parser.parse_args()

    target = await resolve(args.address, args.scan_seconds)
    notifications: list[dict[str, object]] = []
    frames: list[dict[str, object]] = []
    stream = bytearray()

    def handler(sender, data: bytearray) -> None:
        raw = bytes(data)
        item = {"sender": str(sender), "raw_hex": raw.hex(), "len": len(raw)}
        notifications.append(item)
        emit("ble_notify", **item)
        stream.extend(raw)
        while len(stream) >= 3:
            declared = int.from_bytes(stream[1:3], "little")
            expected = declared + 3
            if expected < 5:
                emit("ble_stream_bad_header", buffer_hex=bytes(stream).hex(), declared_tail_len=declared)
                stream.clear()
                break
            if len(stream) < expected:
                emit("ble_stream_wait", buffered=len(stream), expected=expected)
                break
            frame_raw = bytes(stream[:expected])
            del stream[:expected]
            parsed = parse_frame(frame_raw)
            frames.append(parsed)
            emit("ble_frame", parsed=parsed)

    try:
        async with BleakClient(target, pair=args.pair, timeout=20.0) as client:
            emit("ble_connected", address=args.address, is_connected=client.is_connected)
            await client.start_notify(NOTIFY_UUID, handler)
            await asyncio.sleep(0.5)

            response = not args.without_response
            if not args.no_conn:
                await write_frame(client, "CONN_REQUEST", build_conn_request(), response=response)
                await asyncio.sleep(1.0)

            if args.single:
                for name in args.single:
                    await write_frame(client, f"DPR_REQUEST:{name}", build_dpr_request(name), response=response)
                    await asyncio.sleep(0.8)
            else:
                names = [part.strip() for part in args.params.split(",") if part.strip()]
                await write_frame(client, "DPRL_REQUEST", build_dprl_request(names), response=response)

            await asyncio.sleep(args.listen_seconds)
            await client.stop_notify(NOTIFY_UUID)
    except Exception as exc:
        emit("ble_error", error=f"{type(exc).__name__}: {exc}")
        return 1

    emit("ble_summary", chunk_count=len(notifications), frame_count=len(frames), notifications=notifications, frames=frames, leftover_hex=bytes(stream).hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
