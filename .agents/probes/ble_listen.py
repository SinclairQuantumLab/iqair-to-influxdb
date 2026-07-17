# /// script
# dependencies = ["bleak>=0.22"]
# ///
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner


DEFAULT_NOTIFY_UUID = "6f5e9f58-ed60-47a2-bbe4-ec93545b94b6"


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def preview(data: bytes) -> dict[str, object]:
    return {"len": len(data), "hex": data.hex(), "text": data.decode("utf-8", errors="replace")}


async def resolve(address: str, seconds: float):
    devices = await BleakScanner.discover(timeout=seconds, return_adv=True)
    for key, (device, adv) in devices.items():
        if device.address.upper() == address.upper() or key.upper() == address.upper():
            emit(
                "ble_resolved",
                address=device.address,
                name=device.name or adv.local_name,
                rssi=adv.rssi,
                manufacturer_data={str(k): v.hex() for k, v in adv.manufacturer_data.items()},
            )
            return device
    emit("ble_resolve_miss", address=address)
    return address


async def main() -> int:
    parser = argparse.ArgumentParser(description="Subscribe to a BLE notify characteristic without writing.")
    parser.add_argument("address")
    parser.add_argument("--notify-uuid", default=DEFAULT_NOTIFY_UUID)
    parser.add_argument("--scan-seconds", type=float, default=5.0)
    parser.add_argument("--listen-seconds", type=float, default=30.0)
    parser.add_argument("--pair", action="store_true")
    args = parser.parse_args()

    target = await resolve(args.address, args.scan_seconds)
    notifications: list[dict[str, object]] = []

    def handler(sender, data: bytearray) -> None:
        item = {"sender": str(sender), "value": preview(bytes(data))}
        notifications.append(item)
        emit("ble_notify", **item)

    emit("ble_connect_start", address=args.address, notify_uuid=args.notify_uuid, pair=args.pair)
    try:
        async with BleakClient(target, pair=args.pair, timeout=20.0) as client:
            emit("ble_connected", address=args.address, is_connected=client.is_connected)
            await client.start_notify(args.notify_uuid, handler)
            emit("ble_listen_start", seconds=args.listen_seconds)
            await asyncio.sleep(args.listen_seconds)
            await client.stop_notify(args.notify_uuid)
    except Exception as exc:
        emit("ble_listen_error", error=f"{type(exc).__name__}: {exc}")
        return 1

    emit("ble_listen_summary", count=len(notifications), notifications=notifications)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
