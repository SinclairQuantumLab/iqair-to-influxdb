# Validation Record

## Live-Verified Results

### LAN identity and service exposure

Date: 2026-07-16, while connected to the purifier's LAN.

- `192.168.60.30` replied to ping.
- ARP MAC was `10:97:BD:09:3A:D0`, matching the known purifier.
- Reverse DNS was `iqair-purifier.home`.
- TCP ports `1-65535` were scanned; no open ports were found.
- No purifier mDNS or SSDP service was advertised.

Conclusion: the Wi-Fi interface is online but exposes no inbound local data API.

### BLE measurement read

Date: 2026-07-16, while physically within BLE range.

Command:

```powershell
uv run iqair_test.py --wifi-mac 10:97:BD:09:3A:D0 --pair --scan-seconds 5 --listen-seconds 3
```

Observed:

- BLE address: `10:97:BD:09:3A:D2`
- Advertisement name: `ONHN5OCNNIU6NIFNP`
- RSSI: `-68 dBm`
- Connection response: valid CRC, status `0`
- Measurement response: valid CRC, status `0`
- Fan RPM: `2805`
- PM1: `43`
- PM2.5: `43`
- PM10: `43`

Earlier live reads returned fan RPM around `804-805` and PM values of `1`.

## Offline-Verified Results

Date: 2026-07-16.

For `query_device.py`:

- `uv run query_device.py --help` succeeded.
- `uvx ruff check query_device.py` passed.
- Python AST parsing passed.
- A stored live DPRL frame passed length and CRC validation.
- Fragmented notification chunks reassembled into one valid frame.
- A known DPRL request fixture matched the previously live-tested request bytes.
- Reversed serial-number decoding passed a synthetic fixture.
- Human-readable report formatting passed a synthetic device fixture.

### Reusable client refactor

Date: 2026-07-17.

Commands:

```powershell
uv run --with pytest --with bleak pytest -q
uvx ruff check iqair_client.py query_device.py iqair_test.py test_iqair_client.py
uv run iqair_test.py --help
uv run query_device.py --help
```

Observed:

- All 9 tests in `test_iqair_client.py` passed.
- Stored measurement and DPRL request frames passed parsing and CRC validation.
- Fragmented BLE notifications reassembled correctly.
- Malformed parameter payloads were rejected.
- Serial-number and MAC selection behavior passed synthetic fixtures.
- Disconnected commands raised the expected library error.
- Ruff passed for the client, both CLIs, and tests.
- Both CLI help entry points loaded successfully through `uv`.

### Development archive relocation

Date: 2026-07-17.

The root `artifacts/`, `probes/`, and `tools/` directories were moved to
`.agents/artifacts/`, `.agents/probes/`, and `.agents/tools/`. The old root paths
are absent and all three new paths are present.

Commands:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .agents\artifacts\iqair_airvisual.xapk,.agents\artifacts\xapk\com.airvisual.apk
uv run --with pytest --with bleak pytest -q
uvx ruff check iqair_client.py query_device.py iqair_test.py test_iqair_client.py
uv run .agents\tools\dex_xref_strings.py --help
```

Observed:

- Both package hashes still match the values recorded in `PROVENANCE.md`.
- All 9 runtime tests passed after the move.
- Ruff passed for the runtime client, CLIs, and tests.
- The relocated DEX analysis tool loaded successfully from its new path.

### Client documentation and direct demo

Date: 2026-07-17, outside purifier Bluetooth range.

Commands:

```powershell
uv run --with pytest --with bleak pytest -q
uvx ruff check iqair_client.py test_iqair_client.py
uv run iqair_client.py
uv run iqair_client.py scan --help
uv run iqair_client.py discover --help
uv run iqair_client.py sample --help
```

Observed:

- All 12 offline tests passed.
- An AST-based test confirmed that every class, function, and method in
  `iqair_client.py` has a docstring.
- Ruff passed for the changed client and test files.
- Running the module without a command printed help and exited successfully
  without starting a BLE operation.
- Help for the `scan`, `discover`, and `sample` demo commands loaded successfully.
- No live scan, connection, pairing, identity query, or measurement was attempted.

### uv project migration

Date: 2026-07-17, outside purifier Bluetooth range.

The root PEP 723 scripts were migrated to a flat project created with
`uv init --bare`. Runtime and development dependencies are now declared in
`pyproject.toml` and resolved in `uv.lock`.

Commands:

```powershell
uv sync --frozen
uv run python .\query_device.py --help
uv run python -c "import importlib.metadata, sys; import bleak, iqair_client; print(sys.executable); print(importlib.metadata.version('bleak'))"
uv run pytest -q
uv run ruff check iqair_client.py query_device.py iqair_test.py test_iqair_client.py
```

Observed:

- Frozen sync audited the locked environment successfully.
- The user's `uv run python .\query_device.py` execution path loaded successfully
  when run with the non-BLE `--help` option.
- Python resolved to the project's `.venv\Scripts\python.exe`.
- The project imported Bleak `3.0.2` and `IQAirClient` successfully.
- All 12 offline tests passed without temporary `--with` dependencies.
- Project-managed Ruff passed all runtime and test files.
- No live BLE scan, connection, pairing, or measurement was attempted.

### Git initialization

Date: 2026-07-17.

- Initialized the repository with `git init -b main`.
- Added `.gitignore` entries for environments, caches, bytecode, third-party
  APK/XAPK files, and the extracted app icon.
- Added `.gitattributes` for stable text-file line endings.
- Prepared source, tests, project metadata, lockfile, handoff documents, generated
  text analysis, and non-binary package metadata for the baseline commit.
- Kept ignored third-party binaries on disk; no evidence file was deleted.

## Not Yet Verified

- `iqair_client.py` and both refactored CLIs against the physical purifier.
- Actual serial number response and encoding.
- Product, firmware, hardware, and certificate field encodings.
- Long-running BLE connection stability.
- Reconnect and backoff behavior.
- Periodic polling.
- InfluxDB writes.
- Operation on Linux or macOS.

## Current Test Constraint

The user is currently away from the purifier. A BLE scan from the present machine
did not see company ID `0x060A` or address `10:97:BD:09:3A:D2`, which is expected
outside Bluetooth range and is not a failed product test.

## Maintenance Format

For future meaningful tests, append:

- Local date and timezone.
- Whether the executing machine was physically near the purifier.
- Exact `uv` command.
- Relevant raw frame or checksum when protocol behavior changes.
- Outcome and any fields that remain inferred.
