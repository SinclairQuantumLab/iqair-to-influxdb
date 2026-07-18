# IQAir to InfluxDB

Continuously read an IQAir HealthPro Plus XE over Bluetooth Low Energy (BLE) and
write its particulate measurements and fan speed to InfluxDB.

The app keeps one BLE session open while polling. It reconnects only after the
session is lost or a measurement fails. The purifier's Wi-Fi connection and the
public IQAir API are not used.

## Requirements

- A computer with Bluetooth that remains within range of the purifier
- [`uv`](https://docs.astral.sh/uv/) for Python and dependency management
- InfluxDB 2.x access details in `imaq_config/auth.toml` for upload-enabled runs
- Linux Supervisor or the lab's `supervisor-win` setup only if the app will run
  as a managed background service

The official phone app may compete for the purifier's BLE connection. Close it
while setting up or troubleshooting this collector.

## Quick installation

Clone the project, enter its directory, and synchronize the Python environment:

```bash
uv sync
```

For upload-enabled runs, place the lab's private `imaq_config` repository in this
project so that the following file exists. This step may be skipped for
`--dry-run`:

```text
imaq_config/auth.toml
```

The file must contain an InfluxDB table with `url`, `token`, `org`, and `bucket`:

```toml
[influxdb]
url = "https://influxdb.example.org"
token = "..."
org = "..."
bucket = "..."
```

Create the local settings file from `settings.toml.template`:

```bash
cp settings.toml.template settings.toml
```

PowerShell equivalent:

```powershell
Copy-Item .\settings.toml.template .\settings.toml
```

## Find and pair the purifier

Run discovery interactively on the same machine that will run the collector:

```bash
uv run python query_device.py
```

This scans nearby IQAir advertisements, connects to candidates, performs the
IQAir protocol handshake, and prints the verified serial number and MAC address.
Use the serial number in `settings.toml` when available:

```toml
device_identifier = "050S-B009-T080-1"
```

A MAC address such as `10:97:BD:09:3A:D2` is also accepted. The machine must be
physically within Bluetooth range; being on the same LAN is irrelevant.

## Configure polling

`settings.toml` supports these values:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `device_identifier` | required | Purifier serial number or BLE MAC address |
| `measurement` | `IQAir` | InfluxDB measurement name |
| `interval_s` | `30` | Seconds between polling-cycle starts |
| `reconnect_delay_s` | `10` | Wait before one BLE reconnect and retry |
| `exception_threshold` | `3` | Unresolved lifetime failures before exit |
| `pair_on_startup` | `true` | Allow OS pairing on the initial connection |
| `scan_seconds` | `10` | BLE discovery timeout |
| `connect_timeout_s` | `20` | BLE connection timeout |
| `response_timeout_s` | `6` | IQAir request/response timeout |
| `auth_path` | `imaq_config/auth.toml` | InfluxDB authentication file |

Relative `auth_path` values are resolved from the directory containing the
selected settings file.

## Test one sample

Before starting continuous collection, verify BLE and inspect the exact record
without loading InfluxDB credentials or sending any network write:

```bash
uv run python main.py --settings settings.toml --once --dry-run
```

Dry-run mode still performs real Bluetooth discovery, pairing, connection,
identity reads, measurement polling, and schema conversion. It does not create an
InfluxDB client, and the resulting record is printed with `not uploaded` in the
log.

After the dry-run succeeds, read and upload exactly one sample:

```bash
uv run python main.py --settings settings.toml --once
```

This command connects, reads the purifier, writes one InfluxDB record, closes both
clients, and exits. A failed read is reconnected and retried once before the
command exits with an error.

## Run continuously

```bash
uv run python main.py --settings settings.toml
```

The OS launch wrappers provide the same continuous launch from any working
directory. They bypass `uv` and execute the Python interpreter from the prepared
project `.venv` directly. Run `uv sync` explicitly after installation or a
dependency update before starting either wrapper.

Linux:

```bash
bash Startup_bash
```

Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Startup.ps1
```

These standardized Supervisor launchers start `main.py` with its default
`settings.toml`; use the `uv run python main.py ...` commands above for one-shot,
dry-run, or alternate-settings invocations.

Stop the foreground process with `Ctrl+C`. During normal operation the same BLE
connection is reused for every poll. A recovered disconnect is logged but does
not consume the lifetime exception allowance.

Failures that remain after the local retry and InfluxDB write failures increment
one lifetime counter. Successful polls do not reset it. At the configured
threshold the app exits with an error so Supervisor can restart the process.

## InfluxDB schema

The default measurement is `IQAir`.

Fields:

| Field | Value |
| --- | --- |
| `Fan speed [rpm]` | Purifier fan speed |
| `PM1 [ug/m^3]` | PM1 concentration |
| `PM2.5 [ug/m^3]` | PM2.5 concentration |
| `PM10 [ug/m^3]` | PM10 concentration |

Tags:

| Tag | Value |
| --- | --- |
| `Serial number` | Purifier serial number, when available |
| `Product name` | Product/model name, when available |
| `MAC address` | Connected purifier MAC address |
| `Connection` | `Bluetooth LE` |

The record timestamp is the UTC observation time produced by `IQAirClient`.
Missing values are omitted, and raw BLE frames are never written to InfluxDB.

Example Flux filters using names that contain spaces:

```flux
from(bucket: "YOUR_BUCKET")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "IQAir")
  |> filter(fn: (r) => r["Connection"] == "Bluetooth LE")
  |> filter(fn: (r) => r._field == "PM2.5 [ug/m^3]")
```

## Run with Supervisor

### Linux

First run the one-shot command successfully as the same Linux user that
Supervisor will use. This verifies Bluetooth permissions, pairing, settings, and
InfluxDB access before background operation.

Run `uv sync` in the project as the Supervisor user, then edit
`supervisor/iqair-to-influxdb.conf.template` and replace `USERNAME` and any
paths. Install the edited configuration. Supervisor invokes `Startup_bash`
through `/bin/bash`, so the file does not need an executable bit:

```bash
sudo cp supervisor/iqair-to-influxdb.conf.template /etc/supervisor/conf.d/iqair-to-influxdb.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status iqair-to-influxdb
```

Follow its output with:

```bash
sudo supervisorctl tail -f iqair-to-influxdb
```

The template uses `autorestart=unexpected`. Normal Supervisor stops remain
stopped; a fatal collector exception exits unexpectedly and is restarted.

### Windows

Install and start the lab's
[`supervisor-windows`](https://github.com/SinclairQuantumLab/supervisor-windows)
project first. It runs `supervisor-win` in the logged-in Windows user's session
through Task Scheduler.

From this project directory, verify one upload-enabled sample under that same
Windows account:

```powershell
uv run python .\main.py --settings .\settings.toml --once
```

Then copy the Windows app template into the Supervisor project's `conf.d` folder
and update Supervisor:

```powershell
Copy-Item -LiteralPath .\supervisor\iqair-to-influxdb.conf.template.windows -Destination "$HOME\Projects\supervisor\conf.d\iqair-to-influxdb.conf"
supervisorctl -u "<USERNAME>" -p "<PASSWORD>" update
supervisorctl -u "<USERNAME>" -p "<PASSWORD>" status
```

The template assumes both repositories are under `$HOME\Projects`. It starts
`Startup.ps1` with Windows PowerShell and writes logs beneath the Supervisor
repository's `logs` directory. Its layout follows the current
`supervisor-windows/conf.d/[APPNAME].conf.template` and is the formatting
reference for this app's Supervisor templates.

## Troubleshooting

- **No IQAir device found:** confirm Bluetooth is enabled, move the collector
  machine closer, and close the phone app. Wi-Fi connectivity does not help BLE
  discovery.
- **Pairing or permission error under Supervisor:** stop Supervisor and run
  `query_device.py` plus the one-shot command interactively as the configured
  service user first.
- **Windows Supervisor reports a missing Python executable:** run
  `uv sync` in `$HOME\Projects\supervisor`, then restart its scheduled
  task.
- **A startup wrapper cannot find the project venv:** run `uv sync` in this
  IQAir project as the same account that runs Supervisor.
- **Configuration error:** confirm `settings.toml` exists and its `auth_path`
  points to a readable TOML file containing `[influxdb]`.
- **InfluxDB outage:** the write failure consumes one lifetime exception but does
  not deliberately disconnect a healthy BLE session.
- **Repeated BLE failure:** the app tries the cached device first, then performs
  fresh discovery only if that direct reconnect fails.

## Developer's notes

`iqair_client.py` owns all IQAir discovery, GATT, framing, identity, connection,
and measurement behavior. `main.py` owns configuration, polling policy, the
human-readable InfluxDB schema, error accounting, and process lifecycle.

Internal sample attributes remain stable `snake_case` Python names. The InfluxDB
conversion boundary maps them to the exact display names documented above. Tag
names containing spaces must be accessed with bracket syntax such as
`r["Product name"]` in Flux.

The startup wrappers intentionally bypass `uv` and execute the prepared `.venv`
directly, following the lab Supervisor templates. This keeps service restarts
independent of dependency resolution and prevents a restart from modifying
`uv.lock`. Dependency synchronization is an explicit installation/update step.

Offline verification:

```bash
uv run pytest -q
uv run ruff check main.py iqair_client.py query_device.py iqair_test.py test_main.py test_iqair_client.py supervisor
```

Protocol evidence and live-validation history are maintained under `.agents/`.
