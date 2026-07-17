from __future__ import annotations

import argparse
import asyncio
import json
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PORTS = (21, 22, 23, 53, 80, 81, 88, 123, 137, 138, 139, 443, 445, 5000, 5353, 5357, 8000, 8080, 8081, 8443, 8883, 1883, 49152)
HTTP_PATHS = (
    "/",
    "/status",
    "/api",
    "/api/status",
    "/api/v1/status",
    "/metrics",
    "/device",
    "/config",
    "/info",
    "/json",
)


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


async def tcp_check(host: str, port: int, timeout: float) -> tuple[int, bool, str | None]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return port, True, None
    except Exception as exc:
        return port, False, type(exc).__name__


def http_probe(host: str, port: int, scheme: str, path: str, timeout: float) -> dict[str, object]:
    url = f"{scheme}://{host}:{port}{path}"
    context = ssl._create_unverified_context() if scheme == "https" else None
    request = Request(url, headers={"User-Agent": "iqair-local-probe/0.1", "Accept": "*/*"})
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(500)
            headers = dict(response.headers.items())
            return {
                "url": url,
                "ok": True,
                "status": response.status,
                "headers": headers,
                "body_preview": body.decode("utf-8", errors="replace"),
            }
    except Exception as exc:
        return {"url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe one host for likely local device protocols.")
    parser.add_argument("host")
    parser.add_argument("--ports", default=",".join(str(port) for port in PORTS))
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()

    ports = tuple(int(part) for part in args.ports.split(",") if part.strip())
    emit("tcp_start", host=args.host, ports=ports)
    checks = await asyncio.gather(*(tcp_check(args.host, port, args.timeout) for port in ports))
    open_ports = [port for port, ok, _ in checks if ok]
    emit("tcp_result", host=args.host, open_ports=open_ports)

    for port in open_ports:
        if port in (80, 81, 8000, 8080, 8081, 5000, 5357, 49152):
            for path in HTTP_PATHS:
                emit("http_probe", **http_probe(args.host, port, "http", path, args.timeout))
        if port in (443, 8443):
            for path in HTTP_PATHS:
                emit("http_probe", **http_probe(args.host, port, "https", path, args.timeout))

    if 445 in open_ports or 139 in open_ports:
        emit("smb_candidate", host=args.host, note="SMB/NetBIOS port open; try AirVisual Pro Samba-style access if credentials are known.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
