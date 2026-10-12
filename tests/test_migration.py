import json

from ruamel.yaml import YAML

from whisper_key.migration import migrate_legacy_settings


def make_legacy(root):
    legacy = root / "whisperkey"
    legacy.mkdir()
    (legacy / "user_settings.yaml").write_text(
        "whisper:\n  model: qwen3-asr-1.7b\n  onnx_runtime: onnx-cuda\n  model_languages:\n    qwen3-asr-1.7b: de\n"
        "hotkey:\n  recording_mode: push_to_talk\n",
        encoding="utf-8",
    )
    (legacy / "commands.yaml").write_text("commands: []\n", encoding="utf-8")
    (legacy / "app.log").write_text("old log", encoding="utf-8")
    return legacy


def test_copies_settings_once_and_leaves_the_original(tmp_path):
    legacy = make_legacy(tmp_path)
    original = (legacy / "user_settings.yaml").read_text(encoding="utf-8")
    app_dir = tmp_path / "buzzkey"
    assert migrate_legacy_settings(app_dir) is True
    assert (app_dir / "commands.yaml").read_text(encoding="utf-8") == "commands: []\n"
    assert not (app_dir / "app.log").exists()
    assert (legacy / "user_settings.yaml").read_text(encoding="utf-8") == original
    marker = json.loads((app_dir / "migration.json").read_text(encoding="utf-8"))
    assert marker["version"] == 1 and marker["copied"] == ["user_settings.yaml", "commands.yaml"]


def test_switches_to_the_chosen_model_and_keeps_other_settings(tmp_path):
    make_legacy(tmp_path)
    app_dir = tmp_path / "buzzkey"
    migrate_legacy_settings(app_dir)
    data = YAML().load((app_dir / "user_settings.yaml").read_text(encoding="utf-8"))
    assert data["whisper"]["model"] == "large-v3-turbo"
    assert "onnx_runtime" not in data["whisper"]
    assert dict(data["whisper"]["model_languages"]) == {"large-v3-turbo": "de"}
    assert data["hotkey"]["recording_mode"] == "push_to_talk"


def test_runs_only_once(tmp_path):
    make_legacy(tmp_path)
    app_dir = tmp_path / "buzzkey"
    migrate_legacy_settings(app_dir)
    (app_dir / "user_settings.yaml").write_text("whisper:\n  model: tiny\n", encoding="utf-8")
    (app_dir / "migration.json").unlink()
    assert migrate_legacy_settings(app_dir) is False
    assert "tiny" in (app_dir / "user_settings.yaml").read_text(encoding="utf-8")


def test_without_legacy_folder_only_writes_the_marker(tmp_path):
    app_dir = tmp_path / "buzzkey"
    assert migrate_legacy_settings(app_dir) is False
    assert (app_dir / "migration.json").exists()
    assert not (app_dir / "user_settings.yaml").exists()


def test_buzzkey_never_installs_upstream_releases(monkeypatch):
    from whisper_key import update_checker
    called = []
    monkeypatch.setattr(update_checker, "fetch_latest_release", lambda: called.append(1) or ("99.0.0", "x"))
    update_checker.check_for_updates(config_manager=None)
    assert called == []
