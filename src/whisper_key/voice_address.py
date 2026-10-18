"""Spoken pane addressing for Swarmterm (BUZ-5).

"Pane 2: fix the login test" or "An Backend, bitte die Tests laufen lassen
senden" names the Swarmterm pane the dictation is for. Only the prefix and the
trailing send word are recognised here; Swarmterm resolves the name or number
and refuses anything ambiguous, so a misheard address never types into the
wrong pane.
"""
import re
from dataclasses import dataclass
from typing import Optional

MAX_TARGET_CHARS = 40

# Whisper writes the prefix with a colon, comma or dash, sometimes a period.
_PREFIX = re.compile(
    r"^\s*(?P<keyword>pane|panel|an|to)\s+(?P<target>[^\s:,.;–-][^:,;–]{0,%d}?)\s*[:,;.–-]\s*(?P<body>.+)$" % (MAX_TARGET_CHARS - 1),
    re.IGNORECASE | re.DOTALL,
)
_SEND = re.compile(r"[\s,.;:!-]*\b(?:senden|abschicken|send it|send)[\s.!]*$", re.IGNORECASE)


@dataclass
class Addressed:
    target: Optional[str]
    body: str
    send: bool = False
    # "Pane …" can only be an address; "An …"/"To …" may be ordinary speech,
    # so an unknown pane there falls back to the pane that had the keyboard.
    explicit: bool = False


def parse_address(text: str) -> Addressed:
    match = _PREFIX.match(text)
    if not match:
        return Addressed(target=None, body=text)
    target = match.group("target").strip()
    body = match.group("body").strip()
    send = False
    stripped = _SEND.sub("", body)
    if stripped != body and stripped.strip():
        body, send = stripped.strip(), True
    if not body:
        return Addressed(target=None, body=text)
    explicit = match.group("keyword").lower() in ("pane", "panel")
    return Addressed(target=target, body=body, send=send, explicit=explicit)


def compose(addressed: Addressed) -> str:
    """Inverse of parse_address for a rewritten body."""
    if addressed.target is None:
        return addressed.body
    keyword = "Pane" if addressed.explicit else "An"
    return f"{keyword} {addressed.target}: {addressed.body}" + (" senden" if addressed.send else "")
