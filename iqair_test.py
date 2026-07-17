"""One-shot IQAir measurement test built on :class:`iqair_client.IQAirClient`."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from iqair_client import IQAirClient, increment_mac


def emit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields},
            sort_keys=True,
        )
    )


def resolve_selector(args: argparse.Namespace) -> str:
    selectors = [
        value
        for value in (
            args.identifier,
            args.serial_number,
            args.address,
            increment_mac(args.wifi_mac, 2) if args.wifi_mac else None,
        )
        if value
    ]
    if not selectors:
        raise SystemExit("pass --identifier, --serial-number, --address, or --wifi-mac")
    if len(selectors) > 1:
        raise SystemExit("pass only one device selector")
    return selectors[0]


async def async_main(args: argparse.Namespace) -> int:
    selector = resolve_selector(args)
    async with IQAirClient(
        selector,
        pair=args.pair,
        scan_seconds=args.scan_seconds,
        connect_timeout=args.connect_timeout,
        response_timeout=args.response_timeout,
        query_identity_on_connect=not args.skip_device_info,
    ) as client:
        emit(
            "connected",
            mac_address=client.mac_address,
            serial_number=client.serial_number,
        )
        sample = await client.read_measurements()
        emit("sample", **sample.to_dict(include_raw_frames=args.include_raw_frames))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect to one IQAir purifier and read one BLE measurement sample."
    )
    parser.add_argument("--identifier", help="Purifier serial number or BLE MAC address.")
    parser.add_argument("--serial-number", help="Purifier serial number.")
    parser.add_argument("--address", help="BLE MAC address (legacy alias).")
    parser.add_argument(
        "--wifi-mac",
        help="Known Wi-Fi MAC; this unit's BLE MAC is inferred as Wi-Fi MAC + 2.",
    )
    parser.add_argument("--scan-seconds", type=float, default=8.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument(
        "--response-timeout",
        "--listen-seconds",
        dest="response_timeout",
        type=float,
        default=6.0,
        help="IQAir response timeout; --listen-seconds is kept as a legacy alias.",
    )
    parser.add_argument("--pair", action="store_true")
    parser.add_argument(
        "--skip-device-info",
        action="store_true",
        help="Skip optional product/firmware metadata reads after connecting.",
    )
    parser.add_argument("--include-raw-frames", action="store_true")
    args = parser.parse_args()

    if args.scan_seconds <= 0 or args.connect_timeout <= 0 or args.response_timeout <= 0:
        parser.error("timeouts and scan duration must be positive")
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
