"""Provider interface, DeepL implementation, and a small thread-safe LRU."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import requests


@dataclass(frozen=True)
class Translation:
    text: str
    source_language: str
    target_language: str


class TranslationError(Exception):
    """A short error suitable for display in the popup."""


class Translator(Protocol):
    def translate(
        self, text: str, target_language: str, progress: Callable[[str], None] | None = None
    ) -> Translation: ...


class DeepLTranslator:
    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self._api_key = api_key.strip()
        # requests.post creates a session per call; a shared Session is not thread-safe.
        self._session = session or requests

    def translate(
        self, text: str, target_language: str, progress: Callable[[str], None] | None = None
    ) -> Translation:
        if not self._api_key:
            raise TranslationError("Add a DeepL API key in settings or DEEPL_API_KEY.")
        host = "api-free.deepl.com" if self._api_key.endswith(":fx") else "api.deepl.com"
        try:
            response = self._session.post(
                f"https://{host}/v2/translate",
                json={"text": [text], "target_lang": target_language.upper()},
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                timeout=(4, 12),
            )
        except requests.Timeout as error:
            raise TranslationError("Translation timed out. Please try again.") from error
        except requests.RequestException as error:
            raise TranslationError("Could not reach DeepL. Check your connection.") from error
        messages = {
            400: "DeepL cannot translate to that language.",
            403: "DeepL rejected the API key. Check your settings.",
            429: "DeepL is busy. Please try again shortly.",
            456: "DeepL character quota is exhausted.",
        }
        if response.status_code != 200:
            message = messages.get(response.status_code, "DeepL could not translate this text.")
            raise TranslationError(message)
        try:
            item = response.json()["translations"][0]
            return Translation(
                text=item["text"],
                source_language=item["detected_source_language"],
                target_language=target_language.upper(),
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
            raise TranslationError("DeepL returned an unexpected response.") from error


class CachedTranslator:
    def __init__(self, backend: Translator, capacity: int = 64) -> None:
        self.backend = backend
        self.capacity = capacity
        self._items: OrderedDict[tuple[str, str], Translation] = OrderedDict()
        self._lock = threading.Lock()

    def translate(
        self, text: str, target_language: str, progress: Callable[[str], None] | None = None
    ) -> Translation:
        key = (text, target_language.upper())
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        if progress is None:
            result = self.backend.translate(text, target_language)
        else:
            result = self.backend.translate(text, target_language, progress)
        with self._lock:
            self._items[key] = result
            self._items.move_to_end(key)
            if len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return result
