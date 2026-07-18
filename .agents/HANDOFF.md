# Current Handoff

Last updated: 2026-07-17 (America/Chicago)

## Current Objective

The project communicates with the IQAir HealthPro Plus XE exclusively over BLE.
Discovery, identity selection, persistent connection ownership, generic parameter
reads, and one-shot measurements are centralized in `iqair_client.py`.
Long-running polling and InfluxDB writing are implemented in `main.py`. The app
keeps one BLE session alive, writes human-readable InfluxDB fields and tags,
retries one BLE reconnect after a measurement failure, and raises after a
lifetime exception threshold so Supervisor can restart it.

Supervisor deployment assets are grouped under `supervisor/`, following the
layout used by the lab's newer `nut`, `multivisor`, and `koheron_ctl` relays.
`main.py` imports the logging functions explicitly from
`supervisor.supervisor_helper`.

Root `Startup_bash` and `Startup.ps1` are the Linux and Windows launch wrappers
used by their respective Supervisor templates. They are copied from the lab's
generic Windows Supervisor and NUT launch templates: both resolve the project
directory and run the prepared local `.venv` directly without invoking `uv`.
The Bash wrapper uses `exec` for clean signal propagation. These standardized
service launchers do not forward collector arguments; interactive one-shot and
alternate-settings commands continue to use `uv run` explicitly.

## Important Current Context

- On 2026-07-17 the user returned within Bluetooth range and explicitly resumed
  live testing. Confirm proximity again in a later session before interpreting a
  missing advertisement as a regression.
- The refactored client, discovery CLI, identity reads, and one-shot measurement
  are now live verified on Windows with Bleak `3.0.2`.
- On 2026-07-17 the collector's `--once --dry-run` path was live verified against
  the purifier. It connected, built the final InfluxDB record, and did not load
  credentials, construct an InfluxDB client, or upload data.
- The Windows app template parses with `supervisor-win 4.7.0`. The current
  direct-venv startup scripts match their upstream lab templates and pass
  PowerShell/Bash syntax validation, but have not yet been registered or run
  under Supervisor.
- The Windows app template is the current formatting reference. It follows the
  lab supervisor repository's `conf.d/[APPNAME].conf.template`; the Linux
  supervisor layout is expected to converge on that format later.
- The existing `$HOME\Projects\supervisor\.venv` currently points to a missing
  uv-managed Python 3.11 interpreter. Run `uv sync` in that Supervisor
  repository before trying to register this app.
- This project's existing default `.venv` points to a missing user-level Python
  3.14 interpreter. Because the standardized wrappers now execute that exact
  environment directly, repair or recreate `.venv` before their first runtime
  test. Only source equivalence and syntax were verified in the current pass.
- The purifier's Wi-Fi state and IP address are not inputs to normal collection.

## Project Environment

This is a flat `uv` project initialized with `uv init --bare`:

- `pyproject.toml`: Python `>=3.11`, runtime `bleak>=0.22` and
  `influxdb-client>=1.50.0`, plus dev dependencies `pytest` and `ruff`.
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

The purifier rejects DPRL writes larger than 20 bytes. The client caps a request at
seven parameter codes, producing a 19-byte frame.

Status: 15 offline tests and Ruff pass. Handshake, seven-code identity batches, and
measurement reads are live verified against the physical purifier.

`reconnect()` reuses the cached verified target without requesting pairing or
identity again, then falls back to selector resolution if the direct attempt
fails. Serial/automatic resolution also avoids repeating optional identity reads
when opening the final persistent session.

### `query_device.py`

Thin discovery and identification CLI over `IQAirClient`. It:

1. Scans BLE advertisements.
2. Selects candidates with Bluetooth manufacturer company ID `0x060A` (IQAir AG).
3. Connects and requests pairing by default.
4. Checks the IQAir custom service and characteristics.
5. Requires a valid proprietary handshake before marking a device as verified.
6. Reads standard GATT device information when available.
7. Requests IQAir product, serial, firmware, hardware, and network identity fields.

Live-verified commands, which still require physical proximity:

```powershell
uv run query_device.py
uv run query_device.py --json
uv run query_device.py --address 10:97:BD:09:3A:D2
```

Status: live verified on 2026-07-17. The CLI identified the purifier, returned a
serial number and metadata, and exited with status 0.

Use `--scan-only` for advertisement-only discovery without connection or pairing.

### `iqair_test.py`

Thin one-shot reader over `IQAirClient`. It accepts a serial number or BLE MAC,
connects, requests PM1/PM2.5/PM10/fan RPM, validates the response, and emits JSON.

Known live command:

```powershell
uv run iqair_test.py --wifi-mac 10:97:BD:09:3A:D0 --pair
```

The library-backed CLI was live verified on 2026-07-17 by BLE MAC and by serial
number. The serial-number flow discovered identity, selected the matching device,
reconnected, and returned fan RPM `806` with PM1/PM2.5/PM10 values of `1`.

### `main.py`

Long-running BLE-to-InfluxDB collector for one configured purifier. It accepts a
serial number or BLE MAC in `settings.toml`, maintains one `IQAirClient` session,
and writes the default `IQAir` schema:

- Fields: `Fan speed [rpm]`, `PM1 [ug/m^3]`, `PM2.5 [ug/m^3]`, `PM10 [ug/m^3]`
- Tags: `Serial number`, `Product name`, `MAC address`, `Connection=Bluetooth LE`

Runtime settings are loaded from `settings.toml`; InfluxDB credentials are loaded
from `imaq_config/auth.toml`. Use `--once` for a single end-to-end write, or
`--once --dry-run` to exercise BLE and record conversion while making InfluxDB
access impossible.

Status: schema mapping, persistent-session reuse, one reconnect/retry, lifetime
exception accounting, Influx-only failure handling, and fatal cleanup are covered
offline. One real BLE dry-run returned fan RPM `807` and PM1/PM2.5/PM10 values of
`1`. The collector has not yet performed a live InfluxDB write.

## Known Device

- Product: IQAir HealthPro Plus XE
- Serial number: `050S-B009-T080-1`
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
- Supervisor launch wrappers execute the already prepared `.venv` directly;
  dependency synchronization is an explicit installation/update operation.
- Wi-Fi credentials are never queried. Opcode `4102` is intentionally excluded.
- Python sample names remain `snake_case`; only `main.py` maps them to the exact
  human-readable InfluxDB names.
- InfluxDB records use `Connection=Bluetooth LE` and no ambiguous `source` tag.
- Unresolved cycle failures accumulate for the process lifetime and are not reset
  by successful cycles.
- Investigation probes, local analysis tools, and APK evidence live under
  `.agents/`, separate from production code.
- Git history uses `main`; third-party mobile-app binaries are not committed.

## Next Work

1. Provide valid `imaq_config/auth.toml` and run the collector's upload-enabled
   `--once` mode when the user is ready to write the first InfluxDB record.
2. Recreate this project's `.venv` with `uv sync`, test `Startup.ps1`, repair the
   existing `supervisor-windows` environment, then copy and register the IQAir
   Windows template when background operation is desired.
3. Test repeated polling and forced BLE disconnect recovery over one persistent
   connection.
4. Correct remaining best-effort version/bitset decoders as more fields are live
   observed.
5. Add CI and a remote repository when the user chooses a hosting target.

## Known Risks and Gaps

- Version fields are decoded conservatively as raw hex plus numeric/text hints.
- The official mobile app may compete for the device's BLE connection.
- Pairing and address behavior has only been observed on Windows.
- PM field names use `ug/m^3`, but physical units/scaling have not been independently
  cross-checked against the purifier display or a reference instrument.
- The complete BLE-to-record dry-run is live verified, but the InfluxDB client and
  network write path remain offline-tested only.
- The APK disassembly was generated with one-off analysis code; exact disassembly
  reproduction commands were not preserved.
