import json

from whisper_key.swarmterm_client import SwarmtermEndpoint, SwarmtermError, TurnSubscription

ENDPOINT = SwarmtermEndpoint(pipe=r"\\.\pipe\swarmterm-voice-x", token="t" * 64, pid=1)
TIMEOUT = object()


class ScriptedTransport:
    """Replays answers; TIMEOUT raises like an idle read, running out = EOF."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.sent = []
        self.closed = False

    def send_line(self, line, timeout=None):
        self.sent.append(json.loads(line))

    def read_line(self, timeout=None):
        if not self.lines:
            raise SwarmtermError("connection closed")
        item = self.lines.pop(0)
        if item is TIMEOUT:
            raise SwarmtermError("timeout")
        return json.dumps(item)

    def close(self):
        self.closed = True


def turn(seq, kind="done"):
    return {"v": 1, "event": "turn", "terminalId": "t1", "paneName": "Refactor", "agent": "claude-code",
            "kind": kind, "excerpt": None, "seq": seq}


HELLO_OK = {"v": 1, "ok": True, "proto": 2, "capabilities": ["turns"]}


def test_subscribes_pings_on_idle_and_delivers_turns_in_order():
    transport = ScriptedTransport([HELLO_OK, {"v": 1, "ok": True, "seq": 4}, TIMEOUT, {"v": 1, "ok": True},
                                   turn(5), turn(6, "needs_input")])
    got = []
    sub = TurnSubscription(transport, ENDPOINT)
    assert sub.open() == 4
    sub.pump(got.append)
    hello, subscribe, ping = transport.sent
    assert hello["op"] == "hello" and hello["capabilities"] == ["turns"] and hello["token"] == "t" * 64
    assert subscribe["op"] == "subscribe" and ping["op"] == "ping"
    assert [(e.kind, e.pane_name, e.seq) for e in got] == [("done", "Refactor", 5), ("needs_input", "Refactor", 6)]
    assert transport.closed


def test_server_without_turns_is_not_subscribed():
    transport = ScriptedTransport([{"v": 1, "ok": True, "proto": 2, "capabilities": ["indicator"]}])
    assert TurnSubscription(transport, ENDPOINT).open() is None
    assert [m["op"] for m in transport.sent] == ["hello"] and transport.closed


def test_refused_subscribe_and_legacy_server_are_not_subscribed():
    legacy = ScriptedTransport([{"v": 1, "ok": False, "reason": "bad-request"}])
    assert TurnSubscription(legacy, ENDPOINT).open() is None
    busy = ScriptedTransport([HELLO_OK, {"v": 1, "ok": False, "reason": "busy"}])
    assert TurnSubscription(busy, ENDPOINT).open() is None and busy.closed


def test_unknown_events_and_malformed_turns_are_ignored():
    bad = dict(turn(2), kind="exploded")
    transport = ScriptedTransport([HELLO_OK, {"v": 1, "ok": True, "seq": 0},
                                   {"v": 1, "event": "future"}, bad, {"v": 1, "event": "turn"}, turn(3)])
    got = []
    sub = TurnSubscription(transport, ENDPOINT)
    sub.open()
    sub.pump(got.append)
    assert [e.seq for e in got] == [3]


def test_a_failing_handler_does_not_end_the_feed():
    transport = ScriptedTransport([HELLO_OK, {"v": 1, "ok": True, "seq": 0}, turn(1), turn(2)])
    seen = []

    def handler(e):
        seen.append(e.seq)
        raise RuntimeError("tts broke")

    sub = TurnSubscription(transport, ENDPOINT)
    sub.open()
    sub.pump(handler)
    assert seen == [1, 2]
