# buzzkey changelog

buzzkey is a fork of [whisper-key-local](https://github.com/SesioN/whisper-key-local). Versions are
`<upstream version>+bk.<n>`; upstream changes are listed in `CHANGELOG.md`. Release tags are
`buzzkey-<upstream version>-bk.<n>`.

## [Unreleased]

- **Upstream whisper-key 1.9.6** merged from the release tag `v1.9.6` (1.9.2 to 1.9.6:
  pre-roll toggle, beam size, Vulkan install fixes, return to original window). Version
  `1.9.6+bk.1`. Dictation into Swarmterm still goes over the pipe first; the new refocus
  only applies to the clipboard/paste path.
- **Announcements share one voice with Swarmterm** (BUZ-4, Swarmterm SWA-66): Swarmterm
  speaks "<pane>: fertig" itself. buzzkey negotiates `tts`, silences Swarmterm's voice before
  its microphone opens (`tts_stop`, answered on silence) and only speaks with the opt-in
  `swarmterm.fallback_voice` — and then only events Swarmterm left to it (`ttsHandled` false
  or missing). Status only; silent while recording or transcribing.

## [1.9.1+bk.5]

Based on whisper-key-local 1.9.1.

- **Swarmterm can install, quit and update buzzkey** (Settings → Voice): buzzkey keeps a
  connection to every running Swarmterm, so it shows as connected there and quits cleanly when
  Swarmterm asks (capability `quit`), e.g. before an update.
- **`buzzkey-console --probe-hardware --json`** prints the suggested model and runtime for this
  machine and exits; read-only, works next to a running buzzkey.
- **Signed checksums:** releases carry `SHA256SUMS.txt.minisig` (minisign, key id EFAC892903260581).

## [1.9.1+bk.4]

Based on whisper-key-local 1.9.1.

- **Startup no longer fails on a busy clipboard:** if another app holds the clipboard while
  buzzkey starts (typical at logon), buzzkey retries briefly and then starts anyway instead of
  exiting silently.

## [1.9.1+bk.3]

Based on whisper-key-local 1.9.1.

- **Listening indicator in Swarmterm:** with Swarmterm GN 1.10.0 or newer, the target pane shows a
  "Listening…" pill with a Stop button while you dictate. buzzkey negotiates the protocol on
  connect and holds one connection per dictation; with older Swarmterm versions it falls back to
  the previous behaviour. Stop in Swarmterm discards the dictation; any other cancellation keeps
  the text on the clipboard.

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
