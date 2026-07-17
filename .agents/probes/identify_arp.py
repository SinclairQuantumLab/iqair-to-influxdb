# /// script
# dependencies = ["manuf>=1.1.5"]
# ///
from __future__ import annotations

import json
import re
import socket
import subprocess
from datetime import datetime, timezone

from manuf import manuf


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, sort_keys=True))


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
            if not ip.startswith(("224.", "239.", "255.")) and mac != "ff-ff-ff-ff-ff-ff":
                entries[ip] = mac.lower()
    return entries


def reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def netbios_name(ip: str) -> list[str]:
    try:
        raw = subprocess.check_output(["nbtstat", "-A", ip], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except Exception:
        return []

    names: list[str] = []
    for line in raw.splitlines():
        match = re.match(r"\s*([^\s<]+)\s+<([0-9A-Fa-f]{2})>\s+(UNIQUE|GROUP)", line)
        if match:
            names.append(match.group(1).strip())
    return sorted(set(names))


def main() -> int:
    parser = manuf.MacParser()
    entries = read_arp_table()
    rows = []
    for ip, mac in sorted(entries.items(), key=lambda item: tuple(int(part) for part in item[0].split("."))):
        rows.append(
            {
                "ip": ip,
                "mac": mac,
                "vendor": parser.get_manuf(mac),
                "comment": parser.get_comment(mac),
                "reverse_dns": reverse_dns(ip),
                "netbios_names": netbios_name(ip),
            }
        )

    for row in rows:
        emit("host_identity", **row)
    emit("identity_summary", hosts=rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
