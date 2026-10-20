"""Passive global key and mouse hooks; no Qt calls from hook threads."""

from __future__ import annotations

import time
from collections.abc import Callable


class DoubleCopyDetector:
    def __init__(self, interval_ms: int = 500) -> None:
        self.interval_s = interval_ms / 1000
        self.last_press: float | None = None
        self.cooldown_until = 0.0

    def press(self, when: float) -> bool:
        if when < self.cooldown_until:
            return False
        if self.last_press is not None and 0 < when - self.last_press <= self.interval_s:
            self.last_press = None
            self.cooldown_until = when + self.interval_s
            return True
        self.last_press = when
        return False

    def reset(self) -> None:
        self.last_press = None


class GlobalListener:
    def __init__(
        self,
        interval_ms: int,
        on_trigger: Callable[[], None],
        on_click: Callable[[], None],
        on_escape: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.detector = DoubleCopyDetector(interval_ms)
        self.on_trigger = on_trigger
        self.on_click = on_click
        self.on_escape = on_escape
        self.on_error = on_error
        self._ctrl_down: set[object] = set()
        self._copy_down = False
        self._keyboard = None
        self._mouse = None
        self.count_copy = True  # False when an external trigger supplies the copy presses

    @staticmethod
    def _is_copy(key: object) -> bool:
        char = getattr(key, "char", None)
        vk = getattr(key, "vk", None)
        return vk == 67 or (isinstance(char, str) and char.lower() in ("c", "\x03"))

    @staticmethod
    def _is_ctrl(key: object) -> bool:
        return str(key) in ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r")

    def key_press(self, key: object) -> None:
        if str(key) == "Key.esc":
            self.on_escape()
            return
        if self._is_ctrl(key):
            self._ctrl_down.add(str(key))
        elif self.count_copy and self._is_copy(key) and not self._copy_down:
            self._copy_down = True
            if self._ctrl_down and self.detector.press(time.monotonic()):
                self.on_trigger()
            elif not self._ctrl_down:
                self.detector.reset()

    def key_release(self, key: object) -> None:
        if self._is_ctrl(key):
            self._ctrl_down.discard(str(key))
        elif self._is_copy(key):
            self._copy_down = False

    def start(self) -> None:
        try:
            from pynput import keyboard, mouse

            self._keyboard = keyboard.Listener(
                on_press=self.key_press, on_release=self.key_release,
            )
            self._mouse = mouse.Listener(
                on_click=lambda _x, _y, _button, pressed: self.on_click() if pressed else None,
            )
            self._keyboard.start()
            self._mouse.start()
        except Exception as error:
            self.on_error(f"Could not start global hooks: {type(error).__name__}")

    def is_healthy(self) -> bool:
        return bool(
            self._keyboard is not None
            and self._mouse is not None
            and self._keyboard.is_alive()
            and self._mouse.is_alive()
        )

    def stop(self) -> None:
        if self._keyboard is not None:
            self._keyboard.stop()
        if self._mouse is not None:
            self._mouse.stop()
