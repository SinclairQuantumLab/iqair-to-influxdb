# Agent Instructions

## Project Goal

Build a local IQAir HealthPro Plus XE data collector that reads the purifier over
Bluetooth Low Energy (BLE), polls measurements, and eventually writes normalized
samples to InfluxDB.

## Required Reading

Before changing code, read these files in order:

1. `.agents/HANDOFF.md` - current state, device facts, and next work.
2. `.agents/PROTOCOL.md` - verified BLE framing, UUIDs, and parameter codes.
3. `.agents/PROVENANCE.md` - origin and purpose of probes and artifacts.
4. `.agents/VALIDATION.md` - what has and has not been tested.

## Working Rules

- Use `uv` for every Python command. Do not introduce `pip` workflows.
- Device communication and measurement polling are BLE-only. The public IQAir API
  and the purifier's Wi-Fi connection are outside the collector data path.
- A machine must be physically within Bluetooth range to run live BLE tests. Being
  on the same LAN is neither required nor sufficient. Confirm the user's current
  location before treating a missing advertisement as a code regression.
- `iqair_client.py` is the reusable BLE library and the single source of truth for
  discovery, framing, connection ownership, identity, and measurement reads.
- `query_device.py` is a thin discovery and identification CLI over the library.
- `iqair_test.py` is a thin one-shot measurement CLI over the library.
- Do not duplicate protocol implementations back into the CLI files.
- Runtime and development dependencies are managed by `pyproject.toml` and
  `uv.lock`. Run `uv sync`, then use `uv run`; do not add duplicate PEP 723
  metadata to root runtime files.
- Standalone scripts under `.agents/probes/` may retain PEP 723 metadata because
  they are independent investigations with probe-specific dependencies.
- Files under `.agents/probes/` are investigation utilities, not production
  modules.
- Files under `.agents/tools/` are locally authored analysis helpers, not stable
  project APIs.
- Files under `.agents/artifacts/` are external binaries or generated
  reverse-engineering evidence. Do not edit generated evidence by hand.
- Never query, log, or add the Wi-Fi password opcode `WIFI_WIFIPSW` (`4102`).
- Keep raw protocol evidence available when changing parsing, but do not write raw
  frames to InfluxDB as fields or tags.
- This project is managed in Git on the `main` branch. Inspect `git status` before
  edits and never discard unrelated user changes.

## Verification Expectations

- Run offline parser and frame-fixture checks before any live BLE test.
- Run `uv run ruff check` on changed Python files.
- Run live commands only when the user confirms the executing machine is near the
  purifier and Bluetooth is available.
- Clearly distinguish historical live results from results obtained in the current
  session.

## Handoff Maintenance

After every material change:

- Update `.agents/HANDOFF.md` with current status, next action, and blockers.
- Update `.agents/PROTOCOL.md` when UUIDs, message framing, opcodes, decoding, or
  units change.
- Update `.agents/PROVENANCE.md` when adding, replacing, or generating artifacts,
  probes, or analysis tools.
- Update `.agents/VALIDATION.md` with the exact command, date, environment, and
  outcome of meaningful tests.
- Update this `AGENTS.md` only when durable project-wide instructions change.
