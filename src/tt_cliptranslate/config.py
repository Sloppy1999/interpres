"""Small TOML configuration with platform-specific user directories."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ClipTranslate"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ClipTranslate"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cliptranslate"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass(frozen=True)
class AppConfig:
    target_language: str = "EN"
    double_press_ms: int = 500
    clipboard_delay_ms: int = 125
    max_text_length: int = 5000
    popup_timeout_ms: int = 0
    theme: str = "system"
    provider: str = "local"
    auto_download_models: bool = True
    local_device: str = "cpu"
    api_key: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or config_path()
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("Config must be a TOML table")
        defaults = cls()
        allowed = set(cls.__dataclass_fields__)
        values = {key: data.get(key, getattr(defaults, key)) for key in allowed}
        numeric_fields = (
            "double_press_ms", "clipboard_delay_ms", "max_text_length", "popup_timeout_ms"
        )
        for name in numeric_fields:
            value = values[name]
            if type(value) is not int or value < (0 if name == "popup_timeout_ms" else 1):
                raise ValueError(f"Invalid config value: {name}")
        if values["theme"] not in ("system", "light", "dark"):
            raise ValueError("theme must be system, light, or dark")
        if type(values["auto_download_models"]) is not bool:
            raise ValueError("auto_download_models must be true or false")
        if values["local_device"] not in ("cpu", "cuda"):
            raise ValueError("local_device must be cpu or cuda")
        for name in ("target_language", "provider", "local_device", "api_key"):
            if not isinstance(values[name], str):
                raise ValueError(f"Invalid config value: {name}")
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,3})?", values["target_language"]):
            raise ValueError("Invalid target_language")
        values["target_language"] = values["target_language"].upper()
        return cls(**values)


SAMPLE_CONFIG = '''# ClipTranslate settings. DEEPL_API_KEY takes precedence over api_key.
target_language = "EN"
double_press_ms = 500
clipboard_delay_ms = 125
max_text_length = 5000
popup_timeout_ms = 0 # 0 disables automatic closing
theme = "system" # system, light, or dark
provider = "local" # local or deepl
auto_download_models = true # only downloads missing language packs
local_device = "cpu" # set to "cuda" for a configured NVIDIA runtime
# api_key = ""
'''


def ensure_config(path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return path


def save_target_language(language: str, path: Path | None = None) -> None:
    """Update one flat key, preserving other user settings and comments."""
    path = ensure_config(path)
    if not re.fullmatch(r"[A-Z]{2,3}(?:-[A-Z]{2,3})?", language):
        raise ValueError("Invalid target language")
    content = path.read_text(encoding="utf-8")
    replacement = f'target_language = "{language}"'
    pattern = r"(?m)^target_language\s*=.*$"
    updated, count = re.subn(pattern, replacement, content, count=1)
    if not count:
        updated = content.rstrip("\n") + "\n" + replacement + "\n"
    path.write_text(updated, encoding="utf-8")
