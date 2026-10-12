import json
import queue
import threading

import pytest

from whisper_key.swarmterm_client import SwarmtermClient, SwarmtermError

# What Swarmterm 1.9.0 answers to hello: it has no such op.
LEGACY_HELLO = {"v": 1, "ok": False, "reason": "bad-request"}


class FakeTransport:
    def __init__(self, responses, sent, fail=None):
        self.responses = responses
        self.sent = sent
        self.fail = fail

    def __call__(self, pipe, timeout):
        self.pipe = pipe
        return self

    def send_line(self, line, timeout=None):
        if self.fail:
            raise SwarmtermError(self.fail)
        self.sent.append(json.loads(line))

    def read_line(self, timeout=None):
        return json.dumps(self.responses.pop(0))

    def close(self):
        pass


def write_endpoint(directory, pid=4242, token="t" * 64, pipe="\\\\.\\pipe\\swarmterm-voice-x"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "voice-endpoint.json").write_text(json.dumps({"version": 1, "pipe": pipe, "token": token, "pid": pid}))


def make_client(tmp_path, responses, sent, pid=4242, fail=None, enabled=True):
    # These tests cover the proto-1 path: the server refuses hello first.
    responses = [LEGACY_HELLO] + responses
    write_endpoint(tmp_path / "prod", pid=4242)
    write_endpoint(tmp_path / "dev", pid=777, token="d" * 64, pipe="\\\\.\\pipe\\swarmterm-voice-dev")
    return SwarmtermClient(
        enabled=enabled,
        app_ids=[],
        foreground_pid=lambda: pid,
        transport_factory=FakeTransport(responses, sent, fail),
        discovery_dirs=[tmp_path / "prod", tmp_path / "dev"],
    )


def test_begin_pins_only_when_swarmterm_is_foreground(tmp_path):
    sent = []
    client = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p1", "name": "pane"}], sent)
    target = client.begin()
    assert target.pin == "p1" and target.name == "pane"
    assert sent[0]["op"] == "hello" and sent[0]["proto"] == 2 and "indicator" in sent[0]["capabilities"]
    assert sent[1]["op"] == "begin" and sent[1]["token"] == "t" * 64 and sent[1]["v"] == 1


def test_matches_the_instance_by_foreground_pid(tmp_path):
    sent = []
    client = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p1"}], sent, pid=777)
    target = client.begin()
    assert target.endpoint.pipe.endswith("swarmterm-voice-dev")
    assert sent[1]["token"] == "d" * 64


def test_other_foreground_app_or_disabled_means_no_target(tmp_path):
    assert make_client(tmp_path, [], [], pid=1).begin() is None
    assert make_client(tmp_path, [], [], enabled=False).begin() is None


def test_refused_begin_keeps_swarmterm_as_target_without_pin(tmp_path):
    client = make_client(tmp_path, [{"v": 1, "ok": False, "reason": "no-focus"}], [])
    target = client.begin()
    assert target is not None and target.pin is None and target.error == "no-focus"
    assert client.insert(target, "hello", False) == (False, "no-focus")


def test_insert_sends_pin_once_and_reports_reasons(tmp_path):
    sent = []
    client = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p1"}, {"v": 1, "ok": True}], sent)
    target = client.begin()
    assert client.insert(target, "hello", True) == (True, None)
    assert sent[2]["op"] == "insert" and sent[2]["pin"] == "p1" and sent[2]["submit"] is True
    assert target.pin is None
    assert client.insert(target, "again", False) == (False, "no-target")

    gone = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p2"}, {"v": 1, "ok": False, "reason": "target-gone"}], [])
    assert gone.insert(gone.begin(), "x", False) == (False, "target-gone")


def test_transport_failures_never_raise(tmp_path):
    client = make_client(tmp_path, [], [], fail="timeout")
    target = client.begin()
    assert target.pin is None and target.error == "timeout"


def test_ignores_malformed_discovery_files(tmp_path):
    (tmp_path / "prod").mkdir()
    (tmp_path / "prod" / "voice-endpoint.json").write_text("{broken")
    write_endpoint(tmp_path / "evil", pipe="C:\\temp\\file")
    client = SwarmtermClient(
        enabled=True,
        app_ids=[],
        foreground_pid=lambda: 4242,
        transport_factory=FakeTransport([], []),
        discovery_dirs=[tmp_path / "prod", tmp_path / "evil"],
    )
    assert client.find_foreground_endpoint() is None


@pytest.mark.parametrize("reason", ["target-busy", "pin-invalid", "unknown-outcome"])
def test_insert_failure_reasons_pass_through(tmp_path, reason):
    client = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p"}, {"v": 1, "ok": False, "reason": reason}], [])
    assert client.insert(client.begin(), "x", False) == (False, reason)


# --- proto 2: held dictation connection ("indicator") -----------------------

class ScriptedConn:
    """One fake pipe connection. The handler answers each request with a
    list of lines; tests may also push lines or close from the server side."""

    def __init__(self, handler):
        self.handler = handler
        self.sent = []
        self.lines = queue.Queue()
        self.closed = False

    def send_line(self, line, timeout=None):
        if self.closed:
            raise SwarmtermError("write failed")
        message = json.loads(line)
        self.sent.append(message)
        for out in self.handler(message):
            self.lines.put(out)

    def read_line(self, timeout=None):
        try:
            item = self.lines.get(timeout=2)
        except queue.Empty:
            raise SwarmtermError("timeout")
        if item is None:
            raise SwarmtermError("connection closed")
        return json.dumps(item)

    def push(self, message):
        self.lines.put(message)

    def server_close(self):
        self.lines.put(None)

    def close(self):
        self.closed = True
        self.lines.put(None)


class ScriptedPipe:
    def __init__(self, handler):
        self.handler = handler
        self.conns = []

    def __call__(self, pipe, timeout):
        conn = ScriptedConn(self.handler)
        self.conns.append(conn)
        return conn


def modern(message):
    rid = message.get("requestId")
    op = message["op"]
    if op == "hello":
        return [{"v": 1, "requestId": rid, "ok": True, "proto": 2, "app": "swarmterm", "capabilities": ["indicator"]}]
    if op == "begin":
        return [{"v": 1, "requestId": rid, "ok": True, "pin": "p1", "name": "pane"}]
    if op == "insert":
        # A push for another pin arriving before the answer must not be
        # mistaken for it.
        return [{"v": 1, "event": "cancelled", "pin": "older", "reason": "superseded"},
                {"v": 1, "requestId": rid, "ok": True}]
    return [{"v": 1, "requestId": rid, "ok": True}]


def legacy(reason):
    def handler(message):
        if message["op"] == "hello":
            return [{"v": 1, "ok": False, "reason": reason}]
        if message["op"] == "begin":
            return [{"v": 1, "ok": True, "pin": "p1"}]
        return [{"v": 1, "ok": True}]
    return handler


def scripted_client(tmp_path, handler):
    write_endpoint(tmp_path / "prod", pid=4242)
    pipe = ScriptedPipe(handler)
    client = SwarmtermClient(enabled=True, app_ids=[], foreground_pid=lambda: 4242,
                             transport_factory=pipe, discovery_dirs=[tmp_path / "prod"])
    return client, pipe


def test_indicator_session_runs_hello_begin_insert_on_one_connection(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    target = client.begin()
    assert target.pin == "p1" and target.session is not None
    assert client.insert(target, "hello", False) == (True, None)
    assert len(pipe.conns) == 1
    assert [m["op"] for m in pipe.conns[0].sent] == ["hello", "begin", "insert"]
    assert pipe.conns[0].closed and target.session is None


@pytest.mark.parametrize("reason", ["bad-request", "unknown-op"])
def test_proto1_server_falls_back_on_new_connections_and_is_remembered(tmp_path, reason):
    client, pipe = scripted_client(tmp_path, legacy(reason))
    first = client.begin()
    assert first.pin == "p1" and first.session is None
    assert pipe.conns[0].closed and [m["op"] for m in pipe.conns[0].sent] == ["hello"]
    assert [m["op"] for m in pipe.conns[1].sent] == ["begin"]
    client.cancel(first)
    client.begin()
    # No second hello against the same Swarmterm instance.
    assert all(m["op"] != "hello" for c in pipe.conns[1:] for m in c.sent)


def test_user_stop_push_discards_without_insert(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    stopped = threading.Event()
    client.on_user_cancel = stopped.set
    target = client.begin()
    pipe.conns[0].push({"v": 1, "event": "cancelled", "pin": "p1", "reason": "user"})
    assert stopped.wait(2)
    assert client.insert(target, "hello", False) == (False, "cancelled-by-user")
    assert [m["op"] for m in pipe.conns[0].sent] == ["hello", "begin"]


def test_other_cancel_reasons_keep_the_clipboard_fallback(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    called = []
    client.on_user_cancel = lambda: called.append(True)
    target = client.begin()
    pipe.conns[0].push({"v": 1, "event": "cancelled", "pin": "p1", "reason": "target-gone"})
    for _ in range(100):
        if target.session.cancelled_reason:
            break
        threading.Event().wait(0.01)
    assert client.insert(target, "hello", False) == (False, "target-gone")
    assert called == []


def test_server_closing_the_held_connection_is_a_plain_failure(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    target = client.begin()
    pipe.conns[0].server_close()
    ok, reason = client.insert(target, "hello", False)
    assert ok is False and reason in ("connection closed", "write failed")


def test_cancel_on_a_held_session_is_tagged_and_closes(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    target = client.begin()
    client.cancel(target)
    cancel = pipe.conns[0].sent[-1]
    assert cancel["op"] == "cancel" and cancel["pin"] == "p1" and cancel["requestId"]
    assert pipe.conns[0].closed


def test_unsolicited_line_is_not_taken_as_the_next_answer(tmp_path):
    client, pipe = scripted_client(tmp_path, modern)
    target = client.begin()
    pipe.conns[0].push({"v": 1, "ok": False, "reason": "stray"})
    threading.Event().wait(0.1)
    assert client.insert(target, "hello", False) == (True, None)
