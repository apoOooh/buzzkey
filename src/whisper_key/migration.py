import json
import logging
import shutil
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

from .branding import DEFAULT_LANGUAGE, DEFAULT_MODEL, LEGACY_APP_DIR_NAME

MIGRATION_VERSION = 1
MARKER_FILE = "migration.json"
COPIED_FILES = ("user_settings.yaml", "commands.yaml")

logger = logging.getLogger(__name__)


def _select_model(settings_path: Path):
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(settings_path.read_text(encoding="utf-8")) or {}
    whisper = data.setdefault("whisper", {})
    whisper["model"] = DEFAULT_MODEL
    whisper.pop("onnx_runtime", None)
    whisper["model_languages"] = {DEFAULT_MODEL: DEFAULT_LANGUAGE}
    with settings_path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


def migrate_legacy_settings(app_dir: Path) -> bool:
    marker = app_dir / MARKER_FILE
    if marker.exists() or (app_dir / "user_settings.yaml").exists():
        return False
    legacy_dir = app_dir.parent / LEGACY_APP_DIR_NAME
    copied = []
    app_dir.mkdir(parents=True, exist_ok=True)
    for name in COPIED_FILES:
        source = legacy_dir / name
        if source.is_file():
            shutil.copy2(source, app_dir / name)
            copied.append(name)
    if "user_settings.yaml" in copied:
        try:
            _select_model(app_dir / "user_settings.yaml")
        except Exception as e:
            logger.warning(f"Could not set the default model in migrated settings: {e}")
    marker.write_text(json.dumps({
        "version": MIGRATION_VERSION,
        "from": LEGACY_APP_DIR_NAME,
        "date": date.today().isoformat(),
        "copied": copied,
    }, indent=2), encoding="utf-8")
    if copied:
        logger.info(f"Copied settings from {legacy_dir}: {', '.join(copied)}")
    return bool(copied)
