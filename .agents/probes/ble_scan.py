# /// script
# dependencies = ["bleak>=0.22"]
# ///
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from bleak import BleakScanner


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scan nearby BLE advertisements.")
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    emit("ble_start", seconds=args.seconds)
    devices = await BleakScanner.discover(timeout=args.seconds, return_adv=True)
    rows = []
    for key, (device, adv) in devices.items():
        rows.append(
            {
                "key": key,
                "address": device.address,
                "name": device.name or adv.local_name,
                "rssi": adv.rssi,
                "service_uuids": adv.service_uuids,
                "manufacturer_data": {str(k): v.hex() for k, v in adv.manufacturer_data.items()},
            }
        )
    rows.sort(key=lambda item: (item["name"] or "", item["address"]))
    for row in rows:
        emit("ble_device", **row)
    emit("ble_summary", count=len(rows), devices=rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
