import sys
import os
import uuid
import json
import subprocess
import ctypes
import shutil
import requests
import threading
from ctypes import wintypes
from typing import List, Optional

try:
    from pypresence import Presence
except ImportError:
    Presence = None

from PyQt6.QtCore import (
    QThread, pyqtSignal, Qt, QSettings, QPoint, QPropertyAnimation,
    QEasingCurve, QEvent, QParallelAnimationGroup, QRect, QTimer
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QMessageBox, QGraphicsDropShadowEffect,
    QFrame, QLabel, QStackedWidget, QSlider, QCheckBox, QListWidget, QListWidgetItem,
    QProgressBar
)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QBrush, QPolygon, QIcon, QPixmap

import minecraft_launcher_lib
import minecraft_launcher_lib.runtime
import minecraft_launcher_lib.fabric

API_HEADERS = {
    "User-Agent": "VantaLauncher/1.5 (+https://github.com/salo-yek/vanta; support@getvanta.xyz)"
}


def silence_asyncio_windows_bugs() -> None:
    """
    Patch asyncio transport destructors on Windows to suppress benign errors
    during process exit (ValueError/RuntimeError on closed pipe).
    """
    if sys.platform != "win32":
        return
    try:
        import asyncio
        from asyncio import proactor_events, base_subprocess

        if hasattr(proactor_events, "_ProactorBasePipeTransport"):
            original_del = proactor_events._ProactorBasePipeTransport.__del__

            def patched_pipe_del(self):
                try:
                    original_del(self)
                except (ValueError, OSError, RuntimeError):
                    pass

            proactor_events._ProactorBasePipeTransport.__del__ = patched_pipe_del

        if hasattr(base_subprocess, "BaseSubprocessTransport"):
            original_sub_del = base_subprocess.BaseSubprocessTransport.__del__

            def patched_sub_del(self):
                try:
                    original_sub_del(self)
                except (ValueError, OSError, RuntimeError):
                    pass

            base_subprocess.BaseSubprocessTransport.__del__ = patched_sub_del
    except Exception:
        pass


def is_fabric_compatible(version: str) -> bool:
    """Fabric loader requires Minecraft 1.14 or higher."""
    try:
        core = version.split("-")[0]
        parts = core.split(".")
        if len(parts) < 2:
            return False
        major, minor = int(parts[0]), int(parts[1])
        return (major > 1) or (major == 1 and minor >= 14)
    except (ValueError, IndexError):
        return False


def matches_mod(filename: str, mod_id: str) -> bool:
    """Check if the filename matches a specific mod ID, handling overlaps like sodium vs sodium-extra."""
    fn = filename.lower().replace("-", "").replace("_", "")
    m = mod_id.lower().replace("-", "").replace("_", "")
    if m == "sodium":
        return "sodium" in fn and "extra" not in fn
    return m in fn




class VersionFetchWorker(QThread):
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
            def set_status(text: str) -> None:
                self.progress.emit(text, -1)

            def set_max(val: int) -> None:
                self._max_val = val

            def set_progress(val: int) -> None:
                if self._max_val > 0:
                    percent = int((val / self._max_val) * 100)
                    self.progress.emit("Downloading Java...", percent)

            callbacks = {
                "setStatus": set_status,
                "setProgress": set_progress,
                "setMax": set_max,
            }

            minecraft_launcher_lib.runtime.install_jvm_runtime(
                self.jvm_version, self.minecraft_dir, callback=callbacks
            )
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class LaunchWorker(QThread):
    progress_updated = pyqtSignal(str, int)
    launch_success = pyqtSignal()
    game_exited = pyqtSignal()
    error_occurred = pyqtSignal(str)
    performance_mods_installed = pyqtSignal()

    def __init__(self, username: str, version: str, minecraft_dir: str,
                 ram_gb: int, performance_mode: bool, java_path: Optional[str] = None):
        super().__init__()
        self.username = username
        self.version = version
        self.minecraft_dir = minecraft_dir
        self.ram_gb = ram_gb
        self.performance_mode = performance_mode
        self.java_path = java_path
        self._max_val = 0
        self.process = None

    def run(self) -> None:
        try:
            def set_status(text: str) -> None:
                self.progress_updated.emit(text, -1)

            def set_max(val: int) -> None:
                self._max_val = val

            def set_progress(val: int) -> None:
                if self._max_val > 0:
                    percent = int((val / self._max_val) * 100)
                    self.progress_updated.emit("Installing...", percent)

            callbacks = {
                "setStatus": set_status,
                "setProgress": set_progress,
                "setMax": set_max,
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

            vanta_dir = os.path.join(
                os.environ.get("APPDATA", "") if sys.platform == "win32" else os.path.expanduser("~"),
                ".Vanta"
            )

            instance_dir = os.path.join(vanta_dir, "instances", self.version)
            os.makedirs(instance_dir, exist_ok=True)

            mods_dir = os.path.join(instance_dir, "mods")
            has_custom_mods = False
            if os.path.exists(mods_dir):
                try:
                    jars = [
                        f for f in os.listdir(mods_dir)
                        if f.endswith(".jar") and "fabric-api" not in f.lower() and "fabric_api" not in f.lower()
                    ]
                    has_custom_mods = len(jars) > 0
                except Exception:
                    pass

            target_version = self.version
            use_fabric = (self.performance_mode or has_custom_mods) and is_fabric_compatible(self.version)

            if use_fabric:
                try:
                    self.progress_updated.emit("Installing Fabric...", 10)
                    minecraft_launcher_lib.fabric.install_fabric(self.version, self.minecraft_dir)
                except Exception as e:
                    sys.stderr.write(f"Fabric installation/cache update skipped: {e}\n")

                try:
                    latest_loader = minecraft_launcher_lib.fabric.get_latest_loader_version()
                    target_version = f"fabric-loader-{latest_loader}-{self.version}"
                except Exception as e:
                    sys.stderr.write(f"Cannot resolve latest online Fabric loader ({e}). Searching cache...\n")
                    # Offline fallback: Search installed versions for any pre-installed fabric build
                    found_cached_fabric = None
                    try:
                        for vid in installed:
                            if vid.startswith("fabric-loader-") and vid.endswith(self.version):
                                found_cached_fabric = vid
                                break
                    except Exception:
                        pass

                    if found_cached_fabric:
                        target_version = found_cached_fabric
                    else:
                        target_version = self.version

                try:
                    self._ensure_fabric_api_and_mods(instance_dir, self.performance_mode)
                except Exception as e:
                    sys.stderr.write(f"Fabric performance mods installation error: {e}\n")

            options = {
                "username": self.username,
                "uuid": str(uuid.uuid4()),
                "token": "",
                "launcherName": "Vanta",
                "launcherVersion": "1.1",
                "gameDirectory": instance_dir,
                "jvmArguments": [
                    f"-Xmx{self.ram_gb}G",
                    f"-Xms{self.ram_gb}G",
                    "-XX:+UseG1GC",
                    "-XX:+ParallelRefProcEnabled",
                    "-XX:MaxGCPauseMillis=200",
                    "-XX:+UnlockExperimentalVMOptions",
                    "-XX:+DisableExplicitGC",
                    "-XX:+AlwaysPreTouch",
                    "-XX:G1NewSizePercent=30",
                    "-XX:G1MaxNewSizePercent=40",
                    "-XX:G1HeapRegionSize=8M",
                    "-XX:G1ReservePercent=20",
                    "-XX:G1HeapWastePercent=5",
                    "-XX:G1MixedGCCountTarget=4",
                    "-XX:InitiatingHeapOccupancyPercent=15",
                    "-XX:G1MixedGCLiveThresholdPercent=90",
                    "-XX:G1RSetUpdatingPauseTimePercent=5",
                    "-XX:SurvivorRatio=32",
                    "-XX:+PerfDisableSharedMem",
                    "-XX:MaxTenuringThreshold=1"
                ]
            }

            if self.java_path:
                options["executablePath"] = self.java_path
            else:
                try:
                    runtime_info = minecraft_launcher_lib.runtime.get_version_runtime_information(
                        self.version, self.minecraft_dir
                    )
                    if runtime_info:
                        runtime_name = runtime_info.get("name")
                        if runtime_name:
                            java_exec = minecraft_launcher_lib.runtime.get_executable_path(
                                runtime_name, self.minecraft_dir
                            )
                            if java_exec:
                                options["executablePath"] = java_exec
                except Exception:
                    pass

                if "executablePath" not in options:
                    legacy_exec = minecraft_launcher_lib.runtime.get_executable_path(
                        "jre-legacy", self.minecraft_dir
                    )
                    if legacy_exec:
                        options["executablePath"] = legacy_exec

            command = minecraft_launcher_lib.command.get_minecraft_command(
                target_version,
                self.minecraft_dir,
                options
            )

            self.progress_updated.emit("Launching...", 100)

            log_path = os.path.join(instance_dir, "latest.log")
            log_file = open(log_path, "w", encoding="utf-8")
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    cwd=instance_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                self.launch_success.emit()
                self.process.wait()
                self.game_exited.emit()
            finally:
                log_file.close()

        except FileNotFoundError:
            self.error_occurred.emit(
                "Java environment not found.\n\n"
                "Please ensure Java (OpenJDK 17 or 21 recommended) "
                "is installed and present in your system's PATH."
            )
        except Exception as e:
            self.error_occurred.emit(str(e))

    def _ensure_fabric_api_and_mods(self, instance_dir: str, download_perf_mods: bool) -> None:
        mods_dir = os.path.join(instance_dir, "mods")
        os.makedirs(mods_dir, exist_ok=True)

        mods_to_download = ["fabric-api"]
        if download_perf_mods:
            mods_to_download.extend(["sodium", "lithium", "ferrite-core", "entityculling"])

        seen = set()
        mods = []
        for m in mods_to_download:
            if m not in seen:
                seen.add(m)
                mods.append(m)

        for i, mod in enumerate(mods):
            try:
                self.progress_updated.emit(f"Checking {mod}...", int(20 + (i / len(mods)) * 60))
                params = {
                    "loaders": json.dumps(["fabric"]),
                    "game_versions": json.dumps([self.version])
                }
                url = f"https://api.modrinth.com/v2/project/{mod}/version"
                r = requests.get(url, headers=API_HEADERS, params=params, timeout=5)

                target_filename = None
                file_info = None

                if r.status_code == 200:
                    data = r.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        files_list = data[0].get("files", [])
                        if files_list:
                            file_info = files_list[0]
                            for f in files_list:
                                if f.get("primary"):
                                    file_info = f
                                    break
                            target_filename = file_info["filename"]

                if target_filename:
                    # Clean up other versions of this mod
                    if os.path.exists(mods_dir):
                        for f in os.listdir(mods_dir):
                            if f.endswith(".jar") and matches_mod(f, mod) and f != target_filename:
                                try:
                                    os.remove(os.path.join(mods_dir, f))
                                except Exception as del_err:
                                    sys.stderr.write(f"Failed to delete old mod version {f}: {del_err}\n")

                    # Download if missing
                    dest_path = os.path.join(mods_dir, target_filename)
                    if not os.path.exists(dest_path):
                        self.progress_updated.emit(f"Downloading {mod}...", int(20 + (i / len(mods)) * 60))
                        dl_res = requests.get(file_info["url"], headers=API_HEADERS, timeout=10)
                        if dl_res.status_code == 200:
                            with open(dest_path, "wb") as out:
                                out.write(dl_res.content)
                else:
                    # Offline fallback: if any local file matches the signature, we keep it
                    local_match = False
                    if os.path.exists(mods_dir):
                        for f in os.listdir(mods_dir):
                            if f.endswith(".jar") and matches_mod(f, mod):
                                local_match = True
                                break
                    if not local_match:
                        sys.stderr.write(f"Mod {mod} could not be resolved from API and is missing locally.\n")
            except Exception as e:
                sys.stderr.write(f"Error checking/downloading {mod}: {e}\n")

        self.performance_mods_installed.emit()


class AvatarLoaderWorker(QThread):
    avatar_loaded = pyqtSignal(QPixmap)

    def __init__(self, username: str):
        super().__init__()
        self.username = username

    def run(self) -> None:
        if not self.username:
            return
        try:
            url = f"https://minotar.net/helm/{self.username}/32.png"
            r = requests.get(url, headers=API_HEADERS, timeout=5)
            if r.status_code == 200:
                pixmap = QPixmap()
                pixmap.loadFromData(r.content)
                self.avatar_loaded.emit(pixmap)
        except Exception:
            pass


class ModSearchWorker(QThread):
    results_ready = pyqtSignal(list)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self) -> None:
        try:
            params = {
                "query": self.query,
                "facets": json.dumps([["categories:fabric"], ["project_type:mod"]])
            }
            r = requests.get("https://api.modrinth.com/v2/search",
                             headers=API_HEADERS, params=params, timeout=5)
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                self.results_ready.emit(hits)
        except Exception:
            self.results_ready.emit([])


class ModInstallWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, project_id: str, mc_version: str, instance_dir: str):
        super().__init__()
        self.project_id = project_id
        self.mc_version = mc_version
        self.instance_dir = instance_dir

    def run(self) -> None:
        try:
            self.progress.emit("Locating version...")
            params = {
                "loaders": json.dumps(["fabric"]),
                "game_versions": json.dumps([self.mc_version])
            }
            url = f"https://api.modrinth.com/v2/project/{self.project_id}/version"
            r = requests.get(url, headers=API_HEADERS, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if not data or not isinstance(data, list) or len(data) == 0:
                    raise ValueError("No compatible versions found.")

                files_list = data[0].get("files", [])
                if not files_list:
                    raise ValueError("No compatible files found in this project version.")

                file_info = files_list[0]
                for f in files_list:
                    if f.get("primary"):
                        file_info = f
                        break

                target_filename = file_info["filename"]
                self.progress.emit(f"Downloading {target_filename}...")
                mods_dir = os.path.join(self.instance_dir, "mods")
                dest = os.path.join(mods_dir, target_filename)
                os.makedirs(mods_dir, exist_ok=True)

                # Clean up other versions of this mod
                if os.path.exists(mods_dir):
                    for f in os.listdir(mods_dir):
                        if f.endswith(".jar") and matches_mod(f, self.project_id) and f != target_filename:
                            try:
                                os.remove(os.path.join(mods_dir, f))
                            except Exception:
                                pass

                dl_res = requests.get(file_info["url"], headers=API_HEADERS, timeout=10)
                if dl_res.status_code == 200:
                    with open(dest, "wb") as f:
                        f.write(dl_res.content)
                else:
                    raise ValueError("Download failed.")

                self._ensure_fabric_api()
                self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def _ensure_fabric_api(self) -> None:
        mods_dir = os.path.join(self.instance_dir, "mods")
        os.makedirs(mods_dir, exist_ok=True)

        try:
            params = {
                "loaders": json.dumps(["fabric"]),
                "game_versions": json.dumps([self.mc_version])
            }
            r = requests.get("https://api.modrinth.com/v2/project/fabric-api/version",
                             headers=API_HEADERS, params=params, timeout=5)

            target_filename = None
            file_info = None
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    files_list = data[0].get("files", [])
                    if files_list:
                        file_info = files_list[0]
                        for f in files_list:
                            if f.get("primary"):
                                file_info = f
                                break
                        target_filename = file_info["filename"]

            if target_filename:
                # Clean up any other fabric-api versions
                if os.path.exists(mods_dir):
                    for f in os.listdir(mods_dir):
                        if f.endswith(".jar") and matches_mod(f, "fabric-api") and f != target_filename:
                            try:
                                os.remove(os.path.join(mods_dir, f))
                            except Exception:
                                pass

                dest_path = os.path.join(mods_dir, target_filename)
                if not os.path.exists(dest_path):
                    self.progress.emit("Downloading Fabric API dependency...")
                    dl_res = requests.get(file_info["url"], headers=API_HEADERS, timeout=10)
                    if dl_res.status_code == 200:
                        with open(dest_path, "wb") as out:
                            out.write(dl_res.content)
            else:
                # Fallback check
                local_match = False
                if os.path.exists(mods_dir):
                    for f in os.listdir(mods_dir):
                        if f.endswith(".jar") and matches_mod(f, "fabric-api"):
                            local_match = True
                            break
                if not local_match:
                    self.progress.emit("Fabric API is missing and could not be downloaded.")
        except Exception as e:
            sys.stderr.write(f"Failed to auto-download Fabric API: {e}\n")


def _generate_arrow_image() -> str:
    """Generate a small arrow PNG for combo boxes; stores it in a hidden user folder."""
    temp_dir = os.path.join(os.path.expanduser("~"), ".vanta_assets")
    os.makedirs(temp_dir, exist_ok=True)
    arrow_path = os.path.join(temp_dir, "arrow.png")

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
        sys.stderr.write(f"Failed to generate arrow image: {e}\n")

    return arrow_path


def _generate_settings_image() -> str:
    """Generate a hamburger menu icon for the settings button."""
    temp_dir = os.path.join(os.path.expanduser("~"), ".vanta_assets")
    os.makedirs(temp_dir, exist_ok=True)
    settings_path = os.path.join(temp_dir, "settings.png")

    if os.path.exists(settings_path):
        return settings_path

    try:
        image = QImage(16, 16, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#A0A0A2")))

        painter.drawRoundedRect(QRect(1, 3, 14, 2), 1, 1)
        painter.drawRoundedRect(QRect(1, 7, 14, 2), 1, 1)
        painter.drawRoundedRect(QRect(1, 11, 14, 2), 1, 1)

        painter.end()
        image.save(settings_path)
    except Exception as e:
        sys.stderr.write(f"Failed to generate settings icon: {e}\n")

    return settings_path


class MinecraftLauncher(QMainWindow):
    _FADE_DURATION = 250
    _EXPAND_DURATION = 350

    def __init__(self) -> None:
        super().__init__()
        self.minecraft_dir = minecraft_launcher_lib.utils.get_minecraft_directory()
        self.settings = QSettings("Vanta", "Preferences")
        self._drag_position = QPoint()
        self._is_closing = False
        self._drawer_expanded = False
        self.rpc = None
        self._rpc_lock = threading.Lock()
        self._workers: list = []
        self._launch_in_progress = False

        if sys.platform == "win32":
            self.vanta_dir = os.path.join(os.environ.get("APPDATA", ""), ".Vanta")
        else:
            self.vanta_dir = os.path.expanduser("~/.Vanta")

        self.setWindowOpacity(0.0)
        self._init_ui()
        self._init_ram_slider()
        QTimer.singleShot(1000, self._init_rpc)
        self._load_settings()
        self._fetch_versions()

    def _init_rpc(self) -> None:
        if Presence is None:
            self.rpc = None
            self._set_rpc_unavailable()
            return

        if self.settings.value("rpc_enabled", "true") != "true":
            self.rpc = None
            return

        def connect_discord():
            try:
                with self._rpc_lock:
                    if self.rpc:
                        try:
                            self.rpc.close()
                        except Exception:
                            pass
                    self.rpc = Presence("1509979983874097404")
                    self.rpc.connect()
                    from time import time
                    self.rpc.update(
                        state="Free Non-Premium Launcher",
                        details="Playing Minecraft",
                        start=int(time())
                    )
            except Exception:
                with self._rpc_lock:
                    self.rpc = None

        threading.Thread(target=connect_discord, daemon=True).start()

    def _update_rpc(self, state: str, details: str) -> None:
        def update_task():
            with self._rpc_lock:
                if self.rpc:
                    try:
                        from time import time
                        self.rpc.update(state=state, details=details, start=int(time()))
                    except Exception:
                        pass

        threading.Thread(target=update_task, daemon=True).start()

    def _set_rpc_unavailable(self) -> None:
        self.rpc_checkbox.setChecked(False)
        self.rpc_checkbox.setEnabled(False)
        self.rpc_checkbox.setText("Discord Rich Presence (pypresence not installed)")
        self.settings.setValue("rpc_enabled", "false")

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

    def _fade_out_with_shrink(self, finish_callback, *,
                              target_geo: Optional[QRect] = None,
                              slide_down: bool = False) -> None:
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

            def cleanup_and_close():
                self._shutdown_workers()
                with self._rpc_lock:
                    if self.rpc:
                        try:
                            self.rpc.clear()
                            self.rpc.close()
                        except Exception:
                            pass
                        self.rpc = None
                self.close()

            self._fade_out_with_shrink(cleanup_and_close)
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

    def _register_worker(self, worker: QThread) -> None:
        if worker not in self._workers:
            self._workers.append(worker)
            worker.finished.connect(lambda: self._unregister_worker(worker))

    def _unregister_worker(self, worker: QThread) -> None:
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def _shutdown_workers(self) -> None:
        if hasattr(self, "_launch_worker") and self._launch_worker is not None:
            try:
                proc = getattr(self._launch_worker, "process", None)
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            except Exception:
                pass
            if self._launch_worker.isRunning():
                self._launch_worker.wait(3000)

        for worker in list(self._workers):
            if worker is not getattr(self, "_launch_worker", None) and worker.isRunning():
                worker.wait(2000)

    @staticmethod
    def _get_total_ram_gb() -> int:
        if sys.platform == "win32":
            try:
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                kernel32 = ctypes.windll.kernel32
                mem_status = MEMORYSTATUSEX()
                mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status)):
                    return int(mem_status.ullTotalPhys / (1024 ** 3))
            except Exception:
                pass
        elif sys.platform.startswith("linux"):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return int(kb / (1024 ** 2))
            except Exception:
                pass
        elif sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    bytes_total = int(result.stdout.strip())
                    return int(bytes_total / (1024 ** 3))
            except Exception:
                pass
        return 4

    def _show_progress(self, visible: bool, text: str = "") -> None:
        if visible:
            self.play_stack.setCurrentIndex(1)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat(f"{text} %p%")
        else:
            self.play_stack.setCurrentIndex(0)
            self.play_button.setEnabled(True)
            self.play_button.setText("Play")

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

        self.setFixedSize(690, 270)

        arrow_path = _generate_arrow_image()
        self.setStyleSheet(self._stylesheet(arrow_path))

        central = QWidget(self)
        self.setCentralWidget(central)

        self.card = QFrame(central, objectName="cardFrame")
        self.card.setGeometry(25, 25, 320, 220)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(24)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 100))
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(10)

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

        self._settings_btn = QPushButton(objectName="settingsBtn")
        self._settings_btn.setFixedSize(16, 16)
        settings_icon_path = _generate_settings_image()
        if os.path.exists(settings_icon_path):
            self._settings_btn.setIcon(QIcon(settings_icon_path))
            self._settings_btn.setIconSize(self._settings_btn.size())
        self._settings_btn.clicked.connect(self._toggle_drawer)

        self._min_btn = QPushButton(objectName="minBtn")
        self._min_btn.setFixedSize(12, 12)
        self._min_btn.clicked.connect(self._fade_out_and_minimize)

        self._close_btn = QPushButton(objectName="closeBtn")
        self._close_btn.setFixedSize(12, 12)
        self._close_btn.clicked.connect(self.close)

        title.addWidget(self._settings_btn)
        title.addWidget(self._min_btn)
        title.addWidget(self._close_btn)
        card_layout.addLayout(title)

        nick_layout = QHBoxLayout()
        nick_layout.setContentsMargins(0, 0, 0, 0)
        nick_layout.setSpacing(8)

        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(32, 32)
        self.avatar_label.setStyleSheet("border-radius: 4px; background: #2C2C2E;")
        nick_layout.addWidget(self.avatar_label)

        self.nick_input = QLineEdit()
        self.nick_input.setPlaceholderText("Username")
        nick_layout.addWidget(self.nick_input)
        card_layout.addLayout(nick_layout)

        self.version_combo = QComboBox()
        self.version_combo.addItem("Loading versions...")
        self.version_combo.setEnabled(False)
        self.version_combo.currentTextChanged.connect(self._on_version_changed)
        card_layout.addWidget(self.version_combo)

        self.play_stack = QStackedWidget()
        self.play_stack.setFixedHeight(36)

        self.play_button = QPushButton("Play")
        self.play_button.setFixedHeight(36)
        self.play_button.clicked.connect(self._launch_game)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(36)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")

        self.play_stack.addWidget(self.play_button)
        self.play_stack.addWidget(self.progress_bar)
        card_layout.addWidget(self.play_stack)

        self.drawer = QFrame(central, objectName="drawer")
        self.drawer.setGeometry(25, 25, 320, 220)
        self.drawer.stackUnder(self.card)

        drawer_layout = QVBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(16, 16, 16, 16)
        drawer_layout.setSpacing(10)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_tab_btn = QPushButton("Settings", objectName="tabBtn")
        self.mods_tab_btn = QPushButton("Mods Manager", objectName="tabBtn")
        self.settings_tab_btn.clicked.connect(lambda: self.drawer_stack.setCurrentIndex(0))
        self.mods_tab_btn.clicked.connect(lambda: self.drawer_stack.setCurrentIndex(1))
        nav_layout.addWidget(self.settings_tab_btn)
        nav_layout.addWidget(self.mods_tab_btn)
        drawer_layout.addLayout(nav_layout)

        self.drawer_stack = QStackedWidget()
        drawer_layout.addWidget(self.drawer_stack)

        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(10)

        ram_header_layout = QHBoxLayout()
        ram_lbl = QLabel("Allocated RAM:")
        ram_lbl.setStyleSheet("color: #FFFFFF; font-family: 'Segoe UI', sans-serif; font-size: 12px;")
        self.ram_val_lbl = QLabel("4 GB")
        self.ram_val_lbl.setStyleSheet(
            "color: #0A84FF; font-family: 'Segoe UI', sans-serif; font-size: 12px; font-weight: bold;"
        )
        ram_header_layout.addWidget(ram_lbl)
        ram_header_layout.addStretch(1)
        ram_header_layout.addWidget(self.ram_val_lbl)
        settings_layout.addLayout(ram_header_layout)

        self.ram_slider = QSlider(Qt.Orientation.Horizontal)
        self.ram_slider.setMinimum(2)
        self.ram_slider.setMaximum(16)
        self.ram_slider.setValue(4)
        self.ram_slider.valueChanged.connect(self._on_ram_slider_changed)
        settings_layout.addWidget(self.ram_slider)

        self.perf_checkbox = QCheckBox("Performance Mode (Fabric + Optimization Mods)")
        self.perf_checkbox.setChecked(True)
        settings_layout.addWidget(self.perf_checkbox)

        self.rpc_checkbox = QCheckBox("Discord Rich Presence")
        self.rpc_checkbox.setChecked(True)
        self.rpc_checkbox.stateChanged.connect(self._on_rpc_state_changed)
        settings_layout.addWidget(self.rpc_checkbox)
        settings_layout.addStretch(1)

        self.drawer_stack.addWidget(settings_widget)

        mods_widget = QWidget()
        mods_layout = QVBoxLayout(mods_widget)
        mods_layout.setContentsMargins(0, 0, 0, 0)
        mods_layout.setSpacing(6)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(4)

        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Search Modrinth...")
        self.mod_search_input.setFixedHeight(26)
        self.mod_search_input.setStyleSheet("padding: 2px 8px; font-size: 11px;")

        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._on_mod_search)
        self.mod_search_input.textChanged.connect(self._search_timer.start)

        search_layout.addWidget(self.mod_search_input)
        mods_layout.addLayout(search_layout)

        self.mods_list = QListWidget()
        mods_layout.addWidget(self.mods_list)

        mod_action_layout = QHBoxLayout()
        self.mod_action_btn = QPushButton("Install", objectName="modActionBtn")
        self.mod_action_btn.clicked.connect(self._on_mod_action)
        self.mod_action_btn.setFixedHeight(24)
        self.mod_delete_btn = QPushButton("Delete Selected", objectName="modDeleteBtn")
        self.mod_delete_btn.clicked.connect(self._on_mod_delete)
        self.mod_delete_btn.setFixedHeight(24)
        mod_action_layout.addWidget(self.mod_action_btn)
        mod_action_layout.addWidget(self.mod_delete_btn)
        mods_layout.addLayout(mod_action_layout)

        self.drawer_stack.addWidget(mods_widget)

        self._avatar_timer = QTimer()
        self._avatar_timer.setSingleShot(True)
        self._avatar_timer.timeout.connect(self._fetch_avatar)
        self.nick_input.textChanged.connect(self._on_nick_changed)

    def _init_ram_slider(self) -> None:
        total_ram = self._get_total_ram_gb()
        max_ram = max(2, total_ram - 1)
        self.ram_slider.setMaximum(max_ram)

    def _toggle_drawer(self) -> None:
        self._stop_animations()
        anim = QPropertyAnimation(self.drawer, b"geometry")
        anim.setDuration(300)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        if self._drawer_expanded:
            anim.setStartValue(QRect(345, 25, 320, 220))
            anim.setEndValue(QRect(25, 25, 320, 220))
            self._drawer_expanded = False
        else:
            anim.setStartValue(QRect(25, 25, 320, 220))
            anim.setEndValue(QRect(345, 25, 320, 220))
            self._drawer_expanded = True

        self._anim_group = QParallelAnimationGroup()
        self._anim_group.addAnimation(anim)
        self._anim_group.start()

    def _on_ram_slider_changed(self, value: int) -> None:
        self.ram_val_lbl.setText(f"{value} GB")
        self.settings.setValue("ram_gb", value)

    def _on_rpc_state_changed(self, state: int) -> None:
        enabled = state == 2
        self.settings.setValue("rpc_enabled", enabled)
        if enabled:
            self._init_rpc()
        else:
            def close_rpc():
                with self._rpc_lock:
                    if self.rpc:
                        try:
                            self.rpc.close()
                        except Exception:
                            pass
                        self.rpc = None
            threading.Thread(target=close_rpc, daemon=True).start()

    def _on_nick_changed(self) -> None:
        self._avatar_timer.start(500)

    def _fetch_avatar(self) -> None:
        username = self.nick_input.text().strip()
        if not username:
            self.avatar_label.clear()
            return
        self._avatar_loader = AvatarLoaderWorker(username)
        self._register_worker(self._avatar_loader)
        self._avatar_loader.avatar_loaded.connect(self.avatar_label.setPixmap)
        self._avatar_loader.start()

    def _on_version_changed(self, version: str) -> None:
        compatible = is_fabric_compatible(version)
        self.perf_checkbox.setEnabled(compatible)
        if not compatible:
            self.perf_checkbox.setChecked(False)
            self.perf_checkbox.setToolTip("Performance mode requires Minecraft 1.14 or newer.")
        else:
            self.perf_checkbox.setToolTip("")
        self._refresh_installed_mods()

    def _on_mod_search(self) -> None:
        query = self.mod_search_input.text().strip()
        if not query:
            self._refresh_installed_mods()
            return

        if hasattr(self, "_search_worker") and self._search_worker.isRunning():
            try:
                self._search_worker.results_ready.disconnect()
            except Exception:
                pass

        self._search_worker = ModSearchWorker(query)
        self._register_worker(self._search_worker)
        self._search_worker.results_ready.connect(self._on_search_results)
        self._search_worker.start()

    def _on_search_results(self, hits: list) -> None:
        self.mods_list.clear()
        self.mod_action_btn.setText("Install")
        self.mod_action_btn.setProperty("mode", "install")
        for hit in hits:
            item = QListWidgetItem(f"{hit['title']} ({hit['slug']})")
            item.setData(Qt.ItemDataRole.UserRole, hit['project_id'])
            self.mods_list.addItem(item)

    def _refresh_installed_mods(self) -> None:
        self.mods_list.clear()
        self.mod_action_btn.setText("Refresh")
        self.mod_action_btn.setProperty("mode", "refresh")

        version = self.version_combo.currentText()
        if not version or version == "Loading versions...":
            return

        mods_dir = os.path.join(self.vanta_dir, "instances", version, "mods")
        if os.path.exists(mods_dir):
            for file in os.listdir(mods_dir):
                if file.endswith(".jar"):
                    self.mods_list.addItem(QListWidgetItem(file))

    def _on_mod_action(self) -> None:
        mode = self.mod_action_btn.property("mode")
        if mode == "refresh":
            self._refresh_installed_mods()
            return

        selected_item = self.mods_list.currentItem()
        if not selected_item:
            return

        project_id = selected_item.data(Qt.ItemDataRole.UserRole)
        version = self.version_combo.currentText()
        if not project_id or not version or version == "Loading versions...":
            return

        self.mod_action_btn.setEnabled(False)
        self.mod_action_btn.setText("Preparing...")

        instance_dir = os.path.join(self.vanta_dir, "instances", version)
        self._install_worker = ModInstallWorker(project_id, version, instance_dir)
        self._register_worker(self._install_worker)
        self._install_worker.progress.connect(self.mod_action_btn.setText)
        self._install_worker.finished.connect(self._on_mod_installed)
        self._install_worker.error.connect(self._on_mod_install_failed)
        self._install_worker.start()

    def _on_mod_installed(self) -> None:
        self.mod_action_btn.setEnabled(True)
        self.mod_action_btn.setText("Install")
        self.mod_search_input.clear()
        self._refresh_installed_mods()

    def _on_mod_install_failed(self, error: str) -> None:
        self.mod_action_btn.setEnabled(True)
        self.mod_action_btn.setText("Install")
        QMessageBox.warning(self, "Mod Install Error", f"Failed to install mod:\n\n{error}")

    def _on_mod_delete(self) -> None:
        selected_item = self.mods_list.currentItem()
        if not selected_item:
            return

        mode = self.mod_action_btn.property("mode")
        if mode != "refresh":
            QMessageBox.warning(
                self, "Cannot Delete",
                "You can only delete installed mods from the list, not search results."
            )
            return

        filename = selected_item.text()
        version = self.version_combo.currentText()
        if not version or version == "Loading versions...":
            return

        if "fabric-api" in filename.lower() or "fabric_api" in filename.lower():
            QMessageBox.warning(
                self, "Cannot Delete",
                "Fabric API is required for mod support and cannot be removed from here."
            )
            return

        filepath = os.path.join(self.vanta_dir, "instances", version, "mods", filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                self._refresh_installed_mods()
            except Exception as e:
                QMessageBox.warning(self, "Delete Error", f"Could not delete mod file:\n\n{e}")

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
            #drawer {{
                background-color: #1C1C1E;
                border: 1px solid #2C2C2E;
                border-radius: 16px;
            }}
            QLineEdit {{
                background-color: #2C2C2E;
                border: 2px solid #3A3A3C;
                border-radius: 8px;
                padding: 0px 12px;
                height: 32px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
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
                padding: 0px 12px;
                height: 32px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
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
                padding: 6px 12px;
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
                padding: 0px 16px;
                height: 36px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 14px;
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
            QProgressBar {{
                background-color: #2C2C2E;
                border: 1px solid #3A3A3C;
                border-radius: 8px;
                color: #FFFFFF;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: #0A84FF;
                border-radius: 7px;
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
            #settingsBtn {{
                background-color: transparent;
                border: none;
                padding: 0;
            }}
            #settingsBtn:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border-radius: 4px;
            }}
            #tabBtn {{
                background-color: #2C2C2E;
                border: 1px solid #3A3A3C;
                border-radius: 6px;
                padding: 0px 12px;
                height: 26px;
                font-size: 11px;
                font-weight: normal;
                color: #FFFFFF;
            }}
            #tabBtn:hover {{
                background-color: #3A3A3C;
            }}
            #drawer QLabel {{
                color: #FFFFFF;
                font-family: 'Segoe UI', sans-serif;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: #3A3A3C;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #FFFFFF;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }}
            QCheckBox {{
                color: #FFFFFF;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 2px solid #3A3A3C;
                border-radius: 4px;
                background: #2C2C2E;
            }}
            QCheckBox::indicator:checked {{
                background-color: #0A84FF;
                border-color: #0A84FF;
            }}
            QListWidget {{
                background-color: #2C2C2E;
                border: 1px solid #3A3A3C;
                border-radius: 8px;
                color: #FFFFFF;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
            }}
            QListWidget::item {{
                padding: 4px;
                border-bottom: 1px solid #3A3A3C;
            }}
            QListWidget::item:selected {{
                background-color: #0A84FF;
                color: #FFFFFF;
            }}
            #modActionBtn, #modDeleteBtn {{
                font-size: 11px;
                padding: 0px 8px;
                height: 22px;
                border-radius: 6px;
            }}
            #modDeleteBtn {{
                background-color: #FF5F56;
            }}
            #modDeleteBtn:hover {{
                background-color: #E0443E;
            }}
        """

    def _load_settings(self) -> None:
        self.nick_input.setText(self.settings.value("username", ""))
        saved_ram = int(self.settings.value("ram_gb", 4))
        self.ram_slider.setValue(min(saved_ram, self.ram_slider.maximum()))
        self.perf_checkbox.setChecked(self.settings.value("performance_mode", "true") == "true")

        rpc_on = self.settings.value("rpc_enabled", "true") == "true"
        self.rpc_checkbox.blockSignals(True)
        self.rpc_checkbox.setChecked(rpc_on)
        self.rpc_checkbox.blockSignals(False)

    def _save_settings(self) -> None:
        self.settings.setValue("username", self.nick_input.text().strip())
        self.settings.setValue("version", self.version_combo.currentText())
        self.settings.setValue("performance_mode", "true" if self.perf_checkbox.isChecked() else "false")
        self.settings.setValue("ram_gb", self.ram_slider.value())

    def _fetch_versions(self) -> None:
        self._fetch_worker = VersionFetchWorker()
        self._register_worker(self._fetch_worker)
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

    def _prompt_java_download(self, runtime_name: str, on_download: callable) -> bool:
        """Shows a dialog offering to download a required Java runtime."""
        reply = QMessageBox.question(
            self,
            "Java Runtime Missing",
            f"Minecraft {self.version_combo.currentText()} requires the '{runtime_name}' "
            "Java runtime, which is not installed.\n\n"
            "Would you like the launcher to download and install it automatically?\n"
            "No administrator privileges are required.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            on_download()
            return True
        QMessageBox.warning(
            self,
            "Cannot Launch",
            "A compatible Java runtime is required to start Minecraft.\n"
            "Please install one manually or allow the launcher to download it."
        )
        return False

    def _launch_game(self) -> None:
        if self._launch_in_progress:
            return

        username = self.nick_input.text().strip()
        version = self.version_combo.currentText()

        if not username:
            QMessageBox.warning(self, "Invalid Username", "Please enter a username.")
            return

        if not version or version == "Loading versions..." or not self.version_combo.isEnabled():
            QMessageBox.warning(self, "Launcher Busy", "Please wait for the version list to load.")
            return

        self._save_settings()
        self._launch_in_progress = True

        required_runtime = None
        try:
            runtime_info = minecraft_launcher_lib.runtime.get_version_runtime_information(
                version, self.minecraft_dir
            )
            required_runtime = runtime_info.get("name") if runtime_info else None
        except Exception:
            pass

        def start_launch(java_exec: Optional[str] = None):
            self._set_ui_enabled(False)
            self._show_progress(True, "Preparing")
            ram = self.ram_slider.value()
            perf = self.perf_checkbox.isChecked() and is_fabric_compatible(version)

            self._update_rpc(state="In-Game", details=f"Playing Minecraft {version}")

            self._launch_worker = LaunchWorker(username, version, self.minecraft_dir,
                                               ram, perf, java_path=java_exec)
            self._register_worker(self._launch_worker)
            self._launch_worker.progress_updated.connect(self._on_launch_progress)
            self._launch_worker.launch_success.connect(self._on_launch_success)
            self._launch_worker.game_exited.connect(self._on_game_exited)
            self._launch_worker.error_occurred.connect(self._on_launch_error)
            self._launch_worker.performance_mods_installed.connect(self._refresh_installed_mods)
            self._launch_worker.start()

        if required_runtime:
            java_exec = minecraft_launcher_lib.runtime.get_executable_path(
                required_runtime, self.minecraft_dir
            )
            if not java_exec:
                def on_download():
                    self._set_ui_enabled(False)
                    self._show_progress(True, "Downloading Java")
                    self._java_worker = JavaDownloadWorker(required_runtime, self.minecraft_dir)
                    self._register_worker(self._java_worker)
                    self._java_worker.progress.connect(self._on_java_progress)
                    self._java_worker.finished.connect(lambda: start_launch(java_exec))
                    self._java_worker.error.connect(self._on_java_error)
                    self._java_worker.start()

                if not self._prompt_java_download(required_runtime, on_download):
                    self._launch_in_progress = False
                return
            else:
                start_launch(java_exec)
        else:
            java_exec = None
            if not shutil.which("java"):
                legacy_exec = minecraft_launcher_lib.runtime.get_executable_path(
                    "jre-legacy", self.minecraft_dir
                )
                if not legacy_exec:
                    def on_download():
                        self._set_ui_enabled(False)
                        self._show_progress(True, "Downloading Java")
                        self._java_worker = JavaDownloadWorker("jre-legacy", self.minecraft_dir)
                        self._register_worker(self._java_worker)
                        self._java_worker.progress.connect(self._on_java_progress)
                        self._java_worker.finished.connect(lambda: start_launch(None))
                        self._java_worker.error.connect(self._on_java_error)
                        self._java_worker.start()

                    if not self._prompt_java_download("legacy runtime", on_download):
                        self._launch_in_progress = False
                    return
                java_exec = legacy_exec
            start_launch(java_exec)

    def _on_java_progress(self, status: str, percent: int) -> None:
        if percent >= 0:
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"Downloading Java: {percent}%")
        else:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Downloading Java...")

    def _on_java_error(self, error_message: str) -> None:
        self._set_ui_enabled(True)
        self._show_progress(False)
        self._launch_in_progress = False
        QMessageBox.critical(
            self,
            "Java Install Error",
            f"Failed to install portable Java runtime:\n\n{error_message}",
        )

    def _on_launch_progress(self, status: str, percent: int) -> None:
        if percent >= 0:
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"Installing: {percent}%")
        else:
            label = status[:20] + "..." if len(status) > 20 else status
            self.progress_bar.setFormat(f"{label}...")

    def _on_launch_success(self) -> None:
        self._fade_out_with_shrink(self.hide)

    def _on_game_exited(self) -> None:
        self._launch_in_progress = False
        self.setWindowOpacity(0.0)
        self.show()
        self._set_ui_enabled(True)
        self._show_progress(False)
        self.play_button.setText("Play")
        self._update_rpc(state="Free Non-Premium Launcher", details="Playing Minecraft")

    def _on_launch_error(self, error_message: str) -> None:
        self._launch_in_progress = False
        self._set_ui_enabled(True)
        self._show_progress(False)
        self.play_button.setText("Play")
        QMessageBox.critical(
            self,
            "Launch Error",
            f"An error occurred while launching Minecraft:\n\n{error_message}",
        )
        self._update_rpc(state="Free Non-Premium Launcher", details="Playing Minecraft")


if __name__ == "__main__":
    silence_asyncio_windows_bugs()

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "vanta.launcher.minecraft.1.1"
            )
        except Exception as e:
            sys.stderr.write(f"Failed to configure taskbar AppUserModelID: {e}\n")

    app = QApplication(sys.argv)
    launcher = MinecraftLauncher()
    launcher.show()
    sys.exit(app.exec())
