"""Discover and list local IQAir purifiers using :mod:`iqair_client`."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from iqair_client import IQAirClient, IQAirDevice


def compact_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None or value == "":
        return "(not reported)"
    return str(value)


def print_result(devices: list[IQAirDevice], *, scan_only: bool) -> None:
    mode = "advertisement candidate" if scan_only else "IQAir candidate"
    print(f"Found {len(devices)} {mode}(s).")
    if not devices:
        return

    for index, device in enumerate(devices, start=1):
        print()
        print(f"[{index}] {device.display_name}")
        print(f"  Verified IQAir: {'yes' if device.verified else 'no'}")
        print(f"  Serial number: {compact_value(device.serial_number)}")
        print(f"  BLE MAC address: {device.mac_address}")
        print(f"  Advertised name: {compact_value(device.advertised_name)}")
        print(f"  Signal strength: {compact_value(device.rssi_dbm)} dBm")
        print(
            "  Manufacturer ID: "
            + (
                f"0x{device.manufacturer_company_id:04X}"
                if device.manufacturer_company_id is not None
                else "(not reported)"
            )
        )
        print(f"  Manufacturer data: {compact_value(device.manufacturer_data_hex)}")

        if device.standard_information:
            print("  Standard Bluetooth information:")
            for key, value in device.standard_information.items():
                print(f"    {key}: {compact_value(value)}")

        if device.iqair_information:
            print("  IQAir information:")
            for key, value in device.iqair_information.items():
                print(f"    {key}: {compact_value(value)}")

        for error in device.errors:
            print(f"  Warning: {error}")


async def async_main(args: argparse.Namespace) -> int:
    if args.scan_only:
        devices = await IQAirClient.scan_devices(
            scan_seconds=args.scan_seconds,
            addresses=args.address,
        )
    else:
        devices = await IQAirClient.discover_devices(
            scan_seconds=args.scan_seconds,
            pair=args.pair,
            connect_timeout=args.connect_timeout,
            response_timeout=args.response_timeout,
            query_identity=True,
            addresses=args.address,
        )

    if args.json:
        report = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scan_only": args.scan_only,
            "candidate_count": len(devices),
            "verified_iqair_count": sum(device.verified for device in devices),
            "devices": [device.to_dict() for device in devices],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_result(devices, scan_only=args.scan_only)

    if args.scan_only:
        return 0 if devices else 1
    return 0 if any(device.verified for device in devices) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover, verify, pair with, and identify local IQAir purifiers over Bluetooth."
    )
    parser.add_argument("--scan-seconds", type=float, default=10.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--response-timeout", type=float, default=6.0)
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        metavar="MAC",
        help="Also try a known BLE MAC even when its advertisement was not seen.",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="List IQAir manufacturer advertisements without connecting or pairing.",
    )
    pairing = parser.add_mutually_exclusive_group()
    pairing.add_argument("--pair", dest="pair", action="store_true", default=True)
    pairing.add_argument("--no-pair", dest="pair", action="store_false")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.scan_seconds <= 0 or args.connect_timeout <= 0 or args.response_timeout <= 0:
        parser.error("timeouts and scan duration must be positive")
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
