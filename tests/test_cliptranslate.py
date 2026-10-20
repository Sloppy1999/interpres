from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tt_cliptranslate.config import AppConfig, save_target_language
from tt_cliptranslate.hotkey_listener import DoubleCopyDetector, GlobalListener
from tt_cliptranslate.local_translator import LocalTranslator
from tt_cliptranslate.translator import (
    CachedTranslator,
    DeepLTranslator,
    Translation,
    TranslationError,
)


def test_double_copy_timing_and_cooldown() -> None:
    detector = DoubleCopyDetector(500)
    assert not detector.press(1.0)
    assert detector.press(1.5)
    assert not detector.press(1.6)
    assert not detector.press(2.1)
    assert detector.press(2.4)
    assert not detector.press(3.0)
    assert not detector.press(3.51)


def test_double_copy_requires_two_distinct_presses(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter((1.0, 1.2))
    monkeypatch.setattr("tt_cliptranslate.hotkey_listener.time.monotonic", lambda: next(clock))
    triggered = Mock()
    listener = GlobalListener(500, triggered, Mock(), Mock(), Mock())
    ctrl = "Key.ctrl_l"
    c = SimpleNamespace(char="c", vk=67)
    listener.key_press(ctrl)
    listener.key_press(c)
    listener.key_press(c)  # Auto-repeat while C remains down.
    assert not triggered.called
    listener.key_release(c)
    listener.key_release(ctrl)
    listener.key_press(ctrl)
    listener.key_press(c)
    triggered.assert_called_once_with()


def test_deepl_auto_detect_and_free_endpoint() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "translations": [{"text": "Hello", "detected_source_language": "DE"}]
    }
    session = Mock()
    session.post.return_value = response
    translator = DeepLTranslator("secret:fx", session)
    assert translator.translate("Hallo", "EN") == Translation("Hello", "DE", "EN")
    args, kwargs = session.post.call_args
    assert args[0] == "https://api-free.deepl.com/v2/translate"
    assert kwargs["json"] == {"text": ["Hallo"], "target_lang": "EN"}
    assert "source_lang" not in kwargs["json"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(400, "language"), (403, "key"), (429, "busy"), (456, "quota")],
)
def test_deepl_errors_are_readable(status: int, expected: str) -> None:
    session = Mock()
    session.post.return_value = Mock(status_code=status)
    with pytest.raises(TranslationError, match=expected):
        DeepLTranslator("secret", session).translate("Hallo", "EN")


def test_cache_reuses_same_text_and_target() -> None:
    backend = Mock()
    backend.translate.return_value = Translation("Hello", "DE", "EN")
    cache = CachedTranslator(backend, capacity=2)
    assert cache.translate("Hallo", "EN").text == "Hello"
    assert cache.translate("Hallo", "en").text == "Hello"
    backend.translate.assert_called_once_with("Hallo", "EN")


def test_missing_key_and_timeout_are_readable() -> None:
    with pytest.raises(TranslationError, match="API key"):
        DeepLTranslator("").translate("Hallo", "EN")
    session = Mock()
    session.post.side_effect = requests.Timeout()
    with pytest.raises(TranslationError, match="timed out"):
        DeepLTranslator("secret", session).translate("Hallo", "EN")


def test_target_save_preserves_other_settings(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('target_language = "EN"\n# user note\nmax_text_length = 1234\n')
    save_target_language("FR", path)
    assert "# user note" in path.read_text()
    config = AppConfig.load(path)
    assert (config.target_language, config.max_text_length) == ("FR", 1234)


def test_local_translator_uses_installed_model_without_network() -> None:
    package = Mock()
    package.get_installed_packages.return_value = [
        SimpleNamespace(from_code="de", to_code="en")
    ]
    engine = Mock()
    engine.translate.return_value = "Good morning"
    detector = Mock()
    detector.classify.return_value = ("de", 0.9)
    http = Mock()
    local = LocalTranslator(False, package_api=package, translate_api=engine,
                            detector=detector, http=http)
    assert local.translate("Guten Morgen", "EN") == Translation("Good morning", "DE", "EN")
    engine.translate.assert_called_once_with("Guten Morgen", "de", "en")
    http.get.assert_not_called()


def test_local_translator_reports_missing_model_when_offline() -> None:
    package = Mock()
    package.get_installed_packages.return_value = []
    detector = Mock()
    detector.classify.return_value = ("de", 0.9)
    local = LocalTranslator(False, package_api=package, translate_api=Mock(), detector=detector)
    with pytest.raises(TranslationError, match="not installed"):
        local.translate("Guten Morgen", "EN")


def test_local_translator_can_use_installed_english_pivot() -> None:
    package = Mock()
    package.get_installed_packages.return_value = [
        SimpleNamespace(from_code="de", to_code="en"),
        SimpleNamespace(from_code="en", to_code="fr"),
    ]
    engine = Mock()
    engine.translate.side_effect = ["Good morning", "Bonjour"]
    detector = Mock()
    detector.classify.return_value = ("de", 0.9)
    local = LocalTranslator(False, package_api=package, translate_api=engine, detector=detector)
    assert local.translate("Guten Morgen", "FR").text == "Bonjour"
    assert engine.translate.call_count == 2
