# Agent Handoff Index

This directory is the durable handoff and investigation archive for the IQAir BLE
collector. Nothing here is imported by runtime code.

## Handoff Files

- `HANDOFF.md`: frequently updated project state and next actions.
- `PROTOCOL.md`: BLE identification and proprietary message protocol.
- `PROVENANCE.md`: source, ownership, and purpose of every investigation folder.
- `VALIDATION.md`: historical and current test evidence.

## Development Archive

- `artifacts/`: third-party APK/XAPK inputs and generated reverse-engineering
  evidence. See `artifacts/README.md` before using or publishing it.
- `probes/`: locally authored, disposable network and BLE investigation scripts.
  See `probes/README.md`; these are not part of the supported client API.
- `tools/`: locally authored static-analysis helpers. See `tools/README.md`.

Detailed origin, ownership, and reproducibility notes live in `PROVENANCE.md`.
Runtime library, CLI, and test files deliberately remain at the repository root.

Future agents must read the root `AGENTS.md` first and keep these documents aligned
with code changes. Facts should be labeled as one of:

- **Live verified**: observed from the physical purifier.
- **Offline verified**: checked against stored frames or static code analysis.
- **Inferred**: likely from app analysis but not yet confirmed on the purifier.

Last documentation refresh: 2026-07-17 (America/Chicago).
