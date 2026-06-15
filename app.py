import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import load_config, save_config
from cloud_sync_service import download_game, get_remote_info, rollback_game, upload_game
from constants import APP_DATA_DIR_NAME, CONFIG_FILE_NAME, DEFAULT_GAME_ID
from utils import (
    default_device_name,
    format_size,
    format_timestamp,
    now_text,
    parse_time_text,
    parse_transfer_status,
    remote_zip_input_from_path,
    remote_zip_path_from_input,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_ulong),
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class GamesCloudSaveApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("游戏云存档")
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            dpi_scale = max(screen.logicalDotsPerInch() / 96.0, screen.devicePixelRatio())
            self.compact_dpi_layout = dpi_scale >= 1.25 or available.width() < 1500 or available.height() < 1000
            self.resize(min(1550, int(available.width() * 0.94)), min(1200, int(available.height() * 0.94)))
            self.setMinimumSize(min(1120, int(available.width() * 0.85)), min(820, int(available.height() * 0.85)))
        else:
            self.compact_dpi_layout = False
            self.resize(1550, 1200)
            self.setMinimumSize(1120, 820)

        self.app_dir = self._resolve_app_dir()
        self.resource_dir = Path(getattr(sys, "_MEIPASS", self.app_dir))
        icon_path = self.resource_dir / "assets" / "game_cloud_save.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.data_dir = self._resolve_data_dir()
        self.config_path = self.data_dir / CONFIG_FILE_NAME
        self.script_dir = self.app_dir
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.is_busy = False
        self.progress_dialog: QDialog | None = None
        self.progress_dialog_status_label: QLabel | None = None
        self.progress_dialog_bar: QProgressBar | None = None
        self.progress_dialog_percent_label: QLabel | None = None
        self.progress_dialog_speed_label: QLabel | None = None
        self.progress_dialog_size_label: QLabel | None = None
        self.startup_remote_refresh = False
        self.window_capture_seconds = 0

        saved = self._normalize_config(self._load_saved_config())
        self.config_data = saved
        self.games: list[dict] = saved["games"]
        self.current_game_id = str(saved.get("current_game_id") or self.games[0]["id"])
        current_game = self._current_game()
        saved_game_root = str(current_game.get("game_root_path", ""))
        detected_path = str(current_game.get("save_path", ""))
        self.local_info_cache: dict | None = None
        self.remote_info_cache: dict | None = None
        self.pending_restore_state = self._normalize_pending_restore_state(current_game.get("pending_restore"))

        self._build_ui(
            token=str(saved.get("token", "")),
            repo=str(saved.get("repo", "")),
            branch=str(saved.get("branch", "main")),
            emulator_path=str(current_game.get("emulator_path", "")),
            game_name=str(current_game.get("name", "未命名游戏")),
            games=self.games,
            current_game_id=self.current_game_id,
            game_root=saved_game_root or str(self.script_dir),
            save_path=str(current_game.get("save_path") or detected_path),
            remote_zip_path=remote_zip_input_from_path(str(current_game.get("remote_zip_path", ""))),
        )
        self._apply_styles()
        self._update_pending_restore_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start(100)

        QTimer.singleShot(200, self._refresh_on_startup)

    def _build_ui(
        self,
        token: str,
        repo: str,
        branch: str,
        emulator_path: str,
        game_name: str,
        games: list[dict],
        current_game_id: str,
        game_root: str,
        save_path: str,
        remote_zip_path: str,
    ) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        header_card = self._card()
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(6)

        title = QLabel("游戏云存档")
        title.setObjectName("TitleLabel")
        self.status_label = QLabel("需要修改仓库或目录时，可切到设置页。")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("SecondaryLabel")
        header_layout.addWidget(title)
        header_layout.addWidget(self.status_label)

        game_row = QHBoxLayout()
        game_row.setSpacing(10)
        game_row.addWidget(QLabel("当前游戏"))
        self.game_selector = QComboBox()
        self.game_selector.setObjectName("GameSelector")
        self.game_selector.setToolTip("点击此处切换当前游戏")
        self.game_selector.setCursor(Qt.PointingHandCursor)
        self.game_selector.currentIndexChanged.connect(self._on_game_selection_changed)
        game_row.addWidget(self.game_selector, 1)
        self.add_game_button = QPushButton("新增游戏")
        self.add_game_button.clicked.connect(self.add_game)
        self.rename_game_button = QPushButton("重命名")
        self.rename_game_button.clicked.connect(self.rename_current_game)
        self.delete_game_button = QPushButton("删除游戏")
        self.delete_game_button.clicked.connect(self.delete_current_game)
        game_row.addWidget(self.add_game_button)
        game_row.addWidget(self.rename_game_button)
        game_row.addWidget(self.delete_game_button)
        header_layout.addLayout(game_row)
        root_layout.addWidget(header_card)

        self.notebook = QTabWidget()
        root_layout.addWidget(self.notebook, 1)

        self.overview_tab = QWidget()
        self.settings_tab = QWidget()
        self.launcher_tab = QWidget()
        self.notebook.addTab(self.overview_tab, "概览")
        self.notebook.addTab(self.settings_tab, "设置")
        self.notebook.addTab(self.launcher_tab, "快捷存档游戏启动器")

        self._build_overview_tab()
        self._build_settings_tab(token, repo, branch, emulator_path, game_root, save_path, remote_zip_path)
        self._build_launcher_tab()
        self._populate_game_selector(games, current_game_id)
        self._refresh_target_window_label()

    def _build_overview_tab(self) -> None:
        layout = QVBoxLayout(self.overview_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(14)

        self.suggestion_label = None

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.refresh_all_button = QPushButton("刷新本地和云端信息")
        self.refresh_all_button.clicked.connect(self.refresh_all_info)
        self.upload_button = QPushButton("上传本地存档")
        self.upload_button.clicked.connect(self.start_upload)
        self.download_button = QPushButton("下载云端存档")
        self.download_button.clicked.connect(self.start_download)
        action_row.addWidget(self.refresh_all_button)
        action_row.addWidget(self.upload_button)
        action_row.addWidget(self.download_button)
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_info_panel("本地存档信息", "local"))
        splitter.addWidget(self._build_info_panel("云端存档信息", "remote"))
        splitter.setSizes([520, 520])
        layout.addWidget(splitter, 1)

    def _build_info_panel(self, title: str, side: str) -> QWidget:
        box = self._section_group(title)
        inner = QVBoxLayout(box)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setObjectName("InfoText")
        if side == "local":
            self.local_text = text
            self.rollback_backup_button = QPushButton("回退到最近一次下载前的本地存档")
            self.rollback_backup_button.clicked.connect(self.rollback_backup_result)
            inner.addWidget(self.rollback_backup_button)
        else:
            self.remote_text = text
        inner.addWidget(text)
        return box

    def _build_settings_tab(
        self,
        token: str,
        repo: str,
        branch: str,
        emulator_path: str,
        game_root: str,
        save_path: str,
        remote_zip_path: str,
    ) -> None:
        outer = QVBoxLayout(self.settings_tab)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(14)

        card = self._card()
        outer.addWidget(card)
        grid = QGridLayout(card)
        spacing = 8 if self.compact_dpi_layout else 12
        margin = 12 if self.compact_dpi_layout else 18
        grid.setContentsMargins(margin, margin, margin, margin)
        grid.setHorizontalSpacing(spacing)
        grid.setVerticalSpacing(spacing)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnMinimumWidth(0, self.fontMetrics().horizontalAdvance("模拟器/游戏路径") + 18)

        intro = QLabel("本地及云端设置")
        intro.setWordWrap(True)
        intro.setObjectName("SecondaryLabel")
        grid.addWidget(intro, 0, 0, 1, 3)

        self.token_edit = QLineEdit(token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.repo_edit = QLineEdit(repo)
        self.branch_edit = QLineEdit(branch)
        self.emulator_path_edit = QLineEdit(emulator_path)
        self.game_root_edit = QLineEdit(game_root)
        self.remote_zip_path_edit = QLineEdit(remote_zip_path)
        for edit in (
            self.token_edit,
            self.repo_edit,
            self.branch_edit,
            self.emulator_path_edit,
            self.game_root_edit,
            self.remote_zip_path_edit,
        ):
            edit.editingFinished.connect(self.auto_save_settings)
        self.save_path_label = QLabel(save_path)
        self.save_path_label.setWordWrap(True)
        self.open_save_folder_button = QPushButton("打开存档文件夹")
        self.open_save_folder_button.clicked.connect(self.open_save_folder)
        self.config_path_label = QLabel(f"配置保存在：{self.config_path}")
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setObjectName("SecondaryLabel")
        self.view_config_button = QPushButton("查看配置")
        self.view_config_button.clicked.connect(self.open_config_file)
        self.target_window_label = QLabel("")
        self.target_window_label.setWordWrap(True)
        self.target_window_label.setObjectName("SecondaryLabel")
        self.capture_window_button = QPushButton("记录目标窗口")
        self.capture_window_button.clicked.connect(self.start_target_window_capture)

        self._add_labeled_entry(grid, 1, "GitHub Token", self.token_edit)
        self._add_labeled_entry(grid, 2, "仓库名", self.repo_edit, hint="格式：用户名/仓库名")
        self._add_labeled_entry(grid, 3, "分支", self.branch_edit, hint="通常填 main")
        self._add_labeled_entry(
            grid,
            4,
            "存档所在目录",
            self.game_root_edit,
            browse_callback=self.pick_directory,
            extra_button=self.open_save_folder_button,
        )
        self._add_labeled_entry(
            grid,
            5,
            "云端存档目录名",
            self.remote_zip_path_edit,
            hint="填写目录名称，例如 games-botw-save；将自动生成 save_backup_latest.zip",
        )
        grid.addWidget(self.config_path_label, 6, 0, 1, 2)
        grid.addWidget(self.view_config_button, 6, 2)
        self._refresh_open_save_folder_button_state()

        action_row = QHBoxLayout()
        self.save_settings_button = QPushButton("保存设置")
        self.save_settings_button.clicked.connect(self.save_settings_and_notify)
        action_row.addWidget(self.save_settings_button)
        outer.addLayout(action_row)
        outer.addStretch(1)

    def _add_labeled_entry(
        self,
        layout: QGridLayout,
        row: int,
        label: str,
        widget: QWidget,
        hint: str | None = None,
        browse_callback=None,
        extra_button: QPushButton | None = None,
    ) -> None:
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(widget, row, 1)

        stack_buttons = self.compact_dpi_layout and bool(hint)
        right = QVBoxLayout() if stack_buttons else QHBoxLayout()
        right.setSpacing(6)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("SecondaryLabel")
            hint_label.setWordWrap(True)
            hint_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            right.addWidget(hint_label, 1)
        button_row = QHBoxLayout() if stack_buttons else right
        button_row.setSpacing(6)
        if browse_callback:
            button = QPushButton("浏览")
            button.clicked.connect(browse_callback)
            button_row.addWidget(button)
        if extra_button:
            button_row.addWidget(extra_button)
        button_row.addStretch(1)
        if stack_buttons:
            right.addLayout(button_row)

        holder = QWidget()
        holder.setLayout(right)
        layout.addWidget(holder, row, 2)

    def _build_launcher_tab(self) -> None:
        layout = QVBoxLayout(self.launcher_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(14)

        card = self._card()
        layout.addWidget(card)
        grid = QGridLayout(card)
        spacing = 8 if self.compact_dpi_layout else 12
        margin = 12 if self.compact_dpi_layout else 18
        grid.setContentsMargins(margin, margin, margin, margin)
        grid.setHorizontalSpacing(spacing)
        grid.setVerticalSpacing(spacing)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnMinimumWidth(0, self.fontMetrics().horizontalAdvance("模拟器/游戏路径") + 18)

        intro = QLabel("快捷存档游戏启动器")
        intro.setWordWrap(True)
        intro.setObjectName("SecondaryLabel")
        grid.addWidget(intro, 0, 0, 1, 3)
        self._add_labeled_entry(grid, 1, "模拟器/游戏路径", self.emulator_path_edit, browse_callback=self.pick_emulator)
        self._add_labeled_entry(
            grid,
            2,
            "目标窗口",
            self.target_window_label,
            hint="点击后在 5 秒内使用 Alt+Tab 切换到目标游戏或程序窗口",
            extra_button=self.capture_window_button,
        )

        self.create_launcher_shortcut_button = QPushButton("创建此游戏云存档启动器至桌面")
        self.create_launcher_shortcut_button.clicked.connect(self.create_current_game_launcher_shortcut)
        layout.addWidget(self.create_launcher_shortcut_button)
        layout.addStretch(1)

        # Keep the existing logging calls functional without exposing a logs page.
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.hide()

    def _apply_styles(self) -> None:
        font = QFont("Microsoft YaHei UI", 8 if self.compact_dpi_layout else 10)
        QApplication.instance().setFont(font)
        combo_arrow_path = (self.resource_dir / "assets" / "combo_down_arrow.png").as_posix()
        base_style = (
            """
            QMainWindow, QWidget {
                background: #edf3fa;
                color: #17283b;
            }
            QWidget#Card, QGroupBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #f7faff);
                border: 1px solid #c9d8e8;
                border-radius: 16px;
            }
            QGroupBox {
                margin-top: 14px;
                padding-top: 10px;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 8px;
                color: #173e63;
                background: #edf3fa;
                border-radius: 6px;
            }
            QLabel#TitleLabel {
                font-size: 24px;
                font-weight: 700;
                color: #102f4d;
            }
            QLabel#SecondaryLabel {
                color: #617991;
            }
            QLabel#SuggestionLabel {
                color: #087f69;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton {
                min-height: 42px;
                border-radius: 12px;
                border: 1px solid #b6cbe0;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #edf4fb);
                color: #183f63;
                padding: 0 16px;
                font-weight: 700;
            }
            QPushButton:hover {
                color: #075f9c;
                border-color: #65aee2;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f9fdff, stop:1 #dcefff);
            }
            QPushButton:pressed {
                background: #d5e9f8;
                border-color: #398fc9;
            }
            QPushButton:disabled {
                color: #9aaaba;
                background: #e8eef4;
                border-color: #d3dde7;
            }
            QPushButton[text="上传本地存档"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #087f69, stop:1 #16a085);
                color: #ffffff;
                border-color: #087f69;
            }
            QPushButton[text="上传本地存档"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #066d5b, stop:1 #11967d);
                border-color: #055f50;
            }
            QPushButton[text="下载云端存档"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1769aa, stop:1 #268ee2);
                color: #ffffff;
                border-color: #1769aa;
            }
            QPushButton[text="下载云端存档"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #125b94, stop:1 #197dca);
                border-color: #104f81;
            }
            QPushButton[text="回退到最近一次下载前的本地存档"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c97812, stop:1 #e6a52d);
                color: #ffffff;
                border-color: #bd6e0b;
            }
            QPushButton[text="回退到最近一次下载前的本地存档"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #af6508, stop:1 #d28f18);
                border-color: #9d5905;
            }
            QLineEdit, QPlainTextEdit, QComboBox {
                background: #fbfdff;
                border: 1px solid #c4d4e5;
                border-radius: 12px;
                padding: 10px 12px;
                selection-background-color: #56a9dd;
                selection-color: #ffffff;
            }
            QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover {
                border-color: #83b8dc;
                background: #ffffff;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 2px solid #268cc7;
                background: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox#GameSelector {
                border: 1px solid #9aa8b5;
                border-radius: 2px;
                background: #ffffff;
                padding: 4px 8px;
            }
            QComboBox#GameSelector::drop-down {
                width: 32px;
                background: #eef2f6;
                border-left: 1px solid #9aa8b5;
            }
            QComboBox#GameSelector::down-arrow {
                image: url(COMBO_ARROW_PATH);
                width: 14px;
                height: 14px;
            }
            QComboBox#GameSelector:hover {
                border-color: #268cc7;
                background: #ffffff;
            }
            QComboBox#GameSelector:hover::drop-down {
                background: #e2edf6;
            }
            QComboBox#GameSelector QAbstractItemView {
                background: #ffffff;
                border: 1px solid #7f8c98;
                selection-background-color: #0878d1;
                selection-color: #ffffff;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #173e63;
                border: 1px solid #9fc2dd;
                selection-background-color: #d9effc;
                selection-color: #0b4f7d;
            }
            QTabWidget::pane {
                border: 1px solid #c9d8e8;
                border-radius: 14px;
                background: #f9fbfe;
                top: -1px;
            }
            QTabBar::tab {
                background: #dfeaf5;
                color: #526d86;
                border: 1px solid #cad8e7;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 10px 18px;
                margin-right: 6px;
            }
            QTabBar::tab:hover {
                background: #eaf5fd;
                color: #17699f;
            }
            QTabBar::tab:selected {
                background: #f9fbfe;
                color: #0b5f94;
                border-bottom-color: #f9fbfe;
                font-weight: 700;
            }
            QProgressBar {
                background: #dce7f1;
                border: 1px solid #c4d4e2;
                border-radius: 8px;
                min-height: 16px;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #08a88a, stop:0.5 #279ed4, stop:1 #3568d4);
            }
            QScrollBar:vertical {
                background: #edf3f8;
                width: 11px;
                margin: 2px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #a8bfd3;
                min-height: 28px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #6fa6c9;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QSplitter::handle {
                background: transparent;
                height: 10px;
            }
            """
        ).replace("COMBO_ARROW_PATH", combo_arrow_path)
        compact_style = """
            QLabel#TitleLabel {
                font-size: 18px;
            }
            QLabel#SuggestionLabel {
                font-size: 11px;
            }
            QPushButton {
                min-height: 28px;
                border-radius: 8px;
                padding: 0 8px;
            }
            QLineEdit, QPlainTextEdit, QComboBox {
                border-radius: 8px;
                padding: 4px 7px;
            }
            QTabBar::tab {
                padding: 5px 10px;
                margin-right: 3px;
            }
        """
        self.setStyleSheet(base_style + (compact_style if self.compact_dpi_layout else ""))

    def _card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setFrameShape(QFrame.NoFrame)
        return frame

    def _resolve_app_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        if sys.argv and sys.argv[0]:
            return Path(sys.argv[0]).resolve().parent
        return Path.cwd()

    def _resolve_data_dir(self) -> Path:
        candidates: list[Path] = []
        if os.name == "nt":
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                candidates.append(Path(local_app_data) / APP_DATA_DIR_NAME)
            candidates.append(Path.home() / "AppData" / "Local" / APP_DATA_DIR_NAME)
        else:
            candidates.append(Path.home() / ".config" / APP_DATA_DIR_NAME)
        candidates.append(self.app_dir)

        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                continue
        return self.app_dir

    def _section_group(self, title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return box

    def closeEvent(self, event) -> None:
        self._hide_progress_dialog()
        self._save_config()
        super().closeEvent(event)

    def _token(self) -> str:
        return self.token_edit.text().strip()

    def _repo(self) -> str:
        return self.repo_edit.text().strip()

    def _branch(self) -> str:
        return self.branch_edit.text().strip() or "main"

    def _emulator_path(self) -> str:
        return self.emulator_path_edit.text().strip()

    def _game_root(self) -> str:
        return self.game_root_edit.text().strip()

    def _save_path(self) -> str:
        return self.save_path_label.text().strip()

    def _remote_zip_path(self) -> str:
        return remote_zip_path_from_input(self.remote_zip_path_edit.text())

    def _download_mode(self) -> str:
        return "overwrite"

    def _normalize_config(self, saved: dict) -> dict:
        token = str(saved.get("token", ""))
        repo = str(saved.get("repo", ""))
        branch = str(saved.get("branch", "main") or "main")

        games_raw = saved.get("games")
        games: list[dict] = []
        if isinstance(games_raw, list) and games_raw:
            for index, item in enumerate(games_raw, start=1):
                if not isinstance(item, dict):
                    continue
                game_id = str(item.get("id") or f"game_{index}")
                game_name = str(item.get("name") or f"游戏{index}")
                game_root = str(item.get("game_root_path", ""))
                save_path = str(item.get("save_path", ""))
                remote_zip_path = remote_zip_path_from_input(str(item.get("remote_zip_path", "")))
                pending_restore = self._normalize_pending_restore_state(item.get("pending_restore"))
                last_uploaded_at = str(item.get("last_uploaded_at", ""))
                games.append(
                    {
                        "id": game_id,
                        "name": game_name,
                        "game_root_path": game_root,
                        "save_path": save_path,
                        "remote_zip_path": remote_zip_path,
                        "emulator_path": str(item.get("emulator_path", "")),
                        "target_window": self._normalize_target_window(item.get("target_window")),
                        "download_mode": "overwrite",
                        "backup_before_overwrite": True,
                        "pending_restore": pending_restore,
                        "last_uploaded_at": last_uploaded_at,
                    }
                )

        if not games:
            legacy_game_root = str(saved.get("game_root_path", ""))
            legacy_save_path = str(saved.get("save_path", ""))
            legacy_remote = remote_zip_path_from_input(str(saved.get("remote_zip_path", "")))
            legacy_pending = self._normalize_pending_restore_state(saved.get("pending_restore"))
            games = [
                {
                    "id": DEFAULT_GAME_ID,
                    "name": "你的游戏",
                    "game_root_path": legacy_game_root,
                    "save_path": legacy_save_path,
                    "remote_zip_path": legacy_remote,
                    "emulator_path": "",
                    "target_window": None,
                    "download_mode": "overwrite",
                    "backup_before_overwrite": True,
                    "pending_restore": legacy_pending,
                    "last_uploaded_at": "",
                }
            ]

        current_game_id = str(saved.get("current_game_id") or games[0]["id"])
        if current_game_id not in {game["id"] for game in games}:
            current_game_id = games[0]["id"]

        return {
            "token": token,
            "repo": repo,
            "branch": branch,
            "device_name": default_device_name(),
            "games": games,
            "current_game_id": current_game_id,
        }

    def _current_game(self) -> dict:
        for game in self.games:
            if game["id"] == self.current_game_id:
                return game
        self.current_game_id = self.games[0]["id"]
        return self.games[0]

    def _populate_game_selector(self, games: list[dict], current_game_id: str) -> None:
        self.game_selector.blockSignals(True)
        self.game_selector.clear()
        current_index = 0
        for index, game in enumerate(games):
            self.game_selector.addItem(game["name"], game["id"])
            if game["id"] == current_game_id:
                current_index = index
        self.game_selector.setCurrentIndex(current_index)
        self.game_selector.blockSignals(False)

    def _update_current_game_from_ui(self) -> None:
        game = self._current_game()
        game["emulator_path"] = self._emulator_path()
        game["game_root_path"] = self._game_root()
        game["save_path"] = self._save_path()
        game["remote_zip_path"] = self._remote_zip_path()
        game["download_mode"] = "overwrite"
        game["backup_before_overwrite"] = True
        game["pending_restore"] = self.pending_restore_state

    def _load_current_game_into_ui(self) -> None:
        game = self._current_game()
        self.emulator_path_edit.setText(str(game.get("emulator_path", "")))
        self.game_root_edit.setText(str(game.get("game_root_path", "")))
        self.save_path_label.setText(str(game.get("save_path", "")))
        self._refresh_open_save_folder_button_state()
        self.remote_zip_path_edit.setText(remote_zip_input_from_path(str(game.get("remote_zip_path", ""))))
        self._refresh_target_window_label()
        self.pending_restore_state = self._normalize_pending_restore_state(game.get("pending_restore"))
        self._update_pending_restore_ui()

    def _sync_global_config_from_ui(self) -> None:
        self.config_data["token"] = self._token()
        self.config_data["repo"] = self._repo()
        self.config_data["branch"] = self._branch()
        self.config_data.pop("emulator_path", None)
        self.config_data["device_name"] = default_device_name()
        self.config_data["current_game_id"] = self.current_game_id

    def _on_game_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        self._update_current_game_from_ui()
        game_id = self.game_selector.itemData(index)
        if not game_id:
            return
        self.current_game_id = str(game_id)
        self.local_info_cache = None
        self.remote_info_cache = None
        self._load_current_game_into_ui()
        self._set_text(self.local_text, "")
        self._set_text(self.remote_text, "")
        self._refresh_suggestion()
        self._save_config()
        self.refresh_local_info()

    def add_game(self) -> None:
        name, ok = QInputDialog.getText(self, "新增游戏", "输入游戏名称：")
        name = (name or "").strip()
        if not ok or not name:
            return
        self._update_current_game_from_ui()
        new_id = f"game_{int(time.time())}"
        self.games.append(
            {
                "id": new_id,
                "name": name,
                "game_root_path": "",
                "save_path": "",
                "remote_zip_path": "",
                "emulator_path": "",
                "target_window": None,
                "download_mode": "overwrite",
                "backup_before_overwrite": True,
                "pending_restore": None,
                "last_uploaded_at": "",
            }
        )
        self.current_game_id = new_id
        self._populate_game_selector(self.games, self.current_game_id)
        self._load_current_game_into_ui()
        self._save_config()

    def rename_current_game(self) -> None:
        game = self._current_game()
        name, ok = QInputDialog.getText(self, "重命名游戏", "输入新的游戏名称：", text=game["name"])
        name = (name or "").strip()
        if not ok or not name:
            return
        game["name"] = name
        self._populate_game_selector(self.games, self.current_game_id)
        self._save_config()

    def delete_current_game(self) -> None:
        if len(self.games) <= 1:
            self._show_warning("无法删除", "至少需要保留一个游戏配置。")
            return
        game = self._current_game()
        result = QMessageBox.question(self, "删除游戏", f"确定删除“{game['name']}”的配置吗？")
        if result != QMessageBox.Yes:
            return
        self.games = [item for item in self.games if item["id"] != self.current_game_id]
        self.current_game_id = self.games[0]["id"]
        self.config_data["games"] = self.games
        self._populate_game_selector(self.games, self.current_game_id)
        self._load_current_game_into_ui()
        self._save_config()

    def save_settings_and_notify(self) -> None:
        if not self._save_config():
            self._show_error("保存失败", f"无法保存配置到：\n{self.config_path}")
            return
        self.append_log("设置已保存。")
        self._show_info("已保存", f"设置已保存到：\n{self.config_path}")

    def auto_save_settings(self) -> None:
        if self._save_config():
            self.status_label.setText("设置已自动保存。")

    def open_config_file(self) -> None:
        if not self.config_path.exists() and not self._save_config():
            self._show_error("打开失败", f"配置文件不存在且无法创建：\n{self.config_path}")
            return
        try:
            os.startfile(str(self.config_path))
        except OSError as exc:
            self._show_error("打开失败", f"无法打开配置文件：\n{exc}")

    def create_current_game_launcher_shortcut(self) -> None:
        if not self._save_config():
            self._show_error("保存失败", "当前游戏配置未能写入配置文件，无法创建快捷方式。")
            return
        latest = self._normalize_config(load_config(self.config_path))
        game = next(
            (item for item in latest["games"] if str(item.get("id", "")) == self.current_game_id),
            None,
        )
        if game is None:
            self._show_error("创建失败", "配置文件中找不到当前游戏，请保存后重试。")
            return
        missing: list[str] = []
        if not self._token():
            missing.append("GitHub Token")
        if not self._repo() or "/" not in self._repo():
            missing.append("仓库名")
        emulator_path = str(game.get("emulator_path", "")).strip()
        if not emulator_path or not Path(emulator_path).is_file():
            missing.append("模拟器/游戏路径")
        save_path = str(game.get("save_path", "")).strip()
        if not save_path or not Path(save_path).is_dir():
            missing.append("存档所在目录")
        if not str(game.get("remote_zip_path", "")).strip():
            missing.append("云端存档目录名")
        if not self._normalize_target_window(game.get("target_window")):
            missing.append("目标窗口")
        if missing:
            self._show_warning("当前游戏信息未完成", "请先完成以下设置：\n" + "\n".join(f"- {item}" for item in missing))
            return

        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", str(game["name"])).strip(" .") or "游戏"
        desktop = Path.home() / "Desktop"
        if not desktop.is_dir():
            self._show_error("创建失败", f"未找到当前用户桌面目录：\n{desktop}")
            return
        shortcut_path = desktop / f"{safe_name} 云存档启动器.lnk"

        try:
            self._create_windows_shortcut(
                shortcut_path,
                str(game["id"]),
                Path(emulator_path),
            )
        except Exception as exc:
            self._show_error("创建失败", str(exc))
            return
        self.append_log(f"已创建游戏启动快捷方式：{shortcut_path}")
        self._show_info("创建完成", f"已创建“{game['name']}”的启动快捷方式：\n{shortcut_path}")

    def _create_windows_shortcut(
        self,
        shortcut_path: Path,
        game_id: str,
        icon_path: Path,
    ) -> None:
        def ps_quote(value: str) -> str:
            return value.replace("'", "''")

        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(sys, "frozen", False):
            target_path = Path(sys.executable)
            arguments = f'--launch-game-id "{game_id}"'
            working_directory = target_path.parent
        else:
            python_executable = Path(sys.executable)
            pythonw_executable = python_executable.with_name("pythonw.exe")
            target_path = pythonw_executable if pythonw_executable.is_file() else python_executable
            main_script = self.app_dir / "main.py"
            arguments = f'"{main_script}" --launch-game-id "{game_id}"'
            working_directory = self.app_dir
        script = (
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$shortcut = $shell.CreateShortcut('{ps_quote(str(shortcut_path))}'); "
            f"$shortcut.TargetPath = '{ps_quote(str(target_path))}'; "
            f"$shortcut.Arguments = '{ps_quote(arguments)}'; "
            f"$shortcut.WorkingDirectory = '{ps_quote(str(working_directory))}'; "
            f"$shortcut.IconLocation = '{ps_quote(str(icon_path))},0'; "
            "$shortcut.Save()"
        )
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True,
            text=True,
            creationflags=creation_flags,
            timeout=30,
        )
        if completed.returncode != 0 or not shortcut_path.is_file():
            detail = completed.stderr.strip() or completed.stdout.strip() or "Windows 未生成快捷方式文件。"
            raise RuntimeError(f"创建快捷方式失败：{detail}")

    def pick_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择存档文件夹", self._save_path() or self._game_root() or str(self.script_dir))
        if not selected:
            return

        self.game_root_edit.setText(selected)
        self.save_path_label.setText(selected)
        self._refresh_open_save_folder_button_state()
        self._save_config()
        self.refresh_local_info()
        self._show_info("已选择", f"已选择存档文件夹：\n{selected}")

    def pick_emulator(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择模拟器或游戏程序",
            self._emulator_path() or str(self.script_dir),
            "程序文件 (*.exe);;所有文件 (*)",
        )
        if not selected:
            return
        self.emulator_path_edit.setText(selected)
        self._save_config()

    def start_target_window_capture(self) -> None:
        if self.window_capture_seconds > 0:
            return
        self.window_capture_seconds = 5
        self.capture_window_button.setEnabled(False)
        self._advance_target_window_capture()

    def _advance_target_window_capture(self) -> None:
        if self.window_capture_seconds > 0:
            self.capture_window_button.setText(f"请切换窗口（{self.window_capture_seconds}）")
            self.status_label.setText(f"请在 {self.window_capture_seconds} 秒内使用 Alt+Tab 切换到目标窗口。")
            self.window_capture_seconds -= 1
            QTimer.singleShot(1000, self._advance_target_window_capture)
            return

        self.capture_window_button.setText("记录目标窗口")
        self.capture_window_button.setEnabled(True)
        try:
            target_window = self._foreground_window_features()
            self._current_game()["target_window"] = target_window
            self._refresh_target_window_label()
            self._save_config()
            self.status_label.setText("目标窗口已记录。")
            self._show_info(
                "记录完成",
                "已记录目标窗口：\n"
                f"进程：{target_window['process_name']}\n"
                f"类名：{target_window['class_name']}\n"
                f"标题：{target_window['title_keyword'] or '无'}",
            )
        except Exception as exc:
            self.status_label.setText("目标窗口记录失败。")
            self._show_error("记录失败", str(exc))

    def _foreground_window_features(self) -> dict:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise RuntimeError("没有获取到当前前台窗口，请重新记录。")

        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))

        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))

        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process_handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not process_handle:
            raise RuntimeError("无法读取目标窗口所属进程，请尝试以管理员身份运行主程序后重新记录。")
        try:
            path_buffer = ctypes.create_unicode_buffer(32768)
            path_size = ctypes.c_ulong(len(path_buffer))
            if not kernel32.QueryFullProcessImageNameW(process_handle, 0, path_buffer, ctypes.byref(path_size)):
                raise RuntimeError("无法读取目标窗口所属进程名称。")
            process_name = Path(path_buffer.value).name
        finally:
            kernel32.CloseHandle(process_handle)

        class_name = class_buffer.value.strip()
        if not process_name or not class_name:
            raise RuntimeError("目标窗口特征不完整，请重新记录。")
        return {
            "process_name": process_name,
            "class_name": class_name,
            "title_keyword": title_buffer.value.strip(),
        }

    def _refresh_target_window_label(self) -> None:
        target = self._normalize_target_window(self._current_game().get("target_window"))
        if not target:
            self.target_window_label.setText("未记录")
            return
        title = target["title_keyword"] or "无"
        self.target_window_label.setText(
            f"进程：{target['process_name']}　类名：{target['class_name']}　标题：{title}"
        )

    def _refresh_open_save_folder_button_state(self) -> None:
        path_text = self._save_path()
        self.open_save_folder_button.setEnabled(bool(path_text and Path(path_text).exists()))

    def open_save_folder(self) -> None:
        path_text = self._save_path()
        if not path_text:
            return
        path = Path(path_text)
        if not path.exists():
            self._show_warning("路径不存在", f"存档文件夹不存在：\n{path}")
            self._refresh_open_save_folder_button_state()
            return
        os.startfile(str(path))

    def clear_log(self) -> None:
        self.log_text.clear()

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] {message}")

    def _set_text(self, widget: QPlainTextEdit, text: str) -> None:
        widget.setPlainText(text)

    def _save_config(self) -> bool:
        self._update_current_game_from_ui()
        self._sync_global_config_from_ui()
        data = self.config_data
        try:
            save_config(self.config_path, data)
            saved = load_config(self.config_path)
            if not saved or str(saved.get("current_game_id", "")) != self.current_game_id:
                raise OSError("保存后校验失败")
            saved_game = next(
                (game for game in saved.get("games", []) if str(game.get("id", "")) == self.current_game_id),
                None,
            )
            current_game = self._current_game()
            if not isinstance(saved_game, dict):
                raise OSError("保存后找不到当前游戏")
            for field in ("emulator_path", "save_path", "remote_zip_path", "target_window"):
                if saved_game.get(field) != current_game.get(field):
                    raise OSError(f"保存后字段校验失败：{field}")
            return True
        except OSError as exc:
            self.append_log(f"保存配置失败：{exc}")
            return False

    def _load_saved_config(self) -> dict:
        saved = load_config(self.config_path)
        if saved:
            return saved

        legacy_path = self.app_dir / CONFIG_FILE_NAME
        if legacy_path != self.config_path:
            legacy = load_config(legacy_path)
            if legacy:
                try:
                    save_config(self.config_path, legacy)
                except OSError:
                    pass
                return legacy
        return {}

    def set_progress(self, value: float, status: str | None = None) -> None:
        clamped_value = int(max(0.0, min(100.0, value)))
        if self.progress_dialog_bar is not None:
            self.progress_dialog_bar.setValue(clamped_value)
        if self.progress_dialog_percent_label is not None:
            self.progress_dialog_percent_label.setText(f"{clamped_value}%")
        if status:
            message, speed, file_size = parse_transfer_status(status)
            if self.progress_dialog_status_label is not None:
                self.progress_dialog_status_label.setText(message)
            if speed and self.progress_dialog_speed_label is not None:
                self.progress_dialog_speed_label.setText(f"网络速度：{speed}")
            if file_size and self.progress_dialog_size_label is not None:
                self.progress_dialog_size_label.setText(f"ZIP 文件大小：{file_size}")
            self.status_label.setText(message)

    def _show_progress_dialog(self, title: str, status: str) -> None:
        self._hide_progress_dialog()

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        status_label = QLabel(status)
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        percent_label = QLabel("0%")
        percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(progress_bar, 1)
        progress_row.addWidget(percent_label)
        layout.addLayout(progress_row)

        transfer_row = QHBoxLayout()
        speed_label = QLabel("网络速度：--")
        size_label = QLabel("ZIP 文件大小：--")
        size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        transfer_row.addWidget(speed_label)
        transfer_row.addWidget(size_label)
        layout.addLayout(transfer_row)

        self.progress_dialog = dialog
        self.progress_dialog_status_label = status_label
        self.progress_dialog_bar = progress_bar
        self.progress_dialog_percent_label = percent_label
        self.progress_dialog_speed_label = speed_label
        self.progress_dialog_size_label = size_label
        dialog.show()

    def _hide_progress_dialog(self) -> None:
        if self.progress_dialog is not None:
            self.progress_dialog.close()
        self.progress_dialog = None
        self.progress_dialog_status_label = None
        self.progress_dialog_bar = None
        self.progress_dialog_percent_label = None
        self.progress_dialog_speed_label = None
        self.progress_dialog_size_label = None

    def set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self._refresh_action_states()

    def refresh_all_info(self) -> None:
        self._save_config()
        self.refresh_local_info()
        self.refresh_remote_info()

    def _refresh_on_startup(self) -> None:
        self._save_config()
        self.refresh_local_info()
        if not self._validate_remote_inputs(show_message=False):
            return
        self.startup_remote_refresh = True
        self.refresh_remote_info()

    def refresh_local_info(self) -> None:
        try:
            save_dir = Path(self._save_path())
            if not save_dir.exists() or not save_dir.is_dir():
                self.local_info_cache = None
                self._set_text(self.local_text, "未读取到本地存档目录。\n请先到设置页选择正确的本地存档目录。")
                self._refresh_suggestion()
                return

            info = self._build_local_info(save_dir)
            self.local_info_cache = info
            self._set_text(self.local_text, self._format_info_text(info, side="local"))
            self.append_log("本地存档信息已刷新。")
            self._refresh_suggestion()
        except Exception as exc:
            if "404" in str(exc):
                self._emit_log("云端还没有这个游戏的存档，已按未上传处理。")
                self._emit_remote_info(self._build_empty_remote_info())
                self._emit_remote_done()
                return
            self.local_info_cache = None
            self._set_text(self.local_text, f"读取本地信息失败：{exc}")
            self.append_log(f"读取本地信息失败：{exc}")
            self._refresh_suggestion()

    def refresh_remote_info(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_warning("正在运行", "当前已有任务正在执行，请等待完成。")
            return
        if not self._validate_remote_inputs(show_message=True):
            return

        self._save_config()
        self.set_busy(True)
        self.set_progress(0, "正在读取 GitHub 云端存档信息...")
        self.append_log("开始读取云端存档信息。")
        self.worker_thread = threading.Thread(target=self._refresh_remote_worker, daemon=True)
        self.worker_thread.start()

    def start_upload(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_warning("正在运行", "当前已有任务正在执行，请等待完成。")
            return
        if not self._validate_all_inputs(show_message=True):
            return

        self._save_config()
        self.refresh_local_info()
        self._show_compare_popup("upload")

        self._show_progress_dialog("上传进度", "准备上传本地存档到 GitHub...")
        self.set_busy(True)
        self.set_progress(0, "准备上传本地存档到 GitHub...")
        self.append_log("开始上传流程。")
        self.worker_thread = threading.Thread(target=self._upload_worker, daemon=True)
        self.worker_thread.start()

    def start_download(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_warning("正在运行", "当前已有任务正在执行，请等待完成。")
            return
        if not self._validate_all_inputs(show_message=True):
            return

        self._save_config()
        self._show_compare_popup("download")
        mode_text = "备份本地存档后覆盖"
        self._show_progress_dialog("下载进度", f"准备从 GitHub 下载存档，当前模式：{mode_text}...")
        self.set_busy(True)
        self.set_progress(0, f"准备从 GitHub 下载存档，当前模式：{mode_text}...")
        self.append_log(f"开始下载流程，模式：{mode_text}。")
        self.worker_thread = threading.Thread(target=self._download_worker, daemon=True)
        self.worker_thread.start()

    def _validate_remote_inputs(self, show_message: bool) -> bool:
        token = self._token()
        repo = self._repo()
        remote_path = self._remote_zip_path()

        if not token:
            if show_message:
                self._show_error("缺少 Token", "请先到设置页填写 GitHub Token。")
            return False
        if not repo or "/" not in repo:
            if show_message:
                self._show_error("仓库名错误", "仓库名请填写成“用户名/仓库名”的格式。")
            return False
        if not remote_path:
            if show_message:
                self._show_error("云端存档目录名为空", "请填写云端存档目录名称。")
            return False
        return True

    def _validate_all_inputs(self, show_message: bool) -> bool:
        if not self._validate_remote_inputs(show_message):
            return False
        save_dir = Path(self._save_path())
        if not save_dir.exists() or not save_dir.is_dir():
            if show_message:
                self._show_error("本地目录不存在", "本地存档目录不存在，请检查路径。")
            return False
        return True

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "log":
                    self.append_log(str(payload))
                elif event_type == "progress":
                    percent, status = payload
                    self.set_progress(percent, status)
                elif event_type == "remote_info":
                    self.remote_info_cache = payload
                    self._set_text(self.remote_text, self._format_info_text(payload, side="remote"))
                    self._refresh_suggestion()
                    self._show_compare_popup("auto")
                elif event_type == "remote_done":
                    self.set_busy(False)
                    self.set_progress(100, "云端存档信息读取完成。")
                    self.worker_thread = None
                    self.startup_remote_refresh = False
                elif event_type == "remote_error":
                    self.remote_info_cache = None
                    self._set_text(self.remote_text, str(payload))
                    self._refresh_suggestion()
                    self.set_busy(False)
                    self.worker_thread = None
                    if self.startup_remote_refresh:
                        self._show_warning(
                            "自动刷新失败",
                            f"{payload}\n\n请检查网络连接和 GitHub 配置后重试。",
                        )
                    self.startup_remote_refresh = False
                elif event_type == "done":
                    self.set_busy(False)
                    self.set_progress(100, str(payload))
                    self.worker_thread = None
                    self.refresh_local_info()
                    self.refresh_remote_info_after_task()
                    QTimer.singleShot(1000, self._hide_progress_dialog)
                    self._show_info("完成", str(payload))
                elif event_type == "pending_restore_state":
                    self._set_pending_restore_state(payload)
                elif event_type == "error":
                    self.set_busy(False)
                    self.status_label.setText("操作失败。")
                    self.worker_thread = None
                    self._show_error("出错了", str(payload))
        except queue.Empty:
            pass

    def refresh_remote_info_after_task(self) -> None:
        if self._validate_remote_inputs(show_message=False):
            self.worker_thread = None
            self.refresh_remote_info()
        else:
            self.worker_thread = None

    def _emit_log(self, message: str) -> None:
        self.event_queue.put(("log", message))

    def _emit_progress(self, percent: float, status: str) -> None:
        self.event_queue.put(("progress", (percent, status)))

    def _emit_remote_info(self, info: dict) -> None:
        self.event_queue.put(("remote_info", info))

    def _emit_remote_done(self) -> None:
        self.event_queue.put(("remote_done", None))

    def _emit_remote_error(self, message: str) -> None:
        self.event_queue.put(("remote_error", message))

    def _emit_done(self, message: str) -> None:
        self.event_queue.put(("done", message))

    def _emit_error(self, message: str) -> None:
        self.event_queue.put(("error", message))

    def _emit_pending_restore_state(self, state: dict | None) -> None:
        self.event_queue.put(("pending_restore_state", state))

    def _refresh_remote_worker(self) -> None:
        try:
            self._emit_progress(20, "正在读取 GitHub 云端元数据...")
            info = get_remote_info(self.config_data, self.current_game_id)
            self._emit_log("云端存档信息已刷新。")
            self._emit_remote_info(info)
            self._emit_remote_done()
        except Exception as exc:
            self._emit_log(f"读取云端信息失败：{exc}")
            self._emit_remote_error(f"读取云端信息失败：{exc}")

    def _upload_worker(self) -> None:
        try:
            result = upload_game(
                self.config_data,
                self.current_game_id,
                self.config_path,
                self._emit_progress,
                self._emit_log,
            )
            self._emit_done(str(result["message"]))
        except Exception as exc:
            self._emit_log(f"上传失败：{exc}")
            self._emit_error(str(exc))

    def _download_worker(self) -> None:
        try:
            result = download_game(
                self.config_data,
                self.current_game_id,
                self.config_path,
                self.data_dir,
                self._emit_progress,
                self._emit_log,
            )
            self._emit_pending_restore_state(result["pending_restore"])
            self._emit_done(str(result["message"]))
        except Exception as exc:
            self._emit_log(f"下载失败：{exc}")
            self._emit_error(str(exc))

    def _build_local_info(self, save_dir: Path) -> dict:
        game = self._current_game()
        return {
            "uploaded_at": str(game.get("last_uploaded_at", "")),
        }

    def _build_empty_remote_info(self) -> dict:
        return {
            "uploaded_at": "",
            "not_uploaded": True,
        }

    def _scan_slot_times(self, save_dir: Path) -> dict[str, str]:
        slots: dict[str, str] = {}
        for child in sorted(save_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not child.name.isdigit():
                continue
            latest = 0.0
            for root, _, names in os.walk(child):
                for name in names:
                    file_path = Path(root) / name
                    try:
                        latest = max(latest, file_path.stat().st_mtime)
                    except OSError:
                        pass
            if latest > 0:
                slots[child.name] = self._format_timestamp(latest)
        return slots

    def _latest_slot_from_slots(self, slots: dict[str, str]) -> tuple[str, str]:
        if not slots:
            return "无", "无"
        latest_slot = max(slots.items(), key=lambda item: item[1])[0]
        return latest_slot, slots[latest_slot]

    def _path_mtime_text(self, path: Path) -> str:
        if not path.exists():
            return "未找到"
        try:
            return self._format_timestamp(path.stat().st_mtime)
        except OSError:
            return "读取失败"

    def _format_info_text(self, info: dict, side: str) -> str:
        if side == "remote" and info.get("not_uploaded"):
            return "尚未上传存档"

        uploaded_at = str(info.get("uploaded_at", "")).strip()
        if side == "local":
            lines = [f"最近一次上传到云端的时间：{uploaded_at}" if uploaded_at else "没有本地上传记录"]
            pending = self.pending_restore_state
            if pending:
                lines.extend(
                    [
                        "",
                        f"最近一次下载前备份时间：{pending.get('created_at', '未知')}",
                        "可点击下方按钮随时回退。",
                    ]
                )
            else:
                lines.extend(["", "当前没有可回退的下载前备份。"])
            return "\n".join(lines)

        if not uploaded_at:
            return "尚未上传存档"

        device_name = str(info.get("device_name", "")).strip() or "未知"
        return "\n".join(
            [
                f"最近一次上传到云端的时间：{uploaded_at}",
                f"最近一次上传设备：{device_name}",
            ]
        )

    def _refresh_suggestion(self) -> None:
        return

    def _show_compare_popup(self, context: str) -> None:
        _ = context
        return

    def _format_size(self, size: int) -> str:
        return format_size(size)

    def _now_text(self) -> str:
        return now_text()

    def _format_timestamp(self, timestamp: float) -> str:
        return format_timestamp(timestamp)

    def _parse_time_text(self, text: str) -> float | None:
        return parse_time_text(text)

    def _normalize_pending_restore_state(self, value: object) -> dict | None:
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
            "created_at": created_at or self._now_text(),
        }

    def _normalize_target_window(self, value: object) -> dict | None:
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

    def _set_pending_restore_state(self, state: dict | None) -> None:
        self.pending_restore_state = self._normalize_pending_restore_state(state)
        self._update_pending_restore_ui()
        self._save_config()

    def _update_pending_restore_ui(self) -> None:
        pending = self.pending_restore_state
        self.rollback_backup_button.setVisible(bool(pending))
        self.rollback_backup_button.setEnabled(bool(pending) and not self.is_busy)
        self.rollback_backup_button.setToolTip(
            f"备份时间：{pending.get('created_at', '未知')}" if pending else "当前没有可回退的本地备份"
        )
        self._refresh_action_states()

    def _refresh_action_states(self) -> None:
        upload_state = not self.is_busy
        download_state = not self.is_busy
        pending_state = bool(self.pending_restore_state) and (not self.is_busy)

        self.upload_button.setEnabled(upload_state)
        self.download_button.setEnabled(download_state)
        self.rollback_backup_button.setEnabled(pending_state)

    def rollback_backup_result(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_warning("正在运行", "当前已有任务正在执行，请等待完成。")
            return
        if not self.pending_restore_state:
            return
        self.set_busy(True)
        self.set_progress(0, "正在回退到下载前的本地副本存档...")
        self.append_log("开始回退到下载前的本地副本存档。")
        self.worker_thread = threading.Thread(target=self._rollback_backup_worker, daemon=True)
        self.worker_thread.start()

    def _rollback_backup_worker(self) -> None:
        try:
            result = rollback_game(
                self.config_data,
                self.current_game_id,
                self.config_path,
                self._emit_progress,
            )
            self._emit_pending_restore_state(None)
            self._emit_done(str(result["message"]))
        except Exception as exc:
            self._emit_log(f"回退存档失败：{exc}")
            self._emit_error(str(exc))

    def _show_info(self, title: str, message: str) -> None:
        box = QMessageBox(QMessageBox.Information, title, message, QMessageBox.NoButton, self)
        QTimer.singleShot(1000, box.accept)
        box.exec_()

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)


def main() -> None:
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PythonAI1.GameCloudSave.Main")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    resource_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon_path = resource_dir / "assets" / "game_cloud_save.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = GamesCloudSaveApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
