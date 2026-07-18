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

### Refactored client, identity, and DPRL write limit

Date: 2026-07-17, while physically within BLE range on Windows with Bleak `3.0.2`.

Initial reproduction:

```powershell
uv run query_device.py --address 10:97:BD:09:3A:D2 --json --scan-seconds 5 --response-timeout 4
```

The advertisement was found, but the original 12-code identity batch produced a
29-byte DPRL frame and failed with ATT error `0x0D`, `Invalid Attribute Value
Length`. The CLI consequently returned status 1; VS Code displayed the normal
`raise SystemExit(main())` wrapper, but that was not the underlying failure.

Isolation command:

```powershell
uv run iqair_test.py --address 10:97:BD:09:3A:D2 --pair --skip-device-info --scan-seconds 5 --response-timeout 4 --include-raw-frames
```

Handshake and measurement succeeded, proving that the failure was limited to the
larger identity request. Fan RPM was `807`; PM1, PM2.5, and PM10 were all `1`.

Direct `IQAirClient.read_parameters()` diagnostics established the boundary:

- Seven codes: 19-byte DPRL frame, accepted, identity values returned.
- Eight codes: 21-byte DPRL frame, rejected with ATT error `0x0D`.
- Bleak reported MTU `517` and max write-without-response size `514`, so the
  purifier's characteristic value limit must still be respected independently.

After capping DPRL requests at seven codes and correcting response text/IPv4 byte
order, both commands succeeded with status 0:

```powershell
uv run query_device.py --address 10:97:BD:09:3A:D2 --json --scan-seconds 5 --response-timeout 4
uv run query_device.py
```

Observed:

- One candidate and one verified IQAir purifier.
- Serial number and product name returned successfully.
- Device IP decoded as `192.168.60.30`, netmask as `255.255.255.0`, and gateway as
  `192.168.60.1`.
- No device warnings or protocol errors remained.
- The final human-readable command exited with status 0.

Serial-number selection was then live verified:

```powershell
uv run iqair_test.py --identifier 050S-B009-T080-1 --pair --skip-device-info --scan-seconds 5 --response-timeout 4
```

The client discovered identity, selected the serial-number match, reconnected to
BLE address `10:97:BD:09:3A:D2`, and returned fan RPM `806` with all three PM values
equal to `1`. The command exited with status 0.

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

### DPRL limit and identity-decoder regression

Date: 2026-07-17.

Commands:

```powershell
uv run pytest -q
uv run ruff check iqair_client.py query_device.py iqair_test.py test_iqair_client.py
```

Observed:

- All 15 offline tests passed.
- Fixtures cover the seven-code/19-byte limit, rejection of oversized configured
  chunks, normal-order identity text, and network-order IPv4 decoding.
- Ruff passed all runtime and test files.

## Not Yet Verified

- Unreturned firmware, hardware, and certificate field encodings.
- Long-running BLE connection stability.
- Reconnect and backoff behavior.
- Periodic polling.
- InfluxDB writes.
- Operation on Linux or macOS.

### Collector implementation and offline validation

Date: 2026-07-17, current session. The executing machine was not used for a
live BLE or InfluxDB test in this validation pass.

Commands:

```powershell
$env:VIRTUAL_ENV=(Resolve-Path '.venv-codex').Path
$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache')
$env:UV_LINK_MODE='copy'
$env:TEMP=(Resolve-Path '.uv-cache').Path
$env:TMP=$env:TEMP
uv run --active pytest -q --basetemp=.uv-cache\pytest-temp -p no:cacheprovider
uv run --active ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor_helper.py
uv run --active python main.py --help
uv run --active python iqair_client.py --help
```

Observed:

- All 31 offline tests passed after the final implementation review.
- Ruff passed for the collector, BLE library, CLIs, helper, and tests.
- Collector and library help commands loaded without starting Bluetooth or
  InfluxDB operations.
- Regression coverage confirms that verified serial/automatic resolution does not
  repeat optional identity reads when opening the persistent session.
- Fatal lifetime-threshold failure closes both BLE and InfluxDB resources.
- The first test attempt encountered a machine-level permission error in the
  default pytest temp directory; rerunning with a project-local temporary path
  passed.
- A project-local `uv` Python 3.14.3 environment was used because the existing
  `.venv` pointed to an inaccessible user-level Python installation.

Not yet live-verified:

- `uv run python main.py --settings settings.toml --once` with the physical
  purifier and valid InfluxDB credentials.
- Long-running BLE session reuse and forced disconnect recovery.
- Actual InfluxDB field and tag names as displayed by the target Grafana setup.

### Collector live BLE dry-run

Date: 2026-07-17 (America/Chicago), while physically within Bluetooth range on
Windows. The user explicitly requested a real BLE test with InfluxDB upload
disabled.

Commands:

```powershell
uv run pytest -q
uv run ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor_helper.py
uv run python main.py --settings settings.toml --once --dry-run
```

Observed:

- All 33 offline tests passed and Ruff reported no errors before the live test.
- Dry-run mode did not load `imaq_config/auth.toml`, construct an InfluxDB client,
  or execute a network write.
- Serial-number discovery selected `050S-B009-T080-1` at BLE MAC
  `10:97:BD:09:3A:D2` with RSSI `-58 dBm` and verified the IQAir protocol.
- Product name returned as `HealthPro Plus B009-T`.
- The final, explicitly not-uploaded record used measurement `IQAir` and tags
  `Serial number`, `Product name`, `MAC address`, and
  `Connection=Bluetooth LE`.
- Fields were `Fan speed [rpm]=807`, `PM1 [ug/m^3]=1`,
  `PM2.5 [ug/m^3]=1`, and `PM10 [ug/m^3]=1`.
- Observation time was `2026-07-18T00:27:36.060448+00:00` and both collector
  resources shut down cleanly. Process exit status was `0`.

Still not live-verified:

- InfluxDB authentication and actual upload.
- Long-running session reuse and forced disconnect recovery.
- Linux/Supervisor operation.

### Supervisor asset relocation

Date: 2026-07-17 (America/Chicago). This was an offline repository-layout and
import validation; no BLE connection or InfluxDB access was attempted.

Repository comparison:

- Older lab relays including `ULE-Ion-pump-to-influxdb`,
  `LFI3751-to-influxdb`, and `sensorpush-to-influxdb` keep their Supervisor
  helper and configuration at repository root.
- Newer-layout relays `nut-to-influxdb`, `multivisor-to-influxdb`, and
  `koheron_ctl-to-influxdb` group those files under `supervisor/`.
- The IQAir project now follows the grouped layout and adds
  `supervisor/__init__.py` so the local helper import is unambiguous.

Commands:

```powershell
uv run pytest -q
uv run ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor
uv run python main.py --help
```

Observed:

- All 33 offline tests passed.
- Ruff reported no errors, including for the new `supervisor` package.
- `main.py` loaded `supervisor.supervisor_helper` and printed help without
  starting Bluetooth or InfluxDB operations.
- The root helper and configuration template were removed; README deployment
  commands now use `supervisor/iqair-to-influxdb.conf.template`.
- The first sandboxed pytest attempts could not access the machine's existing
  uv cache/Python installation. Running the same commands in the actual project
  uv environment succeeded.

### Linux startup wrapper

Date: 2026-07-17 (America/Chicago). The linked NUT `Startup_bash` was reviewed
from the current local checkout and adapted for this project's `uv` workflow. No
BLE connection or InfluxDB access was attempted.

Commands:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -n Startup_bash
& 'C:\Program Files\Git\bin\bash.exe' Startup_bash --help
# Run from C:\Users\Joon\Projects, outside the repository:
& 'C:\Program Files\Git\bin\bash.exe' '/c/Users/Joon/Projects/iqair-to-influxdb/Startup_bash' --help
uv run pytest -q
uv run ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor
git diff --check
```

Observed:

- Git Bash accepted the script syntax.
- Both help runs exited with status 0 and reached `main.py` through
  `uv run --frozen`; the second run resolved the repository correctly from a
  different working directory.
- The wrapper forwards CLI arguments and uses `exec`, with no interactive prompt
  or manual virtual-environment activation.
- All 33 offline tests passed, Ruff reported no errors, and the diff whitespace
  check passed.
- The Supervisor template invokes the wrapper through `/bin/bash` and supplies
  the absolute `UV_BIN`, so an executable bit on `Startup_bash` is not required.
- WSL Bash could not be used because no WSL distribution is installed; Git Bash
  provided the available Bash validation environment on this Windows machine.
- Linux/Supervisor execution itself remains unverified.

### Windows startup wrapper and Supervisor template

Date: 2026-07-17 (America/Chicago). The lab's generic Windows app template was
read from `SinclairQuantumLab/supervisor-windows` and adapted for the IQAir
collector. No BLE connection, InfluxDB access, Supervisor registration, or
process start was attempted.

Commands:

```powershell
$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path -LiteralPath Startup.ps1), [ref]$tokens, [ref]$errors) | Out-Null
# Run from C:\Users\Joon\Projects, outside the repository:
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -File 'C:\Users\Joon\Projects\iqair-to-influxdb\Startup.ps1' --help
uv run --python 3.11 --with supervisor-win==4.7.0 python -c "from pathlib import Path; import shutil,tempfile; from supervisor.options import ServerOptions; d=Path(tempfile.mkdtemp()); (d/'logs').mkdir(); c=d/'supervisord.conf'; s=Path(r'C:\Users\Joon\Projects\iqair-to-influxdb\supervisor\iqair-to-influxdb.conf.template.windows'); c.write_text('[supervisord]\n'+s.read_text()); o=ServerOptions(); o.realize(args=['-c',str(c)],progname='supervisord'); p=o.process_group_configs[0].process_configs[0]; print(p.command); print(p.directory); print(p.autorestart); shutil.rmtree(d)"
uv run pytest -q
uv run ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor
git diff --check
```

Observed:

- `Startup.ps1` passed PowerShell syntax parsing and Windows PowerShell 5.1
  executed it successfully from outside the repository.
- The wrapper resolved the repository, found `uv`, ran `uv run --frozen`,
  forwarded `--help`, and exited with status 0 without starting BLE or InfluxDB.
- `supervisor-win 4.7.0` parsed the Windows template successfully. It expanded
  the command and directory to this user's project, and interpreted
  `autorestart=unexpected` as `RestartWhenExitUnexpected`.
- All 33 offline tests passed, Ruff reported no errors, and the diff whitespace
  check passed.
- The existing `$HOME\Projects\supervisor\.venv` launcher currently references
  a missing uv-managed Python 3.11 interpreter. The same package was therefore
  validated in a clean one-shot uv Python 3.11 environment; the installed
  Supervisor project needs `uv sync --frozen` before operational use.
- An initial one-shot dependency attempt selected Python 3.14 and correctly
  failed because `supervisor-win 4.7.0` pins a `pywin32` release without Python
  3.14 wheels. Explicit Python 3.11 resolution succeeded.

### Windows template alignment

Date: 2026-07-17 (America/Chicago). The IQAir Windows template was aligned with
the current local `supervisor/conf.d/[APPNAME].conf.template`. No BLE connection,
InfluxDB access, Supervisor registration, or process start was attempted.

Changes and validation:

- Preserved `autorestart=unexpected` so the collector's lifetime exception
  threshold still hands recovery to Supervisor.
- Matched the Windows reference defaults for `startsecs=5`, stdout log rotation
  at 2 MB with two backups, and stderr rotation at 5 MB with two backups.
- Removed the redundant `PYTHONUNBUFFERED` template entry because `Startup.ps1`
  supplies it when absent.
- Parsed the result with `supervisor-win 4.7.0` under a project-local uv-managed
  Python 3.11.14 runtime.
- Supervisor expanded the command to `Startup.ps1`, resolved the project
  directory correctly, and read `startsecs` as `5`.
- `git diff --check` passed.

### Exact Windows reference and startup synchronization

Date: 2026-07-17 (America/Chicago). The exact current contents of
`SinclairQuantumLab/supervisor-windows/conf.d/[APPNAME].conf.template` were
reviewed again from the linked GitHub raw file and the local Supervisor checkout.
No BLE connection, InfluxDB access, Supervisor registration, or continuous
collector run was attempted.

Changes:

- Corrected `startretries` from `3` to the reference value `5`; all remaining
  process, restart, and log-rotation values match the Windows reference.
- Changed both OS wrappers to run `uv sync` once, followed by
  `uv run --no-sync python main.py`, so a restart synchronizes the environment
  without immediately performing the same check twice.
- Plain `uv sync` can update a stale lockfile after project metadata changes,
  but does not upgrade locked versions merely because new releases are
  available.

Commands:

```powershell
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path -LiteralPath Startup.ps1), [ref]$tokens, [ref]$errors) | Out-Null
& 'C:\Program Files\Git\bin\bash.exe' -n Startup_bash

$env:UV_CACHE_DIR = 'C:\Users\Joon\Projects\iqair-to-influxdb\.uv-cache'
$env:TEMP = $env:UV_CACHE_DIR
$env:TMP = $env:UV_CACHE_DIR
uv run --offline --isolated --no-project --python 'C:\Users\Joon\Projects\iqair-to-influxdb\.uv-python\cpython-3.11.14-windows-x86_64-none\python.exe' --with supervisor-win==4.7.0 python -c "from pathlib import Path; import shutil,tempfile; from supervisor.options import ServerOptions; root=Path(tempfile.mkdtemp(dir=r'C:\Users\Joon\Projects\iqair-to-influxdb\.uv-cache')); (root/'logs').mkdir(); source=Path(r'C:\Users\Joon\Projects\iqair-to-influxdb\supervisor\iqair-to-influxdb.conf.template.windows'); conf=root/'supervisord.conf'; conf.write_text('[supervisord]\n'+source.read_text(encoding='utf-8'), encoding='utf-8'); options=ServerOptions(); options.realize(args=['-c',str(conf)], progname='supervisord'); process=options.process_group_configs[0].process_configs[0]; print(process.startsecs, process.startretries, process.autorestart, process.stdout_logfile_maxbytes, process.stdout_logfile_backups, process.stderr_logfile_maxbytes, process.stderr_logfile_backups); shutil.rmtree(root)"

$env:UV_BIN = (Get-Command uv).Source
$env:UV_CACHE_DIR = (Resolve-Path -LiteralPath .uv-cache).Path
$env:UV_LINK_MODE = 'copy'
$env:UV_PYTHON = 'C:\Users\Joon\Projects\iqair-to-influxdb\.uv-python\cpython-3.11.14-windows-x86_64-none\python.exe'
$env:UV_PROJECT_ENVIRONMENT = (Join-Path (Resolve-Path -LiteralPath .uv-cache).Path 'wrapper-venv')
$env:UV_NO_PROGRESS = '1'
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -File (Resolve-Path -LiteralPath Startup.ps1).Path --help

$env:UV_BIN = 'C:\Windows\System32\where.exe'
& 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -ExecutionPolicy Bypass -File (Resolve-Path -LiteralPath Startup.ps1).Path --help

uv run --no-sync pytest -q --basetemp=.uv-cache\pytest-final -p no:cacheprovider
uv run --no-sync ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor
git diff --check
```

Observed:

- PowerShell AST parsing and Git Bash syntax validation passed.
- `supervisor-win 4.7.0` expanded the expected command and directory, read
  `startsecs=5` and `startretries=5`, interpreted `autorestart=unexpected`, and
  read stdout rotation as 2 MB/two backups and stderr as 5 MB/two backups.
- The Windows wrapper downloaded missing locked packages into the isolated
  validation environment, completed `uv sync`, then reached `main.py --help`
  through `uv run --no-sync` with exit status 0. `uv.lock` was not modified.
- A simulated native-command sync failure produced a concise stderr message and
  preserved exit status 1 without a PowerShell `Write-Error` stack trace.
- The first default-environment wrapper attempt exposed a pre-existing broken
  `.venv` whose interpreter points to a missing user-level Python 3.14 install.
  The generated environment was not removed or replaced during this task.
- All 33 offline tests passed with a project-local pytest base directory. Ruff
  and `git diff --check` also passed. An initial unqualified pytest run hit the
  sandbox-inaccessible Windows user temp directory; it was an environment setup
  error rather than a test failure.

### Direct-venv upstream startup templates

Date: 2026-07-17 (America/Chicago). The startup policy was changed at the user's
request to match established lab templates exactly. No BLE connection, InfluxDB
access, Supervisor registration, or collector runtime was attempted.

Sources:

- `Startup.ps1`:
  `https://github.com/SinclairQuantumLab/supervisor-windows/blob/main/python/Startup.ps1`
- `Startup_bash`:
  `https://github.com/SinclairQuantumLab/nut-to-influxdb/blob/main/Startup_bash`
- The linked Pico TC-08 `Startup.ps1` was also reviewed. Its device-specific
  serial-number parsing, terminal layout, and interactive error pause were not
  suitable for the generic IQAir Supervisor launcher.

Changes:

- Replaced both custom uv-based wrappers with the upstream files without local
  application logic.
- Both wrappers now execute the prepared `.venv` directly and never invoke `uv`.
- Removed the now-unused Linux Supervisor `UV_BIN` environment variable.
- Kept `uv sync` as an explicit installation/update step in the user manual.

Commands:

```powershell
# Raw GitHub contents and local files were normalized to LF and compared with -cne.
Invoke-RestMethod -Uri 'https://raw.githubusercontent.com/SinclairQuantumLab/supervisor-windows/main/python/Startup.ps1'
Invoke-RestMethod -Uri 'https://raw.githubusercontent.com/SinclairQuantumLab/nut-to-influxdb/main/Startup_bash'

$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path -LiteralPath Startup.ps1), [ref]$tokens, [ref]$errors) | Out-Null
& 'C:\Program Files\Git\bin\bash.exe' -n Startup_bash

uv run --no-sync pytest -q --basetemp=.uv-cache\pytest-direct-venv -p no:cacheprovider
uv run --no-sync ruff check iqair_client.py main.py query_device.py iqair_test.py test_iqair_client.py test_main.py supervisor
git diff --check
```

Observed:

- Both local startup scripts matched their upstream templates after normalizing
  line endings. One trailing space on the NUT template's standalone `echo` line
  was removed so the staged Git whitespace check remains clean.
- PowerShell AST parsing and Git Bash syntax validation passed.
- All 33 offline tests passed and Ruff reported no errors.
- `git diff --check` passed, and no current deployment file retains `UV_BIN` or
  the superseded `uv run --no-sync` wrapper behavior.
- The existing default `.venv` still points to a missing user-level Python 3.14
  interpreter. It was not deleted or recreated, so an end-to-end direct-wrapper
  runtime test remains pending.

## Current Live Context

On 2026-07-17 the user returned within Bluetooth range and resumed live tests. The
purifier advertised company ID `0x060A` at BLE address `10:97:BD:09:3A:D2`.
Physical proximity must be confirmed again in future sessions.

## Maintenance Format

For future meaningful tests, append:

- Local date and timezone.
- Whether the executing machine was physically near the purifier.
- Exact `uv` command.
- Relevant raw frame or checksum when protocol behavior changes.
- Outcome and any fields that remain inferred.
