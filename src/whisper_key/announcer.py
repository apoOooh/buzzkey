"""Fallback voice for Swarmterm status announcements (BUZ-4).

Swarmterm speaks "<pane>: fertig" itself (SWA-66). buzzkey only steps in
when the user enabled the fallback voice AND Swarmterm did not take the
event (`ttsHandled` false, or missing on an older Swarmterm), so the two
voices never talk over each other. Same rules as Swarmterm's voice: status
only, no paths or URLs, at most three waiting, ten seconds TTL, silent
while buzzkey records or transcribes (half-duplex) and nothing replayed
afterwards.
"""

import logging
import queue
import threading
import time
from typing import Callable, Optional

MAX_QUEUED = 3
TTL_SECONDS = 10.0
MAX_NAME_CHARS = 40
# How often a running utterance checks for a stop.
POLL_SECONDS = 0.02
SEPARATORS = {"·", "|", "—", "-", "–"}


def _is_path_or_url(word: str) -> bool:
    return ("/" in word or "\\" in word or "://" in word or word.startswith("www.")
            or (len(word) >= 2 and word[1] == ":" and word[0].isascii() and word[0].isalpha()))


def spoken_name(pane_name: str) -> Optional[str]:
    """The pane title without status glyphs, paths and URLs, cut at a word."""
    label = "".join(c for c in pane_name if c.isprintable())
    while label and not label[0].isalnum():
        label = label[1:]
    out = ""
    for word in label.split():
        if _is_path_or_url(word):
            continue
        if word in SEPARATORS:
            if out and not out.endswith(","):
                out += ","
            continue
        if len(out) + len(word) + 1 > MAX_NAME_CHARS:
            break
        out = f"{out} {word}" if out else word
    out = out.rstrip(",").strip()
    return out or None


def announcement(pane_name: str, agent: str, kind: str) -> str:
    name = spoken_name(pane_name) or spoken_name(agent) or "Ein Agent"
    return f"{name} wartet auf Eingabe." if kind == "needs_input" else f"{name}: fertig."


def should_speak(tts_handled: Optional[bool], fallback_enabled: bool) -> bool:
    """Only events Swarmterm did not take; never both voices on one event."""
    return fallback_enabled and tts_handled is not True


class SapiVoice:
    """The system's SAPI voices through pywin32, created on the announcer
    thread (COM apartment). Prefers a German voice."""

    def __init__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        german = self._voice.GetVoices("Language=407")
        if german.Count > 0:
            self._voice.Voice = german.Item(0)

    def speak(self, text: str, cancelled: Callable[[], bool]):
        # 1 = async, 2 = purge before speak; an empty purge silences at once.
        self._voice.Speak(text, 1 | 2)
        while not self._voice.WaitUntilDone(int(POLL_SECONDS * 1000)):
            if cancelled():
                self._voice.Speak("", 2)
                return


class Announcer:
    def __init__(self, busy: Callable[[], bool], voice_factory: Callable[[], object] = SapiVoice,
                 clock: Callable[[], float] = time.monotonic):
        self._busy = busy
        self._voice_factory = voice_factory
        self._clock = clock
        self._items: list[tuple[str, str, float]] = []
        self._lock = threading.Lock()
        self._wake: queue.Queue = queue.Queue()
        self._gen = 0
        self.logger = logging.getLogger(__name__)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="announcer").start()

    def say(self, terminal_id: str, text: str):
        """A newer fact about the same pane replaces the older one."""
        with self._lock:
            self._items = [i for i in self._items if i[0] != terminal_id]
            self._items.append((terminal_id, text, self._clock()))
            del self._items[:-MAX_QUEUED]
        self._wake.put(None)

    def stop(self):
        """Drop what waits and end what plays (at its next poll)."""
        with self._lock:
            self._items.clear()
            self._gen += 1

    def next_item(self) -> Optional[tuple[str, int]]:
        """The next fresh text and the stop generation it starts under."""
        with self._lock:
            if self._busy():
                # Recording or transcribing: dropped, not held back.
                self._items.clear()
                return None
            now = self._clock()
            while self._items:
                _, text, at = self._items.pop(0)
                if now - at < TTL_SECONDS:
                    return text, self._gen
            return None

    def _cancelled(self, gen: int) -> bool:
        return self._gen != gen or self._busy()

    def _run(self):
        try:
            voice = self._voice_factory()
        except Exception as e:
            self.logger.error(f"Announcement voice unavailable: {e}")
            return
        while True:
            try:
                self._wake.get(timeout=0.25)
            except queue.Empty:
                pass
            while (item := self.next_item()) is not None:
                text, gen = item
                try:
                    voice.speak(text, lambda g=gen: self._cancelled(g))
                except Exception as e:
                    self.logger.error(f"Announcement failed: {e}")
