# /// script
# dependencies = ["bleak>=0.22"]
# ///
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def bytes_preview(value: bytes, limit: int = 128) -> dict[str, object]:
    preview = value[:limit]
    return {
        "len": len(value),
        "hex": preview.hex(),
        "text": preview.decode("utf-8", errors="replace"),
        "truncated": len(value) > limit,
    }


async def resolve_device(address: str, timeout: float):
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for key, (device, adv) in devices.items():
        if device.address.upper() == address.upper() or key.upper() == address.upper():
            emit(
                "ble_resolved",
                address=device.address,
                name=device.name or adv.local_name,
                rssi=adv.rssi,
                service_uuids=adv.service_uuids,
                manufacturer_data={str(k): v.hex() for k, v in adv.manufacturer_data.items()},
            )
            return device
    emit("ble_resolve_miss", address=address, seen=[device.address for device, _ in devices.values()])
    return address


async def main() -> int:
    parser = argparse.ArgumentParser(description="Connect to a BLE device and enumerate GATT services.")
    parser.add_argument("address")
    parser.add_argument("--scan-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--pair", action="store_true")
    parser.add_argument("--read", action="store_true", help="Attempt to read readable characteristics.")
    args = parser.parse_args()

    target = await resolve_device(args.address, args.scan_seconds)
    emit("ble_connect_start", address=args.address, pair=args.pair)
    try:
        async with BleakClient(target, timeout=args.timeout, pair=args.pair) as client:
            emit("ble_connected", address=args.address, is_connected=client.is_connected)
            services = client.services
            for service in services:
                emit("ble_service", uuid=service.uuid, description=service.description)
                for char in service.characteristics:
                    emit(
                        "ble_characteristic",
                        service_uuid=service.uuid,
                        uuid=char.uuid,
                        description=char.description,
                        properties=char.properties,
                        handle=char.handle,
                    )
                    for descriptor in char.descriptors:
                        emit(
                            "ble_descriptor",
                            characteristic_uuid=char.uuid,
                            uuid=descriptor.uuid,
                            description=descriptor.description,
                            handle=descriptor.handle,
                        )
                        if args.read:
                            try:
                                value = await client.read_gatt_descriptor(descriptor.handle)
                                emit("ble_descriptor_read", handle=descriptor.handle, uuid=descriptor.uuid, value=bytes_preview(bytes(value)))
                            except Exception as exc:
                                emit("ble_descriptor_read_error", handle=descriptor.handle, uuid=descriptor.uuid, error=f"{type(exc).__name__}: {exc}")
                    if args.read and "read" in char.properties:
                        try:
                            value = await client.read_gatt_char(char.uuid)
                            emit("ble_read", uuid=char.uuid, value=bytes_preview(bytes(value)))
                        except Exception as exc:
                            emit("ble_read_error", uuid=char.uuid, error=f"{type(exc).__name__}: {exc}")
            emit("ble_probe_complete", address=args.address)
    except Exception as exc:
        emit("ble_connect_error", address=args.address, error=f"{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
