# Dictation into Swarmterm

When Swarmterm is the foreground app, dictation goes straight to the pane that had the keyboard when recording started — not through the clipboard and Ctrl+V. Clicking another pane while the text is transcribed does not move it.

- Text arrives as a draft in the prompt. The auto-Enter hotkey asks Swarmterm to submit; Swarmterm only does so when **Settings → Voice → Allow auto-Enter** is on.
- If Swarmterm refuses (pane closed or restarted, agent waiting for a permission answer, Settings or a dialog open), nothing is pasted anywhere else. The text stays on the clipboard and a tray notification says why.
- Other apps keep the normal paste or type delivery.

## How it connects

Swarmterm writes `voice-endpoint.json` (pipe name, per-launch token, process id) to `%LOCALAPPDATA%\<app id>\`. Buzzkey reads it only when the foreground window belongs to that process id, so the installed app and a dev build never cross. The pipe is restricted to your Windows user and accepts three operations: `begin`, `insert`, `cancel`.

## Configuration

```yaml
swarmterm:
    enabled: true
    app_ids:
        - "<Swarmterm app id>"
```
