# Investigation Probes

These are locally authored exploratory scripts retained for BLE and LAN diagnosis.
They are not official IQAir utilities, production modules, or a stable public API.

- Network probes document the earlier finding that the purifier exposes no useful
  inbound LAN data service.
- BLE probes were used to enumerate GATT, capture frames, and learn parameter
  behavior before that logic was consolidated into `../../iqair_client.py`.
- Run Python scripts through `uv`. Live BLE probes require the machine to be near
  the purifier and may connect or request pairing; inspect each script first.
- New production behavior belongs in `../../iqair_client.py`, not in these probes.

See `../PROVENANCE.md` for the role of each script.
