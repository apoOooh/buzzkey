# Dictation into Swarmterm

When Swarmterm is the foreground app, dictation goes straight to the pane that had the keyboard when recording started — not through the clipboard and Ctrl+V. Clicking another pane while the text is transcribed does not move it.

- Text arrives as a draft in the prompt. The auto-Enter hotkey asks Swarmterm to submit; Swarmterm only does so when **Settings → Voice → Allow auto-Enter** is on.
- If Swarmterm refuses (pane closed or restarted, agent waiting for a permission answer, Settings or a dialog open), nothing is pasted anywhere else. The text stays on the clipboard and a tray notification says why.
- Other apps keep the normal paste or type delivery.

## Talking to a pane

Start with **"Pane 2: …"** or **"Pane Backend: …"** (also "An Backend: …" / "To Backend: …") and the dictation goes to that pane of the Swarmterm workspace in front instead of the one with the keyboard. Numbers can be digits or words; names are the pane names you gave in Swarmterm (double-click a pane title), else the pane titles. The prefix is removed from the text. End with **"… senden"** or **"… send"** to press Enter afterwards (still only with Allow auto-Enter, never in plain shells).

- Swarmterm decides which pane is meant. If a name fits several panes, or a "Pane …" address fits none, nothing is typed and the text stays on the clipboard.
- "An …" and "To …" are also ordinary words: if no pane has that name, the dictation goes to the focused pane unchanged.
- With the prompt enhancer on, only the part after the address is rewritten.
- Needs a Swarmterm with pane addressing (capability `address`); older versions get the text in the focused pane as before. Turn it off with `voice_addressing: false`.

## How it connects

Swarmterm writes `voice-endpoint.json` (pipe name, per-launch token, process id) to `%LOCALAPPDATA%\<app id>\`. Buzzkey reads it only when the foreground window belongs to that process id, so the installed app and a dev build never cross. The pipe is restricted to your Windows user and accepts three dictation operations: `begin` (optionally with a spoken pane `target`), `insert`, `cancel`.

## Configuration

```yaml
swarmterm:
    enabled: true
    voice_addressing: true
    app_ids:
        - "<Swarmterm app id>"
```
