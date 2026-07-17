# /// script
# dependencies = ["zeroconf>=0.132"]
# ///
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf


SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_airplay._tcp.local.",
    "_hap._tcp.local.",
    "_workstation._tcp.local.",
    "_smb._tcp.local.",
    "_ssh._tcp.local.",
    "_mqtt._tcp.local.",
]


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


class Listener(ServiceListener):
    def __init__(self) -> None:
        self.services: list[dict[str, object]] = []

    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if not info:
            emit("mdns_seen", service_type=service_type, name=name, resolved=False)
            return
        addresses = [".".join(str(part) for part in addr) for addr in info.addresses]
        props = {
            key.decode(errors="replace"): value.decode(errors="replace") if value is not None else None
            for key, value in info.properties.items()
        }
        item = {
            "service_type": service_type,
            "name": name,
            "server": info.server,
            "port": info.port,
            "addresses": addresses,
            "properties": props,
        }
        self.services.append(item)
        emit("mdns_service", **item)

    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zeroconf, service_type, name)

    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        emit("mdns_removed", service_type=service_type, name=name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse selected mDNS service types.")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--service", action="append", help="Additional service type, e.g. _http._tcp.local.")
    args = parser.parse_args()

    service_types = SERVICE_TYPES + (args.service or [])
    listener = Listener()
    zeroconf = Zeroconf()
    browsers = []
    try:
        emit("mdns_start", service_types=service_types, seconds=args.seconds)
        browsers = [ServiceBrowser(zeroconf, item, listener) for item in service_types]
        time.sleep(args.seconds)
    finally:
        for browser in browsers:
            browser.cancel()
        zeroconf.close()
    emit("mdns_summary", services=listener.services)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
