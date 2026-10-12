import pytest

from whisper_key import clipboard_manager as cm
from whisper_key.swarmterm_client import SwarmtermEndpoint, SwarmtermTarget


class FakeClient:
    def __init__(self, target, insert_result=(True, None)):
        self.target = target
        self.insert_result = insert_result
        self.inserted = []
        self.cancelled = []

    def begin(self):
        return self.target

    def insert(self, target, text, submit):
        self.inserted.append((target.pin, text, submit))
        return self.insert_result

    def cancel(self, target):
        self.cancelled.append(target.pin)


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(cm.pyperclip, "paste", lambda: "")
    monkeypatch.setattr(cm.pyperclip, "copy", lambda text: None)
    manager = cm.ClipboardManager(
        auto_paste=True,
        delivery_method="paste",
        paste_hotkey="ctrl+v",
        paste_pre_paste_delay=0,
        paste_preserve_clipboard=False,
        paste_clipboard_restore_delay=0,
        copy_to_clipboard=False,
        type_auto_enter_delay=0,
        type_auto_enter_delay_per_100_chars=0,
        macos_key_simulation_delay=0,
    )
    pasted = []
    monkeypatch.setattr(manager, "execute_delivery", lambda text: pasted.append(text) or True)
    monkeypatch.setattr(manager, "send_enter_key", lambda: pasted.append("ENTER") or True)
    copied = []
    monkeypatch.setattr(manager, "copy_text", lambda text: copied.append(text) or True)
    manager.pasted = pasted
    manager.copied = copied
    return manager


def target(pin="p1"):
    return SwarmtermTarget(endpoint=SwarmtermEndpoint(pipe="\\\\.\\pipe\\x", token="t", pid=1), pin=pin)


def test_pinned_dictation_goes_to_swarmterm_not_ctrl_v(manager):
    client = FakeClient(target())
    manager.swarmterm_client = client
    manager.begin_swarmterm_target()
    assert manager.deliver_transcription("hello", use_auto_enter=True) is True
    assert client.inserted == [("p1", "hello", True)]
    assert manager.pasted == []


def test_refused_dictation_never_falls_back_to_paste(manager):
    client = FakeClient(target(), insert_result=(False, "target-gone"))
    manager.swarmterm_client = client
    blocked = []
    manager.on_delivery_blocked = blocked.append
    manager.begin_swarmterm_target()
    assert manager.deliver_transcription("hello") is False
    assert manager.pasted == []
    assert manager.copied == ["hello"]
    assert "target-gone" in blocked[0]


def test_swarmterm_without_pin_still_never_pastes(manager):
    client = FakeClient(SwarmtermTarget(endpoint=target().endpoint, pin=None, error="no-focus"), insert_result=(False, "no-focus"))
    manager.swarmterm_client = client
    manager.begin_swarmterm_target()
    assert manager.deliver_transcription("hello") is False
    assert manager.pasted == []


def test_other_apps_keep_the_normal_paste(manager):
    manager.swarmterm_client = FakeClient(None)
    manager.begin_swarmterm_target()
    assert manager.deliver_transcription("hello") is True
    assert manager.pasted == ["hello"]


def test_unused_pin_is_cancelled_on_release(manager):
    client = FakeClient(target("p9"))
    manager.swarmterm_client = client
    manager.begin_swarmterm_target()
    manager.release_swarmterm_target()
    assert client.cancelled == ["p9"]
    assert manager.deliver_transcription("hello") is True
    assert manager.pasted == ["hello"]


def test_stop_in_swarmterm_discards_without_clipboard(manager):
    client = FakeClient(target(), insert_result=(False, "cancelled-by-user"))
    manager.swarmterm_client = client
    blocked = []
    manager.on_delivery_blocked = blocked.append
    manager.begin_swarmterm_target()
    assert manager.deliver_transcription("hello") is False
    assert manager.pasted == [] and manager.copied == [] and blocked == []
