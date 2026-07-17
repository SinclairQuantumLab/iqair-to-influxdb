from __future__ import annotations

import argparse
import asyncio
import json
import socket
from datetime import datetime, timezone


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


def parse_ports(value: str) -> list[int]:
    ports: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            ports.update(range(int(start), int(end) + 1))
        else:
            ports.add(int(part))
    return sorted(port for port in ports if 1 <= port <= 65535)


async def check(host: str, port: int, timeout: float) -> int | None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return port
    except Exception:
        return None


async def scan(host: str, ports: list[int], timeout: float, concurrency: int) -> list[int]:
    semaphore = asyncio.Semaphore(concurrency)
    open_ports: list[int] = []

    async def limited(port: int) -> None:
        async with semaphore:
            result = await check(host, port, timeout)
            if result is not None:
                open_ports.append(result)
                emit("tcp_open", host=host, port=result)

    tasks = [limited(port) for port in ports]
    await asyncio.gather(*tasks)
    return sorted(open_ports)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scan TCP ports on a single host.")
    parser.add_argument("host")
    parser.add_argument("--ports", default="1-65535")
    parser.add_argument("--timeout", type=float, default=0.35)
    parser.add_argument("--concurrency", type=int, default=512)
    args = parser.parse_args()

    try:
        resolved = socket.gethostbyname(args.host)
    except Exception as exc:
        emit("resolve_error", host=args.host, error=str(exc))
        return 2

    ports = parse_ports(args.ports)
    emit("tcp_scan_start", host=args.host, resolved=resolved, port_count=len(ports), timeout=args.timeout, concurrency=args.concurrency)
    open_ports = await scan(args.host, ports, args.timeout, args.concurrency)
    emit("tcp_scan_summary", host=args.host, open_ports=open_ports)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
