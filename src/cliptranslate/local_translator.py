"""Offline Argos translation with on-demand installation of language models."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from .translator import Translation, TranslationError


INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
TARGET_ALIASES = {"EN-US": "en", "EN-GB": "en", "PT-BR": "pt", "PT-PT": "pt"}


class LocalTranslator:
    """Translates on-device; only missing model packs are fetched over HTTPS."""

    def __init__(
        self,
        auto_download_models: bool = True,
        device: str = "cpu",
        package_api: Any = None,
        translate_api: Any = None,
        detector: Any = None,
        http: Any = None,
    ) -> None:
        self.auto_download_models = auto_download_models
        self.device = device
        self._package = package_api
        self._translate = translate_api
        self._detector = detector
        self._http = http or requests
        self._index: dict[tuple[str, str], dict[str, Any]] | None = None
        self._manage_sbd = package_api is None
        # Argos maintains process-wide language/model caches. Serialize access to them.
        self._lock = threading.Lock()

    def _load(self) -> None:
        try:
            if self._package is None or self._translate is None:
                from argostranslate import package, settings, translate

                settings.device = self.device
                self._package = self._package or package
                self._translate = self._translate or translate
            if self._detector is None:
                import py3langid

                self._detector = py3langid
        except ImportError as error:
            raise TranslationError(
                "Local translation dependencies are missing. Run uv sync."
            ) from error

    @staticmethod
    def _target_code(language: str) -> str:
        return TARGET_ALIASES.get(language.upper(), language.lower())

    def translate(
        self, text: str, target_language: str, progress: Callable[[str], None] | None = None
    ) -> Translation:
        report = progress or (lambda _message: None)
        with self._lock:
            self._load()
            target = self._target_code(target_language)
            report("Detecting source language locally…")
            try:
                source, _score = self._detector.classify(text)
                source = source.lower()
            except Exception as error:
                raise TranslationError("Could not detect the source language.") from error
            if not source.isalpha() or len(source) not in (2, 3):
                raise TranslationError("Could not detect the source language.")
            if source == target:
                return Translation(text, source.upper(), target_language.upper())
            route = self._route(source, target, report)
            if self._manage_sbd:
                self._ensure_sentence_models(route, report)
            report("Translating locally…")
            try:
                output = text
                for from_code, to_code in route:
                    output = self._translate.translate(output, from_code, to_code)
                if not isinstance(output, str) or not output.strip():
                    raise ValueError("Empty translation")
                return Translation(output, source.upper(), target_language.upper())
            except Exception as error:
                if self.device == "cuda":
                    message = "Local GPU translation failed. Try local_device = \"cpu\"."
                else:
                    message = "Local translation failed. Try a shorter selection."
                raise TranslationError(message) from error

    def _installed_pairs(self) -> set[tuple[str, str]]:
        try:
            return {
                (item.from_code, item.to_code)
                for item in self._package.get_installed_packages()
                if getattr(item, "from_code", None) and getattr(item, "to_code", None)
            }
        except Exception as error:
            raise TranslationError("Could not read installed local models.") from error

    def _route(
        self, source: str, target: str, report: Callable[[str], None]
    ) -> list[tuple[str, str]]:
        installed = self._installed_pairs()
        direct = (source, target)
        pivot = [(source, "en"), ("en", target)]
        if direct in installed:
            return [direct]
        if source != "en" and target != "en" and all(pair in installed for pair in pivot):
            return pivot
        if not self.auto_download_models:
            raise TranslationError(
                "This language model is not installed. Enable auto_download_models in settings."
            )
        index = self._model_index(report)
        if direct in index:
            route = [direct]
        elif source != "en" and target != "en" and all(
            pair in installed or pair in index for pair in pivot
        ):
            route = pivot
        else:
            raise TranslationError("No local model is available for this language pair.")
        for pair in route:
            if pair not in installed:
                self._install_model(pair, index[pair], report)
        return route

    def _model_index(self, report: Callable[[str], None]) -> dict[tuple[str, str], dict[str, Any]]:
        if self._index is not None:
            return self._index
        report("Checking available local models…")
        try:
            response = self._http.get(INDEX_URL, timeout=(5, 15))
            response.raise_for_status()
            entries = response.json()
            if not isinstance(entries, list):
                raise ValueError("Invalid model index")
            self._index = {
                (item["from_code"], item["to_code"]): item
                for item in entries
                if isinstance(item, dict) and "from_code" in item and "to_code" in item
            }
            return self._index
        except (requests.RequestException, ValueError, KeyError) as error:
            raise TranslationError(
                "Could not check local models. Connect once to install a language pack."
            ) from error

    def _install_model(
        self,
        pair: tuple[str, str],
        metadata: dict[str, Any],
        report: Callable[[str], None],
    ) -> None:
        label = f"{pair[0].upper()} → {pair[1].upper()}"
        links = metadata.get("links", [])
        url = next(
            (link for link in links if isinstance(link, str) and link.startswith("https://")),
            None,
        )
        if url is None:
            raise TranslationError(f"No downloadable local model for {label}.")
        report(f"Downloading local model {label}…")
        try:
            with tempfile.TemporaryDirectory(prefix="cliptranslate-model-") as directory:
                path = Path(directory) / "model.argosmodel"
                self._fetch_file(url, path, 500 * 1024 * 1024)
                report(f"Installing local model {label}…")
                self._package.install_from_path(path)
        except (OSError, requests.RequestException, ValueError) as error:
            raise TranslationError(f"Could not download or install local model {label}.") from error
        except Exception as error:
            raise TranslationError(f"Could not install local model {label}.") from error

    def _fetch_file(self, url: str, path: Path, size_limit: int) -> None:
        total = 0
        with self._http.get(url, stream=True, timeout=(5, 30)) as response:
            response.raise_for_status()
            with path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    total += len(chunk)
                    if total > size_limit:
                        raise ValueError("Download exceeds size limit")
                    file.write(chunk)

    def _ensure_sentence_models(
        self, route: list[tuple[str, str]], report: Callable[[str], None]
    ) -> None:
        from minisbd import models

        installed = {
            (pkg.from_code, pkg.to_code): pkg
            for pkg in self._package.get_installed_packages()
            if getattr(pkg, "from_code", None) and getattr(pkg, "to_code", None)
        }
        mapping = {"zh": "zh-hans", "zt": "zh-hant", "pb": "pt"}
        for pair in route:
            pkg = installed.get(pair)
            if pkg is None:
                raise TranslationError("Local model installation was incomplete.")
            bundled = Path(pkg.package_path) / "minisbd"
            if bundled.exists() and any(bundled.glob("*.onnx")):
                continue
            code = mapping.get(pair[0], pair[0])
            if code not in models.MODELS:
                code = "en"
            filename = models.MODELS[code]
            destination = Path(models.cache_dir) / filename
            if destination.exists():
                continue
            if not self.auto_download_models:
                raise TranslationError(
                    "A sentence model is missing. Enable auto_download_models in settings."
                )
            report(f"Downloading local sentence model {code.upper()}…")
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
                    temporary = Path(directory) / filename
                    self._fetch_file(models.REPO_URL + filename, temporary, 50 * 1024 * 1024)
                    temporary.replace(destination)
            except (OSError, requests.RequestException, ValueError) as error:
                raise TranslationError("Could not download a local sentence model.") from error
