# Current Handoff

Last updated: 2026-07-17 (America/Chicago)

## Current Objective

The project communicates with the IQAir HealthPro Plus XE exclusively over BLE.
Discovery, identity selection, persistent connection ownership, generic parameter
reads, and one-shot measurements are centralized in `iqair_client.py`.
Long-running polling and InfluxDB writing have not been implemented yet.

## Important Current Context

- The user is currently away from the purifier and outside its Bluetooth range.
- Do not interpret a current "no IQAir candidates" scan as a regression.
- Do not retry live BLE access until the user confirms that the executing machine
  is physically near the purifier.
- The purifier's Wi-Fi state and IP address are not inputs to normal collection.

## Project Environment

This is a flat `uv` project initialized with `uv init --bare`:

- `pyproject.toml`: Python `>=3.11`, runtime `bleak>=0.22`, and dev dependencies
  `pytest` and `ruff`.
- `uv.lock`: reproducible cross-platform dependency resolution.
- `.venv`: local environment created by `uv sync`/`uv add` and ignored by Git.

The repository is initialized on the `main` branch. Source, tests, lockfile,
handoff documents, generated text analysis, and package metadata are versioned.
Third-party APK/XAPK binaries and the extracted app icon remain local and ignored.

Use `uv sync` after checkout. Both `uv run query_device.py` and
`uv run python .\query_device.py` use the project environment. Root runtime scripts
no longer carry PEP 723 metadata; independent `.agents/probes/` scripts still do.

## Entry Points

### `iqair_client.py`

Reusable asynchronous library modeled after the lab's small instrument-client
pattern, adapted to Bleak's asynchronous API.

Main API:

- `await IQAirClient.scan_devices()`: advertisement-only candidate scan; no
  connection or pairing.
- `await IQAirClient.discover_devices()`: connect, verify, and query identity for
  each candidate sequentially.
- `IQAirClient(serial_or_mac)`: select by serial number or BLE MAC.
- `async with client`, `connect()`, `close()`: own and release one Bleak handle.
- `handle`, `is_connected`, `device`: connection and cached identity properties.
- `read_device_information()`: refresh standard GATT and IQAir identity fields.
- `read_parameters(codes)`: generic read-only DPRL communication method.
- `read_measurements()`: typed PM1/PM2.5/PM10/fan-RPM sample.

Every class, function, and method in the module has a docstring. The module also
has an opt-in demonstration entry point:

```powershell
uv run iqair_client.py scan
uv run iqair_client.py discover --pair
uv run iqair_client.py sample SERIAL-OR-BLE-MAC --pair
```

Running `uv run iqair_client.py` without a command prints help and performs no BLE
operation. Pairing in the direct demo requires an explicit `--pair` flag.

The client validates the custom GATT service and requires a status-0 IQAir
handshake before marking a device verified. Optional metadata failures are recorded
without invalidating an otherwise working connection.

Status: 12 offline tests and Ruff pass. The library has not yet been run against the
physical purifier because the current machine is outside BLE range.

### `query_device.py`

Thin discovery and identification CLI over `IQAirClient`. It:

1. Scans BLE advertisements.
2. Selects candidates with Bluetooth manufacturer company ID `0x060A` (IQAir AG).
3. Connects and requests pairing by default.
4. Checks the IQAir custom service and characteristics.
5. Requires a valid proprietary handshake before marking a device as verified.
6. Reads standard GATT device information when available.
7. Requests IQAir product, serial, firmware, hardware, and network identity fields.

Commands to run later, while physically near the purifier:

```powershell
uv run query_device.py
uv run query_device.py --json
uv run query_device.py --address 10:97:BD:09:3A:D2
```

Status: implementation and offline fixtures pass. Live serial-number and metadata
responses are not yet verified because the device is currently out of range.

Use `--scan-only` for advertisement-only discovery without connection or pairing.

### `iqair_test.py`

Thin one-shot reader over `IQAirClient`. It accepts a serial number or BLE MAC,
connects, requests PM1/PM2.5/PM10/fan RPM, validates the response, and emits JSON.

Known live command:

```powershell
uv run iqair_test.py --wifi-mac 10:97:BD:09:3A:D0 --pair
```

The old standalone implementation was live verified. The new library-backed CLI is
offline verified but still needs a live regression test. It does not yet have a
polling loop or InfluxDB writer.

## Known Device

- Product: IQAir HealthPro Plus XE
- GATT device name observed after pairing: `IQAir HealthPro Plus XE B009-T`
- Wi-Fi MAC: `10:97:BD:09:3A:D0`
- BLE MAC observed on Windows: `10:97:BD:09:3A:D2`
- BLE advertisement name observed: `ONHN5OCNNIU6NIFNP`
- Bluetooth company ID: decimal `1546`, hexadecimal `0x060A` (IQAir AG)
- Advertisement manufacturer payload observed: `050008`
- Historical Wi-Fi IP: `192.168.60.30` (previously `192.168.60.146`)
- Local DNS name observed: `iqair-purifier.home`

The Wi-Fi MAC plus two rule is observed for this unit only. It is not a general
identity rule for all IQAir products.

## Confirmed Decisions

- Production device access will be BLE-only.
- `iqair_client.py` is the single source of truth for protocol and connection code.
- Discovery must use manufacturer ID and protocol verification, not the random
  advertisement name.
- Routine polling may use a saved verified BLE address after initial setup.
- Python execution uses the `pyproject.toml`/`uv.lock` environment managed by `uv`.
- Wi-Fi credentials are never queried. Opcode `4102` is intentionally excluded.
- Investigation probes, local analysis tools, and APK evidence live under
  `.agents/`, separate from production code.
- Git history uses `main`; third-party mobile-app binaries are not committed.

## Next Work

1. Run `query_device.py` near the purifier and record which identification fields
   are actually returned, especially serial number and firmware versions.
2. Run the refactored `iqair_test.py` by BLE MAC and by serial number.
3. Correct any best-effort metadata decoders using captured raw values.
4. Test repeated `read_measurements()` calls over one persistent connection.
5. Add retry, reconnect, timeout, and exponential-backoff behavior.
6. Add an InfluxDB writer with normalized fields only.
7. Add CI and a remote repository when the user chooses a hosting target.

## Known Risks and Gaps

- `iqair_client.py` and both refactored CLIs are not live-verified yet.
- The newly locked Bleak `3.0.2` project environment is only offline-verified.
- Version fields are decoded conservatively as raw hex plus numeric/text hints.
- The official mobile app may compete for the device's BLE connection.
- Pairing and address behavior has only been observed on Windows.
- PM field names use `ug/m3`, but physical units/scaling have not been independently
  cross-checked against the purifier display or a reference instrument.
- The APK disassembly was generated with one-off analysis code; exact disassembly
  reproduction commands were not preserved.
