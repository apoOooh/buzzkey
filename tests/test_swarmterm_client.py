import json

import pytest

from whisper_key.swarmterm_client import SwarmtermClient, SwarmtermError


class FakeTransport:
    def __init__(self, responses, sent, fail=None):
        self.responses = responses
        self.sent = sent
        self.fail = fail

    def __call__(self, pipe, timeout):
        self.pipe = pipe
        return self

    def send_line(self, line):
        if self.fail:
            raise SwarmtermError(self.fail)
        self.sent.append(json.loads(line))

    def read_line(self):
        return json.dumps(self.responses.pop(0))

    def close(self):
        pass


def write_endpoint(directory, pid=4242, token="t" * 64, pipe="\\\\.\\pipe\\swarmterm-voice-x"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "voice-endpoint.json").write_text(json.dumps({"version": 1, "pipe": pipe, "token": token, "pid": pid}))


def make_client(tmp_path, responses, sent, pid=4242, fail=None, enabled=True):
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
    assert sent[0]["op"] == "begin" and sent[0]["token"] == "t" * 64 and sent[0]["v"] == 1


def test_matches_the_instance_by_foreground_pid(tmp_path):
    sent = []
    client = make_client(tmp_path, [{"v": 1, "ok": True, "pin": "p1"}], sent, pid=777)
    target = client.begin()
    assert target.endpoint.pipe.endswith("swarmterm-voice-dev")
    assert sent[0]["token"] == "d" * 64


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
    assert sent[1]["op"] == "insert" and sent[1]["pin"] == "p1" and sent[1]["submit"] is True
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
