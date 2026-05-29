import sys
import os
import uuid
import subprocess
import ctypes
import shutil
from ctypes import wintypes
from typing import List, Optional

from PyQt6.QtCore import (
    QThread, pyqtSignal, Qt, QSettings, QPoint, QPropertyAnimation, 
    QEasingCurve, QEvent, QParallelAnimationGroup, QRect
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QMessageBox, QGraphicsDropShadowEffect, 
    QFrame, QLabel
)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QBrush, QPolygon, QIcon
import minecraft_launcher_lib
import minecraft_launcher_lib.runtime


class VersionFetchWorker(QThread):
    """Asynchronously fetches official Minecraft release versions from the Mojang API."""

    versions_fetched = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def run(self) -> None:
        try:
            version_list = minecraft_launcher_lib.utils.get_version_list()
            releases = [v["id"] for v in version_list if v["type"] == "release"]
            if not releases:
                raise ValueError("No release versions returned from API.")
            self.versions_fetched.emit(releases)
        except Exception as e:
            self.error_occurred.emit(str(e))


class JavaDownloadWorker(QThread):
    """Asynchronously downloads Mojang's official portable JVM runtime."""

    progress = pyqtSignal(str, int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, jvm_version: str, minecraft_dir: str):
        super().__init__()
        self.jvm_version = jvm_version
        self.minecraft_dir = minecraft_dir
        self._max_val = 0

    def run(self) -> None:
        try:
            def _set_status(text: str) -> None:
                self.progress.emit(text, -1)

            def _set_max(val: int) -> None:
                self._max_val = val

            def _set_progress(val: int) -> None:
                if self._max_val > 0:
                    percent = int((val / self._max_val) * 100)
                    self.progress.emit("Downloading Java...", percent)

            callbacks = {
                "setStatus": _set_status,
                "setProgress": _set_progress,
                "setMax": _set_max,
            }

            minecraft_launcher_lib.runtime.install_jvm_runtime(
                self.jvm_version, self.minecraft_dir, callback=callbacks
            )
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class LaunchWorker(QThread):
    """Handles runtime file verification, asset downloads, and execution of the client."""

    progress_updated = pyqtSignal(str, int)
    launch_success = pyqtSignal()
    game_exited = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, username: str, version: str, minecraft_dir: str):
        super().__init__()
        self.username = username
        self.version = version
        self.minecraft_dir = minecraft_dir
        self._max_val = 0

    def run(self) -> None:
        try:
            def _set_status(text: str) -> None:
                self.progress_updated.emit(text, -1)

            def _set_max(val: int) -> None:
                self._max_val = val

            def _set_progress(val: int) -> None:
                if self._max_val > 0:
                    percent = int((val / self._max_val) * 100)
                    self.progress_updated.emit("Installing...", percent)

            callbacks = {
                "setStatus": _set_status,
                "setProgress": _set_progress,
                "setMax": _set_max,
            }

            installed = [
                v["id"] for v in minecraft_launcher_lib.utils.get_installed_versions(
                    self.minecraft_dir
                )
            ]

            try:
                self.progress_updated.emit("Checking files...", 0)
                minecraft_launcher_lib.install.install_minecraft_version(
                    self.version, self.minecraft_dir, callback=callbacks
                )
            except Exception as net_err:
                if self.version in installed:
                    self.progress_updated.emit("Offline: Launching cached...", 100)
                else:
                    raise RuntimeError(
                        f"Failed to fetch assets for {self.version}.\n"
                        "Please verify your internet connection."
                    ) from net_err

            self.progress_updated.emit("Preparing launch...", 100)
            
            # Resolve the data path with camelCase formatting (.Vanta)
            if sys.platform == "win32":
                vanta_dir = os.path.join(os.environ.get("APPDATA", ""), ".Vanta")
            else:
                vanta_dir = os.path.expanduser("~/.Vanta")
                
            os.makedirs(vanta_dir, exist_ok=True)

            options = {
                "username": self.username,
                "uuid": str(uuid.uuid4()),
                "token": "",
                "launcherName": "Vanta",
                "launcherVersion": "1.0",
                "gameDirectory": vanta_dir
            }

            # If system Java is absent and the version doesn't bundle its own runtime,
            # we enforce the downloaded legacy portable runtime.
            if not shutil.which("java"):
                try:
                    runtime_info = minecraft_launcher_lib.runtime.get_version_runtime_information(
                        self.version, self.minecraft_dir
                    )
                except Exception:
                    runtime_info = None

                if runtime_info is None:
                    legacy_exec = minecraft_launcher_lib.runtime.get_executable_path("jre-legacy", self.minecraft_dir)
                    if legacy_exec:
                        options["executablePath"] = legacy_exec

            command = minecraft_launcher_lib.command.get_minecraft_command(
                self.version,
                self.minecraft_dir,
                options
            )

            self.progress_updated.emit("Launching...", 100)
            
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=vanta_dir,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self.launch_success.emit()
            process.wait()
            self.game_exited.emit()

        except FileNotFoundError:
            self.error_occurred.emit(
                "Java environment not found.\n\n"
                "Please ensure Java (OpenJDK 17 or 21 recommended) "
                "is installed and present in your system's PATH."
            )
        except Exception as e:
            self.error_occurred.emit(str(e))


def _generate_arrow_image() -> str:
    """Generates the combobox dropdown vector resource if missing."""
    temp_dir = os.path.join(os.path.expanduser("~"), ".mclaunch")
    os.makedirs(temp_dir, exist_ok=True)
    arrow_path = os.path.join(temp_dir, "arrow.png").replace("\\", "/")

    if os.path.exists(arrow_path):
        return arrow_path

    try:
        image = QImage(12, 8, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(1, 2), QPoint(11, 2), QPoint(6, 7)]))
        painter.end()

        image.save(arrow_path)
    except Exception as e:
        sys.stderr.write(f"Resource generation failure: {e}\n")

    return arrow_path


class MinecraftLauncher(QMainWindow):
    _FADE_DURATION = 250
    _EXPAND_DURATION = 350

    def __init__(self) -> None:
        super().__init__()
        self.minecraft_dir = minecraft_launcher_lib.utils.get_minecraft_directory()
        self.settings = QSettings("Vanta", "Preferences")
        self._drag_position = QPoint()
        self._is_closing = False
        
        self.setWindowOpacity(0.0)
        self._init_ui()
        self._load_settings()
        self._fetch_versions()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def _stop_animations(self) -> None:
        group = getattr(self, "_anim_group", None)
        if group is not None and group.state() == QParallelAnimationGroup.State.Running:
            group.stop()

    @staticmethod
    def _shrink_geometry(rect: QRect, factor: float = 0.92) -> QRect:
        w = int(rect.width() * factor)
        h = int(rect.height() * factor)
        x = rect.x() + (rect.width() - w) // 2
        y = rect.y() + (rect.height() - h) // 2
        return QRect(x, y, w, h)

    @staticmethod
    def _get_taskbar_geometry() -> Optional[QRect]:
        if sys.platform != "win32":
            return None
        try:
            hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if not hwnd:
                return None
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            return None

    def _fade_out_with_shrink(self, finish_callback, *, target_geo: Optional[QRect] = None, slide_down: bool = False) -> None:
        self._stop_animations()

        opacity = QPropertyAnimation(self, b"windowOpacity")
        opacity.setDuration(self._FADE_DURATION)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(0.0)
        opacity.setEasingCurve(QEasingCurve.Type.InCubic)

        geo = QPropertyAnimation(self, b"geometry")
        geo.setDuration(self._FADE_DURATION)
        geo.setStartValue(self.geometry())

        if target_geo is not None:
            geo.setEndValue(target_geo)
        elif slide_down:
            r = self.geometry()
            geo.setEndValue(QRect(r.x(), r.y() + 20, r.width(), r.height()))
        else:
            geo.setEndValue(self._shrink_geometry(self.geometry()))

        geo.setEasingCurve(QEasingCurve.Type.InCubic)

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(opacity)
        self._anim_group.addAnimation(geo)
        self._anim_group.finished.connect(finish_callback)
        self._anim_group.start()

    def _fade_in(self) -> None:
        self._stop_animations()
        current = self.geometry()

        target = QRect(
            current.x() - int(current.width() * 0.04),
            current.y() - int(current.height() * 0.04),
            int(current.width() * 1.08),
            int(current.height() * 1.08),
        )
        shrunken = QRect(
            target.x() + int(target.width() * 0.04),
            target.y() + int(target.height() * 0.04),
            int(target.width() * 0.92),
            int(target.height() * 0.92),
        )
        self.setGeometry(shrunken)

        opacity = QPropertyAnimation(self, b"windowOpacity")
        opacity.setDuration(self._EXPAND_DURATION)
        opacity.setStartValue(self.windowOpacity())
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo = QPropertyAnimation(self, b"geometry")
        geo.setDuration(self._EXPAND_DURATION)
        geo.setStartValue(shrunken)
        geo.setEndValue(target)
        geo.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(opacity)
        self._anim_group.addAnimation(geo)
        self._anim_group.start()

    def _fade_in_from_taskbar(self) -> None:
        self._stop_animations()
        target = getattr(self, "_restore_geometry", self.geometry())

        taskbar = self._get_taskbar_geometry()
        if taskbar is None:
            self._fade_in()
            return

        cx = taskbar.x() + taskbar.width() // 2
        cy = taskbar.y() + taskbar.height() // 2
        start = QRect(cx - 10, cy - 10, 20, 20)
        self.setGeometry(start)
        self.setWindowOpacity(0.0)

        opacity = QPropertyAnimation(self, b"windowOpacity")
        opacity.setDuration(self._EXPAND_DURATION)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo = QPropertyAnimation(self, b"geometry")
        geo.setDuration(self._EXPAND_DURATION)
        geo.setStartValue(start)
        geo.setEndValue(target)
        geo.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(opacity)
        self._anim_group.addAnimation(geo)
        self._anim_group.start()

    def _fade_out_and_minimize(self) -> None:
        self._restore_geometry = self.geometry()
        taskbar = self._get_taskbar_geometry()

        if taskbar is not None:
            cx = taskbar.x() + taskbar.width() // 2
            cy = taskbar.y() + taskbar.height() // 2
            target = QRect(cx, cy, 1, 1)
            self._fade_out_with_shrink(self._minimize_now, target_geo=target)
        else:
            self._fade_out_with_shrink(self._minimize_now, slide_down=True)

    def _minimize_now(self) -> None:
        if hasattr(self, "_restore_geometry"):
            self.setGeometry(self._restore_geometry)
        self.setWindowOpacity(0.0)
        self.showMinimized()

    def closeEvent(self, event) -> None:
        if not self._is_closing:
            self._is_closing = True
            event.ignore()
            self._fade_out_with_shrink(self.close)
        else:
            event.accept()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            if not self.isMinimized() and self.windowOpacity() < 1.0:
                if hasattr(self, "_restore_geometry"):
                    self._fade_in_from_taskbar()
                else:
                    self._fade_in()
        super().changeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.windowOpacity() == 0.0:
            self._fade_in()

    def _init_ui(self) -> None:
        self.setWindowTitle("Vanta Launcher")

        if getattr(sys, "frozen", False):
            icon_path = os.path.join(sys._MEIPASS, "icons", "icon.ico")
        else:
            icon_path = os.path.join(os.path.dirname(__file__), "icons", "icon.ico")
            
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(370, 290)

        arrow_path = _generate_arrow_image()
        self.setStyleSheet(self._stylesheet(arrow_path))

        central = QWidget(self)
        self.setCentralWidget(central)

        outer_v = QVBoxLayout(central)
        outer_v.addStretch(1)

        outer_h = QHBoxLayout()
        outer_h.addStretch(1)

        card = QFrame(objectName="cardFrame")
        card.setFixedSize(320, 240)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 100))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 16, 24, 24)
        card_layout.setSpacing(14)

        title = QHBoxLayout()
        title.setContentsMargins(0, 0, 0, 0)
        title.setSpacing(8)

        brand = QLabel("Vanta")
        brand.setStyleSheet(
            "color: #FFFFFF; font-family: 'Segoe UI', -apple-system, sans-serif;"
            " font-size: 13px; font-weight: bold; background: transparent; padding: 0;"
        )
        title.addWidget(brand)
        title.addStretch(1)

        self._min_btn = QPushButton(objectName="minBtn")
        self._min_btn.setFixedSize(12, 12)
        self._min_btn.clicked.connect(self._fade_out_and_minimize)

        self._close_btn = QPushButton(objectName="closeBtn")
        self._close_btn.setFixedSize(12, 12)
        self._close_btn.clicked.connect(self.close)

        title.addWidget(self._min_btn)
        title.addWidget(self._close_btn)
        card_layout.addLayout(title)

        self.nick_input = QLineEdit()
        self.nick_input.setPlaceholderText("Username")

        self.version_combo = QComboBox()
        self.version_combo.addItem("Loading versions...")
        self.version_combo.setEnabled(False)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._launch_game)

        card_layout.addWidget(self.nick_input)
        card_layout.addWidget(self.version_combo)
        card_layout.addWidget(self.play_button)

        outer_h.addWidget(card)
        outer_h.addStretch(1)
        outer_v.addLayout(outer_h)
        outer_v.addStretch(1)

    @staticmethod
    def _stylesheet(arrow_path: str) -> str:
        return f"""
            QMainWindow {{
                background: transparent;
            }}
            #cardFrame {{
                background-color: #1C1C1E;
                border: 1px solid #2C2C2E;
                border-radius: 16px;
            }}
            QLineEdit {{
                background-color: #2C2C2E;
                border: 2px solid #3A3A3C;
                border-radius: 8px;
                padding: 11px 15px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 14px;
                color: #FFFFFF;
            }}
            QLineEdit:focus {{
                border: 2px solid #0A84FF;
                background-color: #2C2C2E;
            }}
            QLineEdit::placeholder {{
                color: #8E8E93;
            }}
            QComboBox {{
                background-color: #2C2C2E;
                border: 2px solid #3A3A3C;
                border-radius: 8px;
                padding: 11px 15px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 14px;
                color: #FFFFFF;
            }}
            QComboBox:focus {{
                border: 2px solid #0A84FF;
                background-color: #2C2C2E;
            }}
            QComboBox::drop-down {{
                border: none;
                background: transparent;
                width: 30px;
                subcontrol-origin: padding;
                subcontrol-position: top right;
            }}
            QComboBox::down-arrow {{
                image: url({arrow_path});
                width: 10px;
                height: 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1C1C1E;
                border: 1px solid #2C2C2E;
                border-radius: 8px;
                outline: 0;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 10px 14px;
                border-radius: 6px;
                color: #FFFFFF;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: #2C2C2E;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: #0A84FF;
                color: #FFFFFF;
            }}
            QScrollBar:vertical {{
                border: none;
                background: #1C1C1E;
                width: 8px;
                margin: 4px 0;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #48484A;
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #636366;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                border: none;
                background: none;
                height: 0;
            }}
            QPushButton {{
                background-color: #0A84FF;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 14px 20px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #007AFF;
            }}
            QPushButton:pressed {{
                background-color: #0056B3;
            }}
            QPushButton:disabled {{
                background-color: #3A3A3C;
                color: #8E8E93;
            }}
            #closeBtn {{
                background-color: #FF5F56;
                border: none;
                border-radius: 6px;
            }}
            #closeBtn:hover {{
                background-color: #E0443E;
            }}
            #minBtn {{
                background-color: #27C93F;
                border: none;
                border-radius: 6px;
            }}
            #minBtn:hover {{
                background-color: #1AAB33;
            }}
        """

    def _load_settings(self) -> None:
        self.nick_input.setText(self.settings.value("username", ""))

    def _save_settings(self) -> None:
        self.settings.setValue("username", self.nick_input.text().strip())
        self.settings.setValue("version", self.version_combo.currentText())

    def _fetch_versions(self) -> None:
        self._fetch_worker = VersionFetchWorker()
        self._fetch_worker.versions_fetched.connect(self._on_versions_fetched)
        self._fetch_worker.error_occurred.connect(self._on_versions_fetch_failed)
        self._fetch_worker.start()

    def _on_versions_fetched(self, versions: List[str]) -> None:
        self.version_combo.clear()
        self.version_combo.addItems(versions)
        self.version_combo.setEnabled(True)

        saved_version = self.settings.value("version", "")
        if saved_version in versions:
            self.version_combo.setCurrentText(saved_version)

    def _on_versions_fetch_failed(self, _error_message: str) -> None:
        self.version_combo.clear()
        fallback = ["1.21.4", "1.21.1", "1.20.4", "1.19.4", "1.16.5", "1.8.9"]
        try:
            installed = [
                v["id"] for v in minecraft_launcher_lib.utils.get_installed_versions(
                    self.minecraft_dir
                )
            ]
            combined = list(dict.fromkeys(installed + fallback))
            self.version_combo.addItems(combined)
        except Exception:
            combined = fallback
            self.version_combo.addItems(combined)

        self.version_combo.setEnabled(True)
        
        saved_version = self.settings.value("version", "")
        if saved_version in combined:
            self.version_combo.setCurrentText(saved_version)

    def _set_ui_enabled(self, enabled: bool) -> None:
        self.nick_input.setEnabled(enabled)
        self.version_combo.setEnabled(enabled)
        self.play_button.setEnabled(enabled)

    def _launch_game(self) -> None:
        username = self.nick_input.text().strip()
        version = self.version_combo.currentText()

        if not username:
            QMessageBox.warning(self, "Invalid Username", "Please enter a username.")
            return

        if not version or version == "Loading versions..." or not self.version_combo.isEnabled():
            QMessageBox.warning(self, "Launcher Busy", "Please wait for the version list to load.")
            return

        self._save_settings()

        has_system_java = shutil.which("java") is not None

        try:
            runtime_info = minecraft_launcher_lib.runtime.get_version_runtime_information(
                version, self.minecraft_dir
            )
        except Exception:
            runtime_info = None

        if not has_system_java and runtime_info is None:
            legacy_exec = minecraft_launcher_lib.runtime.get_executable_path("jre-legacy", self.minecraft_dir)
            if not legacy_exec:
                reply = QMessageBox.question(
                    self,
                    "Java Runtime Missing",
                    "A Java installation is required to run this older version of Minecraft, but none was detected on your system.\n\n"
                    "Would you like the launcher to automatically download and configure a portable, user-space Java runtime (Java 8)?\n\n"
                    "No administrator privileges are required.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._set_ui_enabled(False)
                    self.play_button.setText("Downloading Java...")
                    
                    self._java_worker = JavaDownloadWorker("jre-legacy", self.minecraft_dir)
                    self._java_worker.progress.connect(self._on_java_progress)
                    self._java_worker.finished.connect(lambda: self._start_game_launch(username, version))
                    self._java_worker.error.connect(self._on_java_error)
                    self._java_worker.start()
                    return
                else:
                    return

        self._start_game_launch(username, version)

    def _on_java_progress(self, status: str, percent: int) -> None:
        if percent >= 0:
            self.play_button.setText(f"Java: {percent}%")
        else:
            self.play_button.setText(status[:20])

    def _on_java_error(self, error_message: str) -> None:
        self._set_ui_enabled(True)
        self.play_button.setText("Play")
        QMessageBox.critical(
            self,
            "Java Install Error",
            f"Failed to install portable Java runtime:\n\n{error_message}",
        )

    def _start_game_launch(self, username: str, version: str) -> None:
        self._set_ui_enabled(False)
        self._launch_worker = LaunchWorker(username, version, self.minecraft_dir)
        self._launch_worker.progress_updated.connect(self._on_launch_progress)
        self._launch_worker.launch_success.connect(self._on_launch_success)
        self._launch_worker.game_exited.connect(self._on_game_exited)
        self._launch_worker.error_occurred.connect(self._on_launch_error)
        self._launch_worker.start()

    def _on_launch_progress(self, status: str, percent: int) -> None:
        if percent >= 0:
            self.play_button.setText(f"Installing: {percent}%")
        else:
            label = status[:20] + "..." if len(status) > 20 else status
            self.play_button.setText(label)

    def _on_launch_success(self) -> None:
        self._fade_out_with_shrink(self.hide)

    def _on_game_exited(self) -> None:
        self.setWindowOpacity(0.0)
        self.show()
        self._set_ui_enabled(True)
        self.play_button.setText("Play")

    def _on_launch_error(self, error_message: str) -> None:
        self._set_ui_enabled(True)
        self.play_button.setText("Play")
        QMessageBox.critical(
            self,
            "Launch Error",
            f"An error occurred while launching Minecraft:\n\n{error_message}",
        )


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vanta.launcher.minecraft.1.0")
        except Exception as e:
            sys.stderr.write(f"Failed to configure taskbar AppUserModelID: {e}\n")

    app = QApplication(sys.argv)
    launcher = MinecraftLauncher()
    launcher.show()
    sys.exit(app.exec())
