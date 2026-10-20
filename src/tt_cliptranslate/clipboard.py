"""Clipboard reads and explicit copy action through Qt's native clipboard."""

from __future__ import annotations

import os
import subprocess

from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication


def read_text() -> str | None:
    if os.environ.get("WAYLAND_DISPLAY"):
        # Qt cannot read the Wayland clipboard while our window is unfocused.
        try:
            text = subprocess.run(
                ["wl-paste", "--no-newline", "--type", "text"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return text if text.strip() else None
    clipboard = QApplication.clipboard()
    mime = clipboard.mimeData(mode=QClipboard.Mode.Clipboard)
    if mime is None or not mime.hasText():
        return None
    text = mime.text()
    return text if text.strip() else None


def copy_text(text: str) -> None:
    QApplication.clipboard().setText(text, mode=QClipboard.Mode.Clipboard)
