from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


COMMON_PORTS = (80, 443, 445, 139, 22, 23, 53, 8080, 8443, 8000, 8883, 1883, 5357)


@dataclass(frozen=True)
class InterfaceNetwork:
    address: str
    prefix_length: int

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.address}/{self.prefix_length}", strict=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": utc_now(), "event": event, **fields}, sort_keys=True))


def get_windows_interfaces() -> list[InterfaceNetwork]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-NetIPAddress -AddressFamily IPv4 | "
            "Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike '169.254.*' } | "
            "Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress"
        ),
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        raw = ""

    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed = [parsed]
            result = []
            for item in parsed:
                result.append(InterfaceNetwork(item["IPAddress"], int(item["PrefixLength"])))
            if result:
                return result
        except Exception as exc:
            emit("interface_parse_error", error=str(exc), raw=raw[:500])

    # Fallback for unusual shells where PowerShell JSON failed.
    hostname = socket.gethostname()
    result = []
    for addr in socket.gethostbyname_ex(hostname)[2]:
        if not addr.startswith(("127.", "169.254.")):
            result.append(InterfaceNetwork(addr, 24))
    return result


def read_arp_table() -> dict[str, str]:
    try:
        raw = subprocess.check_output(["arp", "-a"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return {}

    entries: dict[str, str] = {}
    for line in raw.splitlines():
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+", line)
        if match:
            ip, mac = match.groups()
            entries[ip] = mac.lower()
    return entries


async def tcp_check(ip: str, port: int, timeout: float) -> tuple[int, bool]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return port, True
    except Exception:
        return port, False


async def probe_ip(ip: str, ports: tuple[int, ...], timeout: float) -> dict[str, object] | None:
    tasks = [tcp_check(ip, port, timeout) for port in ports]
    results = await asyncio.gather(*tasks)
    open_ports = [port for port, is_open in results if is_open]
    if open_ports:
        return {"ip": ip, "open_ports": open_ports}
    return None


async def scan_network(network: ipaddress.IPv4Network, ports: tuple[int, ...], timeout: float, limit: int) -> list[dict[str, object]]:
    sem = asyncio.Semaphore(limit)

    async def limited(ip: ipaddress.IPv4Address) -> dict[str, object] | None:
        async with sem:
            return await probe_ip(str(ip), ports, timeout)

    tasks = [limited(ip) for ip in network.hosts()]
    results = await asyncio.gather(*tasks)
    return [item for item in results if item]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Discover local hosts by probing common TCP ports.")
    parser.add_argument("--network", action="append", help="CIDR network to scan. Defaults to active non-link-local IPv4 interfaces.")
    parser.add_argument("--ports", default=",".join(str(port) for port in COMMON_PORTS))
    parser.add_argument("--timeout", type=float, default=0.45)
    parser.add_argument("--concurrency", type=int, default=128)
    args = parser.parse_args()

    ports = tuple(int(part) for part in args.ports.split(",") if part.strip())
    networks = [ipaddress.ip_network(item, strict=False) for item in args.network or []]
    if not networks:
        interfaces = get_windows_interfaces()
        emit("interfaces", interfaces=[{"address": item.address, "prefix_length": item.prefix_length, "network": str(item.network)} for item in interfaces])
        networks = [item.network for item in interfaces]

    before_arp = read_arp_table()
    emit("arp_before", entries=before_arp)

    all_results: list[dict[str, object]] = []
    for network in networks:
        if network.prefixlen < 24 and not os.environ.get("ALLOW_LARGE_SCAN"):
            emit("skip_large_network", network=str(network), reason="set ALLOW_LARGE_SCAN=1 to scan networks larger than /24")
            continue
        emit("scan_start", network=str(network), ports=ports, timeout=args.timeout)
        results = await scan_network(network, ports, args.timeout, args.concurrency)
        emit("scan_result", network=str(network), hosts=results)
        all_results.extend(results)

    after_arp = read_arp_table()
    new_arp = {ip: mac for ip, mac in after_arp.items() if ip not in before_arp}
    emit("arp_after", entries=after_arp, new_entries=new_arp)
    emit("summary", hosts=all_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
