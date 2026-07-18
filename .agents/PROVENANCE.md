# File Provenance and Roles

None of the Python files under `.agents/probes/` or `.agents/tools/` are official
IQAir tools or standard downloaded utilities. They were authored locally during
this investigation.

## `.agents/artifacts/`

This directory mixes third-party binary input with locally generated analysis
output. It is evidence, not runtime application code.

### External input

| Path | Origin | Role |
| --- | --- | --- |
| `.agents/artifacts/iqair_airvisual.xapk` | Downloaded from APKCombo on 2026-06-29 | Archived AirVisual Android package used for static analysis |
| `.agents/artifacts/xapk/com.airvisual.apk` | Extracted from the XAPK | Base Android APK containing app code |
| `.agents/artifacts/xapk/config.*.apk` | Extracted from the XAPK | Android density-specific split resources |
| `.agents/artifacts/xapk/manifest.json` | Included in the XAPK | APKCombo package metadata |
| `.agents/artifacts/xapk/icon.png` | Included in the XAPK | App icon |
| `.agents/artifacts/xapk/APKComboInstaller.url` | Included in the XAPK | Link to APKCombo's generic installation guide |

Recorded package metadata:

- Android package: `com.airvisual`
- App name: AirVisual
- Version: `7.3.5-1.1`
- Version code: `2482`
- Download page used during the investigation:
  `https://apkcombo.com/iqair-airvisual-air-quality/com.airvisual/download/apk`

SHA-256 checksums:

```text
632C3672FCE5D0634B6E38F326BAE509D2992D42D750CC709F1762C1460800BE  .agents/artifacts/iqair_airvisual.xapk
635A640A30584184FE664093B9367345010082B6F6A65AC9061E0791FA0475DF  .agents/artifacts/xapk/com.airvisual.apk
```

APKCombo is a third-party distribution site. These binaries are not claimed to be
source code, an SDK, or a redistributable project dependency. Preserve them only as
reverse-engineering evidence and review licensing before publishing the repository.
The APK/XAPK files and extracted app icon are intentionally ignored by Git; their
documented checksums allow a local copy to be verified. Generated text analysis and
non-binary package metadata remain versioned.

### Locally generated analysis

| Path | Role |
| --- | --- |
| `.agents/artifacts/dex_strings/classes.dex.txt` | String dump from the base APK's first DEX file |
| `.agents/artifacts/dex_strings/classes2.dex.txt` | String dump from the base APK's second DEX file |
| `.agents/artifacts/w6_k_disasm.txt` | Disassembly of obfuscated app class `Lw6/k;`, including custom characteristic UUIDs and BLE handling |
| `.agents/artifacts/ha_y_disasm.txt` | Disassembly of obfuscated app class `Lha/y;`, including parameter-name-to-code mappings |
| `.agents/artifacts/ble_proto_classes_disasm.txt` | Disassembly of protocol-related classes, including message codes, CRC, and byte conversions |

These text files were generated locally with Androguard and one-off analysis code.
The exact disassembly-generation command was not preserved, so they are evidence
but are not fully reproducible from the checked-in tools alone.

## `.agents/probes/`

All probes are custom investigation scripts. They emit JSON lines and are retained
for diagnosis and protocol archaeology.

| Script | Role |
| --- | --- |
| `discover_lan.py` | Find local IPv4 networks and probe common TCP ports |
| `probe_host.py` | Probe one host for common TCP, HTTP, SMB, and MQTT candidates |
| `scan_tcp_ports.py` | Full or selected TCP port scan of one host |
| `ssdp_scan.py` | Browse SSDP/UPnP advertisements |
| `mdns_scan.py` | Browse selected mDNS service types |
| `identify_arp.py` | Read Windows ARP neighbors, resolve names, and identify MAC vendors using `manuf` |
| `ble_scan.py` | List BLE advertisements, RSSI, service UUIDs, and manufacturer data using `bleak` |
| `probe_ble_device.py` | Connect/pair and enumerate GATT services, characteristics, descriptors, and readable values |
| `ble_listen.py` | Subscribe to the IQAir notify characteristic without sending a request |
| `ble_read_params.py` | Experimental IQAir request/response client used to discover framing and parameter behavior |

These are not imported by `iqair_test.py` or `query_device.py` and should not be
treated as a stable public API.

## `.agents/tools/`

| Script | Origin | Role |
| --- | --- | --- |
| `dex_xref_strings.py` | Authored locally | Minimal custom DEX parser that finds methods referencing selected string constants inside an APK |

It is not part of Androguard, JADX, IQAir, or Android SDK tooling. It was created
because Java/JADX was unavailable in the original environment.

## Runtime and Deployment Files

| File | Role |
| --- | --- |
| `iqair_client.py` | Reusable async BLE library and opt-in scan/discover/sample demo; source of truth for discovery, connection ownership, protocol framing, identity, and measurements |
| `main.py` | Long-running collector; owns polling, reconnect/retry policy, lifetime exception accounting, InfluxDB record mapping, and cleanup |
| `iqair_test.py` | Thin one-shot PM and fan-RPM CLI over `IQAirClient` |
| `query_device.py` | Thin scan, pairing, verification, and metadata-listing CLI over `IQAirClient` |
| `test_iqair_client.py` | Offline protocol, selection, connection-guard, and sample-normalization tests |
| `test_main.py` | Offline collector configuration, schema, reconnect, exception-counter, and cleanup tests |
| `settings.toml.template` | Non-secret device, polling, timeout, and auth-path settings template |
| `Startup_bash` | Linux launch wrapper copied from `nut-to-influxdb/Startup_bash`; activates `.venv` and runs the collector directly |
| `Startup.ps1` | Windows launch wrapper copied from the generic `supervisor-windows/python/Startup.ps1`; runs `.venv\Scripts\python.exe` directly |
| `supervisor/__init__.py` | Marks the local Supervisor support directory as an explicit Python package |
| `supervisor/iqair-to-influxdb.conf.template` | Linux Supervisor configuration template for the long-running collector |
| `supervisor/iqair-to-influxdb.conf.template.windows` | Windows `supervisor-win` app configuration based on the lab's current generic `[APPNAME].conf.template`; this is the formatting reference for app templates |
| `supervisor/supervisor_helper.py` | User-provided timestamped logging helper used by `main.py` |
| `pyproject.toml` | `uv init --bare` project metadata and runtime/development dependency declarations |
| `uv.lock` | Generated, reproducible dependency lockfile |
| `README.md` | User-oriented installation, configuration, operation, schema, Supervisor, and troubleshooting manual |

The runtime files are locally authored and use the `uv` project environment. They
depend on `bleak` and `influxdb-client`; `pytest` and `ruff` are development
dependencies. Independent scripts under `.agents/probes/` retain PEP 723 metadata
for probe-specific dependencies. `supervisor/supervisor_helper.py` belongs to the
user and is retained as the collector's stdout/stderr logging boundary. Grouping
the helper and deployment template under `supervisor/` follows the newer layout
observed in the lab's `nut-to-influxdb`, `multivisor-to-influxdb`, and
`koheron_ctl-to-influxdb` repositories on 2026-07-17. `Startup_bash` is copied
from
`https://github.com/SinclairQuantumLab/nut-to-influxdb/blob/main/Startup_bash`.
`Startup.ps1` is copied from
`https://github.com/SinclairQuantumLab/supervisor-windows/blob/main/python/Startup.ps1`;
the Pico TC-08 launcher was also reviewed, but its Pico-specific serial-number,
terminal-layout, and pause behavior were intentionally not copied. Both IQAir
wrappers execute the prepared `.venv` directly and never invoke `uv`.
The Windows app configuration follows
`https://github.com/SinclairQuantumLab/supervisor-windows/blob/main/conf.d/%5BAPPNAME%5D.conf.template`.

## Generated Caches

- `__pycache__/` and `.agents/probes/__pycache__/`: Python bytecode generated by
  imports/execution.
- `.pytest_cache/`: generated by pytest.
- `.ruff_cache/`: generated by `uvx ruff check`.
- `.uv-cache/`: workspace-local uv cache created during sandboxed validation.
- `.venv/`: local environment generated from `pyproject.toml` and `uv.lock`.

They remain tool-managed caches rather than handoff material. They contain no
project logic or investigation evidence and may be recreated at the project root.
