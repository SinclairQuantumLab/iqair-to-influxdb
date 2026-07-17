# Reverse-Engineering Artifacts

This directory contains evidence used to understand the IQAir mobile app's BLE
protocol. It is not runtime input for `iqair_client.py`.

- `iqair_airvisual.xapk` and `xapk/` are third-party package inputs obtained from
  APKCombo, not an IQAir SDK or source release.
- `dex_strings/` and the `*_disasm.txt` files are locally generated static-analysis
  output.
- Generated evidence should not be edited by hand. Regenerated output should be
  recorded in `../PROVENANCE.md` with its command, tool version, and date.
- Review third-party licensing before publishing or redistributing the binaries.
- APK/XAPK files and the extracted app icon remain available locally but are
  intentionally ignored by Git. Text analysis output and package metadata are
  versioned for handoff.

See `../PROVENANCE.md` for the complete inventory, origin, checksums, and known
reproducibility gaps.
