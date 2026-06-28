import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
)

from config import load_config, save_config, update_current_game_id
from cloud_sync_service import (
    download_game,
    get_remote_info as service_get_remote_info,
    update_game_metadata,
    upload_game_archive,
)
from constants import APP_DATA_DIR_NAME, CONFIG_FILE_NAME
from save_manager import snapshot_save_directory
from utils import default_device_name, now_text, parse_transfer_status, remote_zip_path_from_game_name, remote_zip_path_from_input


WINDOW_POLL_INTERVAL = 0.5

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [EnumWindowsProc, ctypes.c_void_p]
user32.EnumWindows.restype = ctypes.c_bool
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindow.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = ctypes.c_bool
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_ulong),
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

GW_OWNER = 4

LAUNCHER_STYLE = """
QDialog, QMessageBox {
    background: #edf3fa;
    color: #17283b;
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
}
QLabel {
    color: #24435f;
    background: transparent;
}
QComboBox {
    min-height: 38px;
    padding: 0 8px;
    color: #173e63;
    background: #ffffff;
    border: 1px solid #9aa8b5;
    border-radius: 2px;
    selection-background-color: #0878d1;
    selection-color: #ffffff;
}
QComboBox:hover {
    background: #ffffff;
    border-color: #70add5;
}
QComboBox:focus {
    border: 2px solid #268cc7;
}
QComboBox::drop-down {
    border: none;
    width: 32px;
    background: #eef2f6;
    border-left: 1px solid #9aa8b5;
}
QComboBox:hover::drop-down {
    background: #e2edf6;
}
QComboBox QAbstractItemView {
    color: #173e63;
    background: #ffffff;
    border: 1px solid #7f8c98;
    selection-background-color: #0878d1;
    selection-color: #ffffff;
}
QPushButton {
    min-height: 38px;
    padding: 0 18px;
    color: #183f63;
    font-weight: 700;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e9f2fa);
    border: 1px solid #abc4da;
    border-radius: 10px;
}
QPushButton:hover {
    color: #075f9c;
    border-color: #5ba6d7;
    background: #e0f1fc;
}
QPushButton:pressed {
    background: #cfe6f6;
}
QPushButton:disabled {
    color: #9aaaba;
    background: #e4ebf2;
    border-color: #ced9e3;
}
QPushButton[text="下载"] {
    color: #ffffff;
    border-color: #1769aa;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1769aa, stop:1 #268ee2);
}
QPushButton[text="上传"] {
    color: #ffffff;
    border-color: #1769aa;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1769aa, stop:1 #268ee2);
}
QPushButton[text="下载"]:hover, QPushButton[text="上传"]:hover {
    border-color: #104f81;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #125b94, stop:1 #197dca);
}
QProgressBar {
    min-height: 16px;
    color: transparent;
    text-align: center;
    background: #dce7f1;
    border: 1px solid #c4d4e2;
    border-radius: 8px;
}
QProgressBar::chunk {
    border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #08a88a, stop:0.5 #279ed4, stop:1 #3568d4);
}
"""


def pump_ui(delay_seconds: float = WINDOW_POLL_INTERVAL) -> None:
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    if app is not None:
        app.processEvents()


def resolve_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if sys.argv and sys.argv[0]:
        return Path(sys.argv[0]).resolve().parent
    return Path.cwd()


def resolve_data_dir() -> Path:
    candidates: list[Path] = []
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / APP_DATA_DIR_NAME)
        candidates.append(Path.home() / "AppData" / "Local" / APP_DATA_DIR_NAME)
    else:
        candidates.append(Path.home() / ".config" / APP_DATA_DIR_NAME)
    candidates.append(resolve_app_dir())

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return resolve_app_dir()


def resolve_config_path() -> Path:
    return resolve_data_dir() / CONFIG_FILE_NAME


class LauncherTrayController(QObject):
    def __init__(self, app: QApplication, icon: QIcon) -> None:
        super().__init__(app)
        self.phase = "idle"
        self.exit_requested = False
        self.menu = QMenu()
        self.exit_action = QAction("退出启动器", self.menu)
        self.exit_action.triggered.connect(self.request_exit)
        self.menu.addAction(self.exit_action)
        self.tray_icon: QSystemTrayIcon | None = None

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(icon, app)
            self.tray_icon.setToolTip("游戏云存档启动器")
            self.tray_icon.setContextMenu(self.menu)
            self.tray_icon.show()

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def request_exit(self) -> None:
        if self.phase == "syncing":
            QMessageBox.information(None, "暂时无法退出", "当前正在同步存档，请等待操作完成后再退出启动器。")
            return

        if self.phase == "monitoring":
            result = QMessageBox.question(
                None,
                "退出启动器",
                "退出后将停止监控，游戏关闭后不会自动上传存档。\n\n是否退出启动器？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                return

        self.exit_requested = True
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.close()

    def close(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.hide()
            self.tray_icon.setContextMenu(None)
            self.tray_icon.deleteLater()
            self.tray_icon = None
        self.menu.clear()
        self.menu.deleteLater()


launcher_tray: LauncherTrayController | None = None


def launcher_exit_requested() -> bool:
    return bool(launcher_tray and launcher_tray.exit_requested)


def load_saved_config_with_legacy_fallback() -> dict:
    config_path = resolve_config_path()
    saved = load_config(config_path)
    if saved:
        return saved

    legacy_path = resolve_app_dir() / CONFIG_FILE_NAME
    if legacy_path != config_path:
        legacy = load_config(legacy_path)
        if legacy:
            try:
                save_config(config_path, legacy)
            except OSError:
                pass
            return legacy
    return {}


def build_default_config() -> dict:
    return {
        "token": "",
        "repo": "",
        "branch": "main",
        "device_name": default_device_name(),
        "games": [
            {
                "id": "game_1",
                "name": "Game1",
                "game_root_path": "",
                "save_path": "",
                "remote_zip_path": remote_zip_path_from_game_name("Game1"),
                "emulator_path": "",
                "target_window": None,
                "pending_restore": None,
                "detect_type": "manual",
                "last_uploaded_at": "",
                "last_downloaded_zip_sha256": "",
            }
        ],
        "current_game_id": "game_1",
    }


def normalize_pending_restore_state(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    backup_dir = str(value.get("backup_dir", "")).strip()
    source_save_dir = str(value.get("source_save_dir", "")).strip()
    created_at = str(value.get("created_at", "")).strip()
    if not backup_dir or not source_save_dir:
        return None
    return {
        "backup_dir": backup_dir,
        "source_save_dir": source_save_dir,
        "created_at": created_at or now_text(),
    }


def normalize_target_window(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    process_name = str(value.get("process_name", "")).strip()
    class_name = str(value.get("class_name", "")).strip()
    title_keyword = str(value.get("title_keyword", "")).strip()
    if not process_name or not class_name:
        return None
    return {
        "process_name": process_name,
        "class_name": class_name,
        "title_keyword": title_keyword,
    }


def normalize_config(saved: dict) -> dict:
    base = build_default_config()
    if not isinstance(saved, dict):
        return base

    games_raw = saved.get("games")
    games: list[dict] = []
    if isinstance(games_raw, list) and games_raw:
        for index, item in enumerate(games_raw, start=1):
            if not isinstance(item, dict):
                continue
            game_id = str(item.get("id") or f"game_{index}")
            game_name = str(item.get("name") or f"Game{index}")
            remote_zip_path = remote_zip_path_from_input(str(item.get("remote_zip_path", "")))
            if not remote_zip_path:
                remote_zip_path = remote_zip_path_from_game_name(game_name)
            games.append(
                {
                    "id": game_id,
                    "name": game_name,
                    "game_root_path": str(item.get("game_root_path", "")),
                    "save_path": str(item.get("save_path", "")),
                    "remote_zip_path": remote_zip_path,
                    "emulator_path": str(item.get("emulator_path", "")),
                    "target_window": normalize_target_window(item.get("target_window")),
                    "pending_restore": normalize_pending_restore_state(item.get("pending_restore")),
                    "detect_type": str(item.get("detect_type", "manual")),
                    "last_uploaded_at": str(item.get("last_uploaded_at", "")),
                    "last_downloaded_zip_sha256": str(item.get("last_downloaded_zip_sha256", "")).strip(),
                }
            )

    if not games:
        games = base["games"]

    current_game_id = str(saved.get("current_game_id") or games[0]["id"])
    if current_game_id not in {game["id"] for game in games}:
        current_game_id = games[0]["id"]

    return {
        "token": str(saved.get("token", "")),
        "repo": str(saved.get("repo", "")),
        "branch": "main",
        "device_name": default_device_name(),
        "games": games,
        "current_game_id": current_game_id,
    }


def get_game_by_id(config_data: dict, game_id: str) -> dict:
    for game in config_data["games"]:
        if game["id"] == game_id:
            return game
    raise RuntimeError(f"绑定的游戏不存在或已被删除：{game_id}")


def current_game(config_data: dict) -> dict:
    return get_game_by_id(config_data, str(config_data.get("current_game_id", "")))


def format_remote_info_text(info: dict) -> str:
    if info.get("not_uploaded"):
        return "云端：尚未上传存档"
    uploaded_at = str(info.get("uploaded_at", "")).strip() or "未知"
    device_name = str(info.get("device_name", "")).strip() or "未知"
    return "\n".join(
        [
            f"云端最近上传时间：{uploaded_at}",
            f"云端最近上传设备：{device_name}",
        ]
    )


def format_local_info_text(game: dict) -> str:
    uploaded_at = str(game.get("last_uploaded_at", "")).strip()
    if not uploaded_at:
        return "本地记录：没有上传记录"
    return f"本地记录最近上传时间：{uploaded_at}"


def get_remote_info(config_data: dict, game: dict) -> dict:
    return service_get_remote_info(config_data, str(game["id"]))


def show_error_message(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


def show_timed_info(title: str, message: str, milliseconds: int = 2000) -> None:
    if launcher_exit_requested():
        return
    box = QMessageBox(QMessageBox.Information, title, message, QMessageBox.NoButton)
    QTimer.singleShot(milliseconds, box.accept)
    box.exec_()


def ask_retry_or_skip(title: str, message: str) -> bool:
    if launcher_exit_requested():
        return False
    box = QMessageBox(QMessageBox.Warning, title, f"{message}\n\n请检查网络后重试。", QMessageBox.NoButton)
    retry_button = box.addButton("重试", QMessageBox.AcceptRole)
    box.addButton("跳过", QMessageBox.RejectRole)
    box.exec_()
    return not launcher_exit_requested() and box.clickedButton() is retry_button


def select_game_for_launch(config_data: dict, fixed_game_id: str | None = None) -> dict | None:
    if fixed_game_id:
        return get_game_by_id(config_data, fixed_game_id)

    games = config_data["games"]
    names = [str(game["name"]) for game in games]
    current_id = str(config_data.get("current_game_id", ""))
    current_index = next((index for index, game in enumerate(games) if game["id"] == current_id), 0)
    selected_name, accepted = QInputDialog.getItem(
        None,
        "选择游戏",
        "请选择要启动的游戏：",
        names,
        current_index,
        False,
    )
    if not accepted:
        return None

    for game in games:
        if game["name"] == selected_name:
            config_data["current_game_id"] = game["id"]
            update_current_game_id(resolve_config_path(), str(game["id"]))
            return game
    return None


class DownloadPromptDialog(QDialog):
    def __init__(
        self,
        config_data: dict,
        fixed_game_id: str | None = None,
        program_started: bool = False,
    ) -> None:
        super().__init__(None)
        self.config_data = config_data
        self.fixed_game_id = fixed_game_id
        self.program_started = program_started
        self.setWindowTitle("下载云存档")
        self.setWindowFlags(Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.choice = "skip"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("当前游戏："))

        self.combo = QComboBox()
        self.combo.setToolTip("点击此处切换当前游戏")
        self.combo.setCursor(Qt.PointingHandCursor)
        current_id = fixed_game_id or str(config_data.get("current_game_id", ""))
        current_index = 0
        for index, game in enumerate(config_data["games"]):
            if fixed_game_id and game["id"] != fixed_game_id:
                continue
            self.combo.addItem(str(game["name"]), game["id"])
            if game["id"] == current_id:
                current_index = self.combo.count() - 1
        self.combo.setCurrentIndex(current_index)
        self.combo.setEnabled(not fixed_game_id)
        self.combo.currentIndexChanged.connect(self.refresh_info)
        if fixed_game_id:
            game_name_label = QLabel(str(get_game_by_id(config_data, fixed_game_id)["name"]))
            game_name_label.setStyleSheet(
                "min-height: 38px; padding: 0 8px; color: #173e63; background: #ffffff;"
                " border: 1px solid #9aa8b5; border-radius: 2px;"
            )
            layout.addWidget(game_name_label)
        else:
            layout.addWidget(self.combo)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        buttons = QHBoxLayout()
        self.download_button = QPushButton("下载")
        self.skip_button = QPushButton("跳过")
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.skip_button)
        layout.addLayout(buttons)

        self.download_button.clicked.connect(self.choose_download)
        self.skip_button.clicked.connect(self.choose_skip)

        self.refresh_info()

    def selected_game_id(self) -> str:
        return self.fixed_game_id or str(self.combo.currentData())

    def refresh_info(self) -> None:
        game = get_game_by_id(self.config_data, self.selected_game_id())
        self.config_data["current_game_id"] = game["id"]
        update_current_game_id(resolve_config_path(), str(game["id"]))
        try:
            remote_info = get_remote_info(self.config_data, game)
            remote_text = format_remote_info_text(remote_info)
            can_download = not remote_info.get("not_uploaded")
        except Exception as exc:
            remote_text = f"云端信息读取失败：{exc}"
            can_download = False

        action_text = (
            "目标程序已启动。现在是否下载这个游戏对应的云存档？"
            if self.program_started
            else "现在是否下载这个游戏对应的云存档？\n选择完成后将启动该游戏配置的模拟器或游戏程序。"
        )
        lines = [
            format_local_info_text(game),
            remote_text,
            "",
            action_text,
        ]
        self.info_label.setText("\n".join(lines))
        self.download_button.setEnabled(can_download)

    def choose_download(self) -> None:
        self.choice = "download"
        self.accept()

    def choose_skip(self) -> None:
        self.choice = "skip"
        self.accept()


class UploadPromptDialog(QDialog):
    def __init__(self, game: dict) -> None:
        super().__init__(None)
        self.setWindowTitle("确认上传")
        self.setWindowFlags(Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.choice = "skip"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("当前游戏："))

        game_name_label = QLabel(str(game["name"]))
        game_name_label.setStyleSheet(
            "min-height: 38px; padding: 0 12px; color: #173e63; background: #fbfdff;"
            " border: 1px solid #b9cde0; border-radius: 10px;"
        )
        layout.addWidget(game_name_label)

        lines = [
            "目标窗口已关闭。",
            "现在是否上传这个游戏对应的云存档？",
        ]
        info_label = QLabel("\n".join(lines))
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        buttons = QHBoxLayout()
        upload_button = QPushButton("上传")
        skip_button = QPushButton("跳过")
        buttons.addWidget(upload_button)
        buttons.addWidget(skip_button)
        layout.addLayout(buttons)

        upload_button.clicked.connect(self.choose_upload)
        skip_button.clicked.connect(self.choose_skip)

    def choose_upload(self) -> None:
        self.choice = "upload"
        self.accept()

    def choose_skip(self) -> None:
        self.choice = "skip"
        self.accept()


class ProgressDialog(QDialog):
    def __init__(self, title: str, status: str) -> None:
        super().__init__(None)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowTitleHint | Qt.CustomizeWindowHint)
        self.setModal(True)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.status_label = QLabel(status)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        row.addWidget(self.progress_bar, 1)

        self.percent_label = QLabel("0%")
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.percent_label.setMinimumWidth(48)
        row.addWidget(self.percent_label)

        layout.addLayout(row)

        transfer_row = QHBoxLayout()
        self.speed_label = QLabel("网络速度：--")
        self.size_label = QLabel("ZIP 文件大小：--")
        self.size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        transfer_row.addWidget(self.speed_label)
        transfer_row.addWidget(self.size_label)
        layout.addLayout(transfer_row)

    def update_progress(self, value: int, status: str) -> None:
        message, speed, file_size = parse_transfer_status(status)
        self.progress_bar.setValue(value)
        self.percent_label.setText(f"{value}%")
        self.status_label.setText(message)
        if speed:
            self.speed_label.setText(f"网络速度：{speed}")
        if file_size:
            self.size_label.setText(f"ZIP 文件大小：{file_size}")


class TargetWindowWaitDialog(QDialog):
    def __init__(self, game: dict, target_window: dict) -> None:
        super().__init__(None)
        self.target_window = target_window
        self.hwnd = 0
        self.setWindowTitle("等待目标窗口")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setWindowOpacity(0.9)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("targetWindowWaitPanel")
        panel.setStyleSheet(
            """
            QFrame#targetWindowWaitPanel {
                background: rgba(237, 243, 250, 245);
                border: 1px solid #8db8d8;
                border-radius: 14px;
            }
            """
        )
        layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(10)

        status_label = QLabel(
            f"正在等待目标游戏窗口……\n\n当前游戏：{game['name']}\n"
            f"目标进程：{target_window['process_name']}"
        )
        status_label.setWordWrap(True)
        panel_layout.addWidget(status_label)

        cancel_button = QPushButton("取消监控")
        cancel_button.clicked.connect(self.reject)
        panel_layout.addWidget(cancel_button)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.find_target_window)
        self.poll_timer.start(int(WINDOW_POLL_INTERVAL * 1000))
        QTimer.singleShot(0, self.find_target_window)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center().x() - self.width() // 2, available.top() + 24)

    def find_target_window(self) -> None:
        hwnd = _find_target_window(self.target_window)
        if not hwnd:
            return
        self.hwnd = hwnd
        self.poll_timer.stop()
        self.accept()


class MetadataWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config_data: dict, game_id: str) -> None:
        super().__init__()
        self.config_data = config_data
        self.game_id = game_id

    def run(self) -> None:
        try:
            self.finished.emit(service_get_remote_info(self.config_data, self.game_id))
        except Exception as exc:
            self.failed.emit(str(exc))


class DownloadWorker(QObject):
    progress_changed = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, config_data: dict, game_id: str) -> None:
        super().__init__()
        self.config_data = config_data
        self.game_id = game_id

    def emit_progress(self, value: float, status: str) -> None:
        self.progress_changed.emit(int(max(0, min(100, value))), status)

    def run(self) -> None:
        try:
            result = download_game(
                self.config_data,
                self.game_id,
                resolve_config_path(),
                resolve_data_dir(),
                self.emit_progress,
            )
            time.sleep(0.2)
            self.finished.emit(str(result["message"]), result["pending_restore"])
        except Exception as exc:
            self.failed.emit(str(exc))


class ArchiveUploadWorker(QObject):
    progress_changed = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config_data: dict, game_id: str) -> None:
        super().__init__()
        self.config_data = config_data
        self.game_id = game_id

    def emit_progress(self, value: float, status: str) -> None:
        self.progress_changed.emit(int(max(0, min(100, value))), status)

    def run(self) -> None:
        try:
            result = upload_game_archive(
                self.config_data,
                self.game_id,
                self.emit_progress,
            )
            time.sleep(0.2)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MetadataUpdateWorker(QObject):
    progress_changed = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config_data: dict, game_id: str, metadata: dict, upload_speed: float | None) -> None:
        super().__init__()
        self.config_data = config_data
        self.game_id = game_id
        self.metadata = metadata
        self.upload_speed = upload_speed

    def emit_progress(self, value: float, status: str) -> None:
        self.progress_changed.emit(int(max(0, min(100, value))), status)

    def run(self) -> None:
        try:
            result = update_game_metadata(
                self.config_data,
                self.game_id,
                resolve_config_path(),
                self.metadata,
                self.emit_progress,
                upload_speed=self.upload_speed,
            )
            time.sleep(0.2)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def run_worker_dialog(worker: QObject, title: str, initial_status: str) -> tuple[bool, object]:
    dialog = ProgressDialog(title, initial_status)
    thread = QThread()
    result_holder = {"ok": False, "payload": ""}

    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    if hasattr(worker, "progress_changed"):
        worker.progress_changed.connect(dialog.update_progress)

    if hasattr(worker, "finished"):
        def on_finished(*args):
            result_holder["ok"] = True
            if not args:
                result_holder["payload"] = ""
            elif len(args) == 1:
                result_holder["payload"] = args[0]
            else:
                result_holder["payload"] = args
            dialog.accept()

        worker.finished.connect(on_finished)

    if hasattr(worker, "failed"):
        def on_failed(message: str):
            result_holder["ok"] = False
            result_holder["message"] = message
            dialog.reject()

        worker.failed.connect(on_failed)
        worker.failed.connect(thread.quit)

    if hasattr(worker, "finished"):
        worker.finished.connect(thread.quit)

    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    if launcher_tray is not None:
        launcher_tray.set_phase("syncing")
    try:
        thread.start()
        dialog.exec_()

        if thread.isRunning():
            thread.quit()
            thread.wait()

        return result_holder["ok"], result_holder["payload"]
    finally:
        if launcher_tray is not None:
            launcher_tray.set_phase("idle")


def launch_configured_program(game: dict) -> subprocess.Popen:
    emulator_path = str(game.get("emulator_path", "")).strip()
    if not emulator_path:
        raise FileNotFoundError("未设置模拟器/游戏路径。请先到 GameCloudSave 设置页填写“模拟器/游戏路径”。")
    if not Path(emulator_path).exists():
        raise FileNotFoundError(f"找不到模拟器或游戏程序：\n{emulator_path}")
    return subprocess.Popen([emulator_path], close_fds=True)


def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _window_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _process_name_for_window(hwnd: int) -> str:
    process_id = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
    if not process_handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(process_handle)


def _find_target_window(target_window: dict) -> int:
    found_hwnd = 0
    fallback_hwnd = 0
    expected_process = str(target_window["process_name"]).casefold()
    expected_class = str(target_window["class_name"]).casefold()
    title_keyword = str(target_window.get("title_keyword", "")).casefold()

    @EnumWindowsProc
    def enum_callback(hwnd, _lparam):
        nonlocal found_hwnd, fallback_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if _window_class_name(hwnd).casefold() != expected_class:
            return True
        if _process_name_for_window(hwnd).casefold() != expected_process:
            return True
        if title_keyword and title_keyword not in _window_text(hwnd).casefold():
            if not fallback_hwnd:
                fallback_hwnd = int(hwnd)
            return True
        found_hwnd = int(hwnd)
        return False

    user32.EnumWindows(enum_callback, 0)
    return found_hwnd or fallback_hwnd


def wait_for_target_window(game: dict, target_window: dict) -> int:
    dialog = TargetWindowWaitDialog(game, target_window)
    if launcher_tray is not None:
        launcher_tray.set_phase("waiting")
    try:
        if dialog.exec_() != QDialog.Accepted:
            return 0
        return dialog.hwnd
    finally:
        if launcher_tray is not None:
            launcher_tray.set_phase("idle")


def wait_for_window_close(hwnd: int) -> bool:
    if launcher_tray is not None:
        launcher_tray.set_phase("monitoring")
    try:
        while user32.IsWindow(hwnd):
            if launcher_exit_requested():
                return False
            pump_ui()
        return not launcher_exit_requested()
    finally:
        if launcher_tray is not None:
            launcher_tray.set_phase("idle")


def run_metadata_dialog(config_data: dict, game: dict) -> tuple[bool, dict | str]:
    dialog = ProgressDialog("读取云端信息", "正在读取云端信息...")
    dialog.progress_bar.setRange(0, 0)
    dialog.percent_label.setText("")
    thread = QThread()
    worker = MetadataWorker(config_data, str(game["id"]))
    result_holder: dict[str, object] = {"ok": False, "result": ""}

    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    def on_finished(result: object) -> None:
        result_holder["ok"] = True
        result_holder["result"] = result
        dialog.accept()

    def on_failed(message: str) -> None:
        result_holder["ok"] = False
        result_holder["result"] = message
        dialog.reject()

    worker.finished.connect(on_finished)
    worker.finished.connect(thread.quit)
    worker.failed.connect(on_failed)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    if launcher_tray is not None:
        launcher_tray.set_phase("syncing")
    try:
        thread.start()
        dialog.exec_()

        if thread.isRunning():
            thread.quit()
            thread.wait()

        return bool(result_holder["ok"]), result_holder["result"]
    finally:
        if launcher_tray is not None:
            launcher_tray.set_phase("idle")


def fetch_remote_info_with_retry(config_data: dict, game: dict) -> tuple[dict | None, bool]:
    while True:
        if launcher_exit_requested():
            return None, False
        ok, result = run_metadata_dialog(config_data, game)
        if launcher_exit_requested():
            return None, False
        if ok and isinstance(result, dict):
            return result, True
        if ask_retry_or_skip("云端信息读取失败", str(result)):
            continue
        show_timed_info("已跳过", "云端信息读取失败，已跳过本次云端检查。", 2000)
        return None, False


def run_download_with_retry(config_data: dict, game: dict) -> bool:
    while True:
        if launcher_exit_requested():
            return False
        worker = DownloadWorker(config_data, game["id"])
        ok, payload = run_worker_dialog(worker, "下载进度", "准备下载云端存档...")
        if launcher_exit_requested():
            return False
        if ok and isinstance(payload, tuple) and len(payload) >= 1:
            show_timed_info("下载成功", str(payload[0]), 2000)
            return True
        if ask_retry_or_skip("下载失败", str(payload)):
            continue
        show_timed_info("下载失败", "下载失败，已跳过本次下载。", 2000)
        return False


def run_upload_with_retry(config_data: dict, game: dict) -> bool:
    archive_result: dict | None = None
    while archive_result is None:
        if launcher_exit_requested():
            return False
        worker = ArchiveUploadWorker(config_data, game["id"])
        ok, payload = run_worker_dialog(worker, "上传进度", "准备上传本地存档...")
        if launcher_exit_requested():
            return False
        if ok and isinstance(payload, dict):
            archive_result = payload
            break
        if not ask_retry_or_skip("上传失败", str(payload)):
            show_timed_info("上传失败", "上传失败，已结束本次上传。", 2000)
            return False

    while True:
        if launcher_exit_requested():
            return False
        metadata = dict(archive_result["metadata"])
        worker = MetadataUpdateWorker(
            config_data,
            game["id"],
            metadata,
            archive_result.get("upload_speed"),
        )
        ok, payload = run_worker_dialog(worker, "同步进度", "正在更新云端信息...")
        if launcher_exit_requested():
            return False
        if ok and isinstance(payload, dict):
            show_timed_info("上传成功", str(payload["message"]), 2000)
            return True
        if ask_retry_or_skip("Metadata 更新失败", str(payload)):
            continue
        show_timed_info(
            "同步未完成",
            "存档已上传，但设备标记更新失败。下次启动时可能仍会提示下载。",
            2000,
        )
        return False


def confirm_risky_upload() -> bool:
    if launcher_exit_requested():
        return False
    result = QMessageBox.warning(
        None,
        "上传风险确认",
        "启动前应下载云端存档，但本次未完成。\n\n"
        "当前本地存档可能不是最新版本，继续上传可能覆盖云端较新的存档。\n\n"
        "是否仍要继续上传？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return result == QMessageBox.Yes


def prepare_startup_sync(config_data: dict, game: dict) -> bool:
    if launcher_exit_requested():
        return False
    remote_info, metadata_ok = fetch_remote_info_with_retry(config_data, game)
    if not metadata_ok:
        return False

    if remote_info is None:
        return False

    if remote_info.get("not_uploaded"):
        show_timed_info("跳过下载", "云端暂无存档，跳过下载。", 2000)
        return True

    remote_device = str(remote_info.get("device_name", "")).strip().casefold()
    if remote_device and remote_device == default_device_name().casefold():
        show_timed_info("跳过下载", "云存档来自此设备，跳过下载。", 2000)
        return True

    remote_zip_sha256 = str(remote_info.get("zip_sha256", "")).strip()
    local_downloaded_zip_sha256 = str(game.get("last_downloaded_zip_sha256", "")).strip()
    if remote_zip_sha256 and local_downloaded_zip_sha256 and remote_zip_sha256 == local_downloaded_zip_sha256:
        show_timed_info("跳过下载", "本地与云端存档一致，跳过下载。", 2000)
        return True

    return run_download_with_retry(config_data, game)


def launch_monitor_then_sync(config_data: dict, fixed_game_id: str | None = None) -> int:
    launch_game = select_game_for_launch(config_data, fixed_game_id)
    if launch_game is None or launcher_exit_requested():
        return 0

    target_window = normalize_target_window(launch_game.get("target_window"))
    if not target_window:
        raise RuntimeError(
            f"游戏“{launch_game['name']}”尚未记录目标窗口。\n\n"
            "请先到 GameCloudSave 设置页点击“记录目标窗口”。"
        )

    startup_sync_safe = prepare_startup_sync(config_data, launch_game)
    if launcher_exit_requested():
        return 0

    save_dir = Path(str(launch_game.get("save_path", "")).strip())
    baseline_snapshot = snapshot_save_directory(save_dir)
    if launcher_exit_requested():
        return 0

    launch_configured_program(launch_game)

    hwnd = wait_for_target_window(launch_game, target_window)
    if launcher_exit_requested():
        return 0
    if not hwnd:
        show_timed_info("已取消", "已取消目标窗口监控。", 2000)
        return 0
    if not wait_for_window_close(hwnd):
        return 0

    current_snapshot = snapshot_save_directory(save_dir)
    if launcher_exit_requested():
        return 0
    if current_snapshot == baseline_snapshot:
        show_timed_info("跳过上传", "存档未变化，跳过上传。", 2000)
        return 0

    if not startup_sync_safe and not confirm_risky_upload():
        show_timed_info("已取消", "已取消上传。", 2000)
        return 0

    return 0 if run_upload_with_retry(config_data, launch_game) else 1


def bound_game_id_from_args(args: list[str]) -> str | None:
    for index, arg in enumerate(args):
        if arg == "--game-id" and index + 1 < len(args):
            return args[index + 1].strip() or None
        if arg.startswith("--game-id="):
            return arg.split("=", 1)[1].strip() or None
    return None


def main() -> int:
    global launcher_tray
    app = QApplication(sys.argv)
    app.setStyleSheet(LAUNCHER_STYLE)
    resource_dir = Path(getattr(sys, "_MEIPASS", resolve_app_dir()))
    icon_path = resource_dir / "assets" / "game_cloud_save.ico"
    icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    app.setWindowIcon(icon)
    launcher_tray = LauncherTrayController(app, icon)
    try:
        config_data = normalize_config(load_saved_config_with_legacy_fallback())
        result = launch_monitor_then_sync(config_data, bound_game_id_from_args(sys.argv[1:]))
    except Exception as exc:
        if not launcher_exit_requested():
            show_error_message("启动失败", str(exc))
        result = 1
    finally:
        if launcher_tray is not None:
            launcher_tray.close()
        launcher_tray = None
        for widget in QApplication.topLevelWidgets():
            widget.close()
        app.processEvents()
        app.quit()
        app.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
