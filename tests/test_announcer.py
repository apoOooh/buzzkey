import json

from whisper_key.announcer import MAX_QUEUED, TTL_SECONDS, Announcer, announcement, should_speak, spoken_name
from whisper_key.swarmterm_client import SwarmtermClient, SwarmtermEndpoint, SwarmtermError, TurnSubscription, _parse_turn

ENDPOINT = SwarmtermEndpoint(pipe=r"\\.\pipe\swarmterm-voice-x", token="t" * 64, pid=1)


def test_only_events_swarmterm_left_are_spoken_and_only_when_opted_in():
    assert should_speak(False, True)
    # Older Swarmterm without the field: buzzkey decides on its own.
    assert should_speak(None, True)
    # Swarmterm took it (says it or dropped it on purpose): never both voices.
    assert not should_speak(True, True)
    assert not should_speak(False, False)


def test_announcement_text_matches_swarmterm():
    assert announcement("Backend", "codex", "done") == "Backend: fertig."
    assert announcement("Backend", "codex", "needs_input") == "Backend wartet auf Eingabe."
    assert spoken_name("✳ Voice Agent · swarmterm") == "Voice Agent, swarmterm"
    assert spoken_name("Fix D:\\GN_AI\\repo build") == "Fix build"
    assert spoken_name("open https://example.com now") == "open now"
    assert announcement("C:\\Users\\x", "codex", "done") == "codex: fertig."
    assert announcement("", "", "done") == "Ein Agent: fertig."


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_queue_replaces_per_pane_caps_and_expires():
    clock = Clock()
    a = Announcer(busy=lambda: False, clock=clock)
    a.say("t1", "one")
    a.say("t2", "two")
    a.say("t1", "one again")
    assert a.next_item()[0] == "two"
    assert a.next_item()[0] == "one again"
    assert a.next_item() is None
    for i in range(MAX_QUEUED + 1):
        a.say(f"p{i}", f"n{i}")
    assert [a.next_item()[0] for _ in range(MAX_QUEUED)] == [f"n{i}" for i in range(1, MAX_QUEUED + 1)]
    a.say("old", "stale")
    clock.t += TTL_SECONDS
    assert a.next_item() is None


def test_recording_drops_the_queue_and_stop_ends_the_utterance():
    busy = {"on": False}
    a = Announcer(busy=lambda: busy["on"], clock=Clock())
    a.say("t1", "one")
    busy["on"] = True
    assert a.next_item() is None
    busy["on"] = False
    # Dropped, not replayed after the recording.
    assert a.next_item() is None
    a.say("t1", "two")
    text, gen = a.next_item()
    assert not a._cancelled(gen)
    a.stop()
    assert a._cancelled(gen)


def test_parses_tts_handled_and_tolerates_its_absence():
    base = {"terminalId": "t1", "paneName": "P", "agent": "codex", "kind": "done", "seq": 3}
    assert _parse_turn({**base, "ttsHandled": True}).tts_handled is True
    assert _parse_turn({**base, "ttsHandled": False}).tts_handled is False
    assert _parse_turn(base).tts_handled is None
    assert _parse_turn({**base, "ttsHandled": "yes"}).tts_handled is None


class ScriptedTransport:
    def __init__(self, lines):
        self.lines = list(lines)
        self.sent = []
        self.closed = False

    def send_line(self, line, timeout=None):
        self.sent.append(json.loads(line))

    def read_line(self, timeout=None):
        if not self.lines:
            raise SwarmtermError("connection closed")
        return json.dumps(self.lines.pop(0))

    def close(self):
        self.closed = True


def test_feed_negotiates_tts():
    transport = ScriptedTransport([{"v": 1, "ok": True, "proto": 2, "capabilities": ["turns", "tts"]},
                                   {"v": 1, "ok": True, "seq": 0, "ttsOwner": "swarmterm"}])
    sub = TurnSubscription(transport, ENDPOINT)
    assert sub.open() == 0
    assert sub.tts
    assert "tts" in transport.sent[0]["capabilities"]


def test_tts_stop_sends_hello_then_tts_stop_on_one_connection():
    made = []

    def factory(pipe, timeout):
        t = ScriptedTransport([{"v": 1, "ok": True, "capabilities": ["tts"]}, {"v": 1, "ok": True}])
        made.append(t)
        return t

    client = SwarmtermClient(enabled=True, app_ids=[], transport_factory=factory, discovery_dirs=[])
    assert client.tts_stop(ENDPOINT)
    (t,) = made
    assert [m["op"] for m in t.sent] == ["hello", "tts_stop"] and t.sent[0]["capabilities"] == ["tts"]
    assert t.closed


def test_tts_stop_reports_a_busy_voice():
    def factory(pipe, timeout):
        return ScriptedTransport([{"v": 1, "ok": True, "capabilities": ["tts"]},
                                  {"v": 1, "ok": False, "reason": "tts-busy"}])

    client = SwarmtermClient(enabled=True, app_ids=[], transport_factory=factory, discovery_dirs=[])
    assert not client.tts_stop(ENDPOINT)
