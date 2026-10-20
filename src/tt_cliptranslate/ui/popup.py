"""A compact popup that can be shown without activating the source window."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..clipboard import copy_text
from ..translator import Translation


LANGUAGES = {
    "EN": "English",
    "EN-US": "English (US)",
    "EN-GB": "English (UK)",
    "DE": "German",
    "FR": "French",
    "ES": "Spanish",
    "IT": "Italian",
    "PT-BR": "Portuguese (BR)",
    "PT-PT": "Portuguese (PT)",
    "PT": "Portuguese",
    "NL": "Dutch",
    "PL": "Polish",
    "JA": "Japanese",
    "ZH": "Chinese",
    "KO": "Korean",
    "RU": "Russian",
    "UK": "Ukrainian",
}

LOCAL_LANGUAGES = {
    code: LANGUAGES[code]
    for code in ("EN", "DE", "FR", "ES", "IT", "PT", "NL", "PL", "JA", "ZH", "KO", "RU", "UK")
}


class TranslationPopup(QWidget):
    target_changed = pyqtSignal(str)

    def __init__(
        self, target: str, timeout_ms: int = 0, languages: dict[str, str] | None = None
    ) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(430)
        self._translated = ""
        self._timeout_ms = timeout_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(self)
        panel.setObjectName("panel")
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.source_label = QLabel("Detecting…")
        header.addWidget(self.source_label)
        header.addWidget(QLabel("→"))
        self.target_box = QComboBox()
        for code, name in (languages or LANGUAGES).items():
            self.target_box.addItem(name, code)
        if self.target_box.findData(target) < 0:
            self.target_box.addItem(target, target)
        self.target_box.setCurrentIndex(self.target_box.findData(target))
        self.target_box.currentIndexChanged.connect(self._on_target_changed)
        header.addWidget(self.target_box)
        header.addStretch()
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("closeButton")
        self.close_button.setToolTip("Close")
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.translation_box = QPlainTextEdit()
        self.translation_box.setReadOnly(True)
        self.translation_box.setPlaceholderText("Translation will appear here")
        self.translation_box.setFixedHeight(135)
        layout.addWidget(self.translation_box)

        self.original_toggle = QToolButton()
        self.original_toggle.setText("Original text")
        self.original_toggle.setCheckable(True)
        self.original_toggle.setChecked(False)
        self.original_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.original_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.original_toggle.toggled.connect(self._show_original)
        layout.addWidget(self.original_toggle)
        self.original_box = QPlainTextEdit()
        self.original_box.setReadOnly(True)
        self.original_box.setFixedHeight(95)
        self.original_box.hide()
        layout.addWidget(self.original_box)

        footer = QHBoxLayout()
        footer.addStretch()
        self.copy_button = QPushButton("Copy translation")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        footer.addWidget(self.copy_button)
        layout.addLayout(footer)

    def _on_target_changed(self) -> None:
        self.target_changed.emit(str(self.target_box.currentData()))

    def _show_original(self, checked: bool) -> None:
        self.original_box.setVisible(checked)
        arrow = Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        self.original_toggle.setArrowType(arrow)
        if self.isVisible():
            self._move_near_cursor()

    def set_target(self, code: str) -> None:
        index = self.target_box.findData(code)
        if index < 0:
            self.target_box.addItem(code, code)
            index = self.target_box.findData(code)
        self.target_box.blockSignals(True)
        self.target_box.setCurrentIndex(index)
        self.target_box.blockSignals(False)

    def show_loading(self, message: str, original: str = "") -> None:
        self.source_label.setText("Detecting…")
        self.translation_box.setPlainText(message)
        self.original_box.setPlainText(original)
        self.copy_button.setEnabled(False)
        self._translated = ""
        self.show_near_cursor()

    def show_translation(self, result: Translation, original: str) -> None:
        self.source_label.setText(LANGUAGES.get(result.source_language, result.source_language))
        self.translation_box.setPlainText(result.text)
        self.original_box.setPlainText(original)
        self._translated = result.text
        self.copy_button.setEnabled(bool(result.text))
        self._restart_timeout()

    def show_error(self, message: str) -> None:
        self.source_label.setText("Translation")
        self.translation_box.setPlainText(message)
        self._translated = ""
        self.copy_button.setEnabled(False)
        self._restart_timeout()

    def show_progress(self, message: str) -> None:
        self.translation_box.setPlainText(message)

    def show_near_cursor(self) -> None:
        self.adjustSize()
        self._move_near_cursor()
        self.show()
        self._restart_timeout()

    def _move_near_cursor(self) -> None:
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = max(area.left(), min(cursor.x() + 16, area.right() - self.width() + 1))
        y = max(area.top(), min(cursor.y() + 20, area.bottom() - self.height() + 1))
        self.move(QPoint(x, y))

    def _restart_timeout(self) -> None:
        self._timer.stop()
        if self._timeout_ms:
            self._timer.start(self._timeout_ms)

    def _copy(self) -> None:
        if self._translated:
            copy_text(self._translated)
            self.copy_button.setText("Copied")
            QTimer.singleShot(1300, lambda: self.copy_button.setText("Copy translation"))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def apply_theme(self, dark: bool) -> None:
        background = "#22252b" if dark else "#ffffff"
        foreground = "#f3f4f7" if dark else "#20242a"
        field = "#30343d" if dark else "#f5f6f8"
        border = "#4a515e" if dark else "#d9dde3"
        self.setStyleSheet(f"""
            QFrame#panel {{ background: {background}; color: {foreground};
                border: 1px solid {border}; border-radius: 12px; }}
            QLabel, QToolButton {{ color: {foreground}; border: none; background: transparent; }}
            QPlainTextEdit, QComboBox {{ background: {field}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 5px;
                selection-background-color: #376de5; }}
            QPushButton {{ background: #376de5; color: white; border: none;
                border-radius: 7px; padding: 7px 11px; }}
            QPushButton:disabled {{ background: {border}; }}
            QPushButton#closeButton {{ background: transparent; color: {foreground};
                font-size: 20px; padding: 0; }}
        """)
