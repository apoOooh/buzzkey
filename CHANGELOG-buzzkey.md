# buzzkey changelog

buzzkey is a fork of [whisper-key-local](https://github.com/SesioN/whisper-key-local). Versions are
`<upstream version>+bk.<n>`; upstream changes are listed in `CHANGELOG.md`. Release tags are
`buzzkey-<upstream version>-bk.<n>`.

## [1.9.1+bk.2]

Based on whisper-key-local 1.9.1.

- **Dictation into Swarmterm panes:** when Swarmterm GN (1.9.0 or newer) is the foreground app,
  dictation goes straight to the pane that had the keyboard when recording started, as a draft —
  not through the clipboard. A refused delivery is never pasted elsewhere; the text stays on the
  clipboard and the tray says why. See `docs/swarmterm.md`.
- **Own identity:** settings in `%APPDATA%\buzzkey`, copied once from `%APPDATA%\whisperkey` on
  first start (the original stays untouched); own single-instance lock and autostart entry.
  buzzkey refuses to start while whisper-key is running.
- **Updates come from this fork only:** buzzkey never installs whisper-key-local releases over
  itself, and its cleanup only removes older buzzkey environments.

## Download

`buzzkey.exe` (no console) or `buzzkey-console.exe`, plus `SHA256SUMS.txt`. The executables are not
Authenticode-signed; Windows SmartScreen may warn about an unknown publisher. The first start
downloads Python dependencies and can take a few minutes.
