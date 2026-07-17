from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime, timezone


MSEARCH = "\r\n".join(
    [
        "M-SEARCH * HTTP/1.1",
        "HOST: 239.255.255.250:1900",
        'MAN: "ssdp:discover"',
        "MX: 1",
        "ST: ssdp:all",
        "",
        "",
    ]
).encode()


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for SSDP/UPnP devices.")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(0.5)
    sock.sendto(MSEARCH, ("239.255.255.250", 1900))
    emit("ssdp_sent", seconds=args.seconds)

    deadline = time.time() + args.seconds
    seen = set()
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        text = data.decode("utf-8", errors="replace")
        key = (addr, text)
        if key in seen:
            continue
        seen.add(key)
        emit("ssdp_response", address=addr[0], port=addr[1], response=text)
    emit("ssdp_summary", count=len(seen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
