# Analysis Tools

This directory contains locally authored, one-off static-analysis helpers. They are
not part of Androguard, JADX, the Android SDK, or IQAir tooling.

`dex_xref_strings.py` reads DEX files from an APK and emits JSON lines for methods
that reference matching string constants. Example from the project root:

```powershell
uv run .agents/tools/dex_xref_strings.py `
  .agents/artifacts/xapk/com.airvisual.apk `
  "1b5ae7e4" "55340670"
```

The tool does not regenerate every checked-in disassembly artifact. See
`../PROVENANCE.md` for that limitation and for ownership details.
