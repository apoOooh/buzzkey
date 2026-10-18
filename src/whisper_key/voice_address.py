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
# Only as its own closing phrase after punctuation ("…, senden."): German puts
# the verb last, so "…die Mail senden" is part of the sentence, not a command.
_SEND = re.compile(r"\s*[,.;:!]\s*(?:und\s+|and\s+)?(?:senden|abschicken|send it|send)[\s.!]*$", re.IGNORECASE)


@dataclass
class Addressed:
    target: Optional[str]
    body: str
    send: bool = False
    # The spoken prefix and send phrase exactly as transcribed, so a rewritten
    # body can be put back without changing the rest (see compose).
    prefix: str = ""
    suffix: str = ""
    # "Pane …" can only be an address; "An …"/"To …" may be ordinary speech,
    # so an unknown pane there falls back to the pane that had the keyboard.
    explicit: bool = False


def parse_address(text: str) -> Addressed:
    match = _PREFIX.match(text)
    if not match:
        return Addressed(target=None, body=text)
    target = match.group("target").strip()
    body = match.group("body").rstrip()
    prefix = text[:match.start("body")]
    send, suffix = False, text[len(prefix) + len(body):]
    send_match = _SEND.search(body)
    if send_match and body[:send_match.start()].strip():
        suffix = body[send_match.start():] + suffix
        body, send = body[:send_match.start()], True
    if not body.strip():
        return Addressed(target=None, body=text)
    explicit = match.group("keyword").lower() in ("pane", "panel")
    return Addressed(target=target, body=body.strip(), send=send, explicit=explicit, prefix=prefix, suffix=suffix)


def compose(addressed: Addressed) -> str:
    """The original text with only the body replaced (prefix and send phrase verbatim)."""
    if addressed.target is None:
        return addressed.body
    return f"{addressed.prefix}{addressed.body}{addressed.suffix}"
