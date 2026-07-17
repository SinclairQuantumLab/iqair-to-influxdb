# IQAir to InfluxDB

Local asynchronous BLE client for an IQAir HealthPro Plus XE purifier. Device
discovery, identity reads, connection ownership, and one-shot measurements are
implemented; periodic polling and InfluxDB writing are still planned work.

## Environment

The project uses `uv`, `pyproject.toml`, and `uv.lock` as its single dependency
source. Create or refresh the local `.venv` with:

```powershell
uv sync
```

Manual activation is optional. `uv run` automatically uses the project environment.

## Commands

```powershell
# Safe help check; performs no Bluetooth operation
uv run python .\query_device.py --help

# Scan, connect, verify, and list local IQAir devices
uv run python .\query_device.py

# Advertisement-only scan without connection or pairing
uv run python .\query_device.py --scan-only

# Direct library demonstration
uv run python .\iqair_client.py
uv run python .\iqair_client.py sample SERIAL-OR-BLE-MAC --pair

# Offline tests and lint
uv run pytest -q
uv run ruff check iqair_client.py query_device.py iqair_test.py test_iqair_client.py
```

Live commands require the executing machine to be physically within Bluetooth
range. The purifier's Wi-Fi connection is not used for measurement collection.
