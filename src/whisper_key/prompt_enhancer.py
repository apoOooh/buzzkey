"""Optional dictation rewrite into a concise prompt via a local LLM (BUZ-3).

Talks to an OpenAI-compatible chat endpoint (llama.cpp's llama-server) on
loopback only: dictated text must never leave the machine, so a non-loopback
URL disables the feature instead of being trusted. Every failure falls back
to the original transcription — enhancement is a nicety, never a reason to
lose a dictation.
"""
import json
import logging
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_SYSTEM_PROMPT = (
    "Rewrite the user's dictated text into a concise, clear prompt for an AI coding agent. "
    "Keep the original language, every requirement, name, path and number. "
    "Remove filler words and repetitions. Do not answer the prompt, do not add anything. "
    "Reply with the rewritten prompt only."
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

Poster = Callable[[str, bytes, float], bytes]


def is_loopback_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "http" and (parts.hostname or "").lower() in LOOPBACK_HOSTS


def _post(url: str, body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    # No proxy: a system proxy would otherwise carry loopback traffic off the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return response.read()


class PromptEnhancer:
    def __init__(self, enabled: bool = False, url: str = DEFAULT_URL, model: str = "",
                 timeout: float = 20.0, system_prompt: str = "", min_words: int = 4,
                 poster: Optional[Poster] = None):
        self.logger = logging.getLogger(__name__)
        self.url = url or DEFAULT_URL
        self.model = model or ""
        self.timeout = float(timeout)
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.min_words = int(min_words)
        self._post = poster or _post
        self.enabled = bool(enabled)
        if self.enabled and not is_loopback_url(self.url):
            self.logger.warning("Prompt enhancer disabled: url must be http on loopback, got %r", self.url)
            self.enabled = False

    def enhance(self, text: str) -> str:
        if not self.enabled or len(text.split()) < self.min_words:
            return text
        payload = {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        if self.model:
            payload["model"] = self.model
        try:
            raw = self._post(self.url, json.dumps(payload).encode("utf-8"), self.timeout)
            result = json.loads(raw)["choices"][0]["message"]["content"]
        except Exception as e:
            self.logger.warning("Prompt enhancer failed, keeping original text: %s", e)
            return text
        result = result.strip() if isinstance(result, str) else ""
        return result or text
