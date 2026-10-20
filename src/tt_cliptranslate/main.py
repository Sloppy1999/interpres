"""Qt application coordinator and tray lifecycle."""

from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtGui import QColor, QCursor, QDesktopServices, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import clipboard
from .config import AppConfig, config_dir, ensure_config, save_target_language
from .hotkey_listener import GlobalListener
from .local_translator import LocalTranslator
from .translator import CachedTranslator, DeepLTranslator, Translation, TranslationError, Translator
from .ui.popup import LANGUAGES, LOCAL_LANGUAGES, TranslationPopup


SOCKET_NAME = "interpres-copy"


def _setup_logging() -> None:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "cliptranslate.log", maxBytes=500_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("tt_cliptranslate").addHandler(handler)
    logging.getLogger("tt_cliptranslate").setLevel(logging.INFO)
    # PyQt6 aborts the process on an unhandled exception in a slot; log it instead.
    sys.excepthook = lambda *exc: logging.getLogger(__name__).error("Unhandled exception", exc_info=exc)


def _tray_icon() -> QIcon:
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#376de5"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 44, 44, 10, 10)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(25)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "文")
    painter.end()
    return QIcon(pixmap)


class HookSignals(QObject):
    triggered = pyqtSignal()
    clicked = pyqtSignal()
    escaped = pyqtSignal()
    error = pyqtSignal(str)
    translated = pyqtSignal(int, object, object)
    progress = pyqtSignal(int, str)


class AppController(QObject):
    def __init__(self, app: QApplication, config: AppConfig, translator: Translator) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.translator = translator
        self.target = config.target_language
        self.enabled = True
        self.generation = 0
        self.original = ""
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cliptranslate")
        self.pending: Future[Translation] | None = None
        self.signals = HookSignals()
        self.language_options = LOCAL_LANGUAGES if config.provider == "local" else LANGUAGES
        self.popup = TranslationPopup(self.target, config.popup_timeout_ms, self.language_options)
        self.popup.target_changed.connect(self.change_target)
        self.signals.triggered.connect(self.on_trigger)
        self.signals.clicked.connect(self.on_global_click)
        self.signals.escaped.connect(self.on_escape)
        self.signals.error.connect(self.on_hook_error)
        self.signals.translated.connect(self.on_translated)
        self.signals.progress.connect(self.on_progress)
        self._apply_theme()
        app.styleHints().colorSchemeChanged.connect(lambda _scheme: self._apply_theme())

        self.tray = QSystemTrayIcon(_tray_icon(), app)
        self.tray.setToolTip("ClipTranslate")
        menu = QMenu()
        self.enabled_action = menu.addAction("Enabled")
        self.enabled_action.setCheckable(True)
        self.enabled_action.setChecked(True)
        self.enabled_action.toggled.connect(self.set_enabled)
        target_menu = menu.addMenu("Target language")
        for code, name in self.language_options.items():
            action = target_menu.addAction(name)
            action.triggered.connect(lambda _checked=False, chosen=code: self.change_target(chosen))
        menu.addSeparator()
        menu.addAction("Open settings", self.open_settings)
        menu.addAction("Quit", app.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

        self.listener = GlobalListener(
            config.double_press_ms,
            self.signals.triggered.emit,
            self.signals.clicked.emit,
            self.signals.escaped.emit,
            self.signals.error.emit,
        )
        # Wayland hides global keys from pynput; the compositor reports them via --trigger.
        self.listener.count_copy = not os.environ.get("WAYLAND_DISPLAY")
        QLocalServer.removeServer(SOCKET_NAME)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_trigger_socket)
        self.server.listen(SOCKET_NAME)
        self.hook_watchdog = QTimer(self)
        self.hook_watchdog.timeout.connect(self._check_hooks)
        self.hook_watchdog.start(1000)
        self.listener.start()
        app.aboutToQuit.connect(self.shutdown)

    def _on_trigger_socket(self) -> None:
        conn = self.server.nextPendingConnection()
        conn.readyRead.connect(conn.readAll)
        conn.disconnected.connect(conn.deleteLater)
        if self.listener.detector.press(time.monotonic()):
            self.signals.triggered.emit()

    def _check_hooks(self) -> None:
        if not self.listener.is_healthy():
            self.on_hook_error("Global keyboard or mouse hook stopped.")

    def _apply_theme(self) -> None:
        dark = self.config.theme == "dark" or (
            self.config.theme == "system"
            and self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        self.popup.apply_theme(dark)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self.generation += 1
            self.popup.hide()
            self.original = ""
        elif not self.listener.is_healthy():
            self.listener.stop()
            self.listener = GlobalListener(
                self.config.double_press_ms,
                self.signals.triggered.emit,
                self.signals.clicked.emit,
                self.signals.escaped.emit,
                self.signals.error.emit,
            )
            self.hook_watchdog.start(1000)
            self.listener.start()

    def on_trigger(self) -> None:
        if not self.enabled:
            return
        self.generation += 1
        token = self.generation
        self.original = ""
        self.popup.show_loading("Reading copied text…")
        QTimer.singleShot(self.config.clipboard_delay_ms, lambda: self._read_clipboard(token))

    def _read_clipboard(self, token: int) -> None:
        if token != self.generation or not self.enabled:
            return
        try:
            text = clipboard.read_text()
        except Exception:
            logging.getLogger(__name__).warning("Clipboard read failed")
            self.popup.show_error("Could not read the clipboard.")
            return
        if not text:
            self.popup.show_error("No copied text found.")
            return
        if len(text) > self.config.max_text_length:
            message = (
                f"Copied text is too long ({len(text):,} characters; "
                f"limit {self.config.max_text_length:,})."
            )
            self.popup.show_error(message)
            return
        self.original = text
        self._translate()

    def _translate(self) -> None:
        if not self.original:
            return
        self.generation += 1
        token = self.generation
        if self.pending is not None:
            self.pending.cancel()
        self.popup.set_target(self.target)
        self.popup.show_loading("Translating…", self.original)
        report = lambda message: self.signals.progress.emit(token, message)
        future = self.executor.submit(self.translator.translate, self.original, self.target, report)
        self.pending = future

        def done(completed: Future[Translation]) -> None:
            try:
                result = completed.result()
                self.signals.translated.emit(token, result, None)
            except TranslationError as error:
                self.signals.translated.emit(token, None, str(error))
            except CancelledError:
                return
            except Exception as error:
                logging.getLogger(__name__).warning("Translation failed: %s", type(error).__name__)
                self.signals.translated.emit(token, None, "Translation failed. Please try again.")

        future.add_done_callback(done)

    def on_translated(self, token: int, result: object, error: object) -> None:
        if token != self.generation or not self.popup.isVisible():
            return
        if isinstance(result, Translation):
            self.popup.show_translation(result, self.original)
        else:
            self.popup.show_error(str(error))

    def on_progress(self, token: int, message: str) -> None:
        if token == self.generation and self.popup.isVisible():
            self.popup.show_progress(message)

    def change_target(self, language: str) -> None:
        if language == self.target:
            return
        self.target = language
        self.popup.set_target(language)
        try:
            save_target_language(language)
        except OSError:
            logging.getLogger(__name__).warning("Could not save target language")
            self.tray.showMessage(
                "ClipTranslate", "Target changed for this session; settings could not be saved."
            )
        if self.popup.isVisible() and self.original:
            self._translate()

    def on_global_click(self) -> None:
        if not self.popup.isVisible() or QApplication.activePopupWidget() is not None:
            return
        if not self.popup.frameGeometry().contains(QCursor.pos()):
            self.popup.hide()
            self.generation += 1

    def on_escape(self) -> None:
        if self.popup.isVisible():
            self.popup.hide()
            self.generation += 1

    def on_hook_error(self, message: str) -> None:
        self.hook_watchdog.stop()
        self.enabled_action.setChecked(False)
        logging.getLogger(__name__).warning("%s", message)
        self.tray.showMessage("ClipTranslate", message)

    def open_settings(self) -> None:
        path = ensure_config()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def shutdown(self) -> None:
        self.listener.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)


def main() -> int:
    _setup_logging()
    try:
        config = AppConfig.load()
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).error("Invalid configuration: %s", type(error).__name__)
        print("Invalid ClipTranslate configuration; check config.toml.", file=sys.stderr)
        return 2
    if config.provider not in ("local", "deepl"):
        print(f"Unsupported provider: {config.provider}", file=sys.stderr)
        return 2
    app = QApplication(sys.argv)
    app.setApplicationName("ClipTranslate")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("ClipTranslate requires a desktop system tray.", file=sys.stderr)
        return 2
    if config.provider == "local":
        translator = CachedTranslator(
            LocalTranslator(config.auto_download_models, config.local_device)
        )
    else:
        api_key = os.environ.get("DEEPL_API_KEY") or config.api_key
        translator = CachedTranslator(DeepLTranslator(api_key))
    controller = AppController(app, config, translator)
    _ = controller  # Keep hooks and tray alive for the Qt event loop.
    return app.exec()
