import base64
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
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
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import load_config, save_config
from constants import CONFIG_FILE_NAME, DEFAULT_GAME_ID, DEFAULT_REMOTE_ZIP_PATH, PENDING_BACKUP_DIR_NAME
from github_client import (
    download_file,
    get_download_url,
    get_existing_file_sha,
    get_remote_metadata,
    metadata_path_for_zip,
    put_file_to_github,
)
from save_manager import (
    build_new_save_folder,
    collect_files,
    copy_directory_snapshot,
    copy_tree_with_progress,
    extract_zip_with_timestamps,
    find_best_save_path_in_root,
    latest_slot_from_slots,
    path_mtime_text,
    read_and_encode_file,
    replace_directory_contents,
    scan_root_directory_for_save,
    scan_slot_times,
    sha256_of_file,
    validate_zip_members,
    zip_save_directory,
)
from utils import default_device_name, format_size, format_timestamp, now_text, parse_time_text, sanitize_device_name


class GamesCloudSaveApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("游戏云存档")
        self.resize(1550, 1200)
        self.setMinimumSize(1120, 820)

        self.app_dir = self._resolve_app_dir()
        self.config_path = self.app_dir / CONFIG_FILE_NAME
        self.script_dir = self.app_dir
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.compare_popup_shown = False
        self.is_busy = False
        self.progress_dialog: QDialog | None = None
        self.progress_dialog_status_label: QLabel | None = None
        self.progress_dialog_bar: QProgressBar | None = None
        self.progress_dialog_percent_label: QLabel | None = None

        saved = self._normalize_config(self._load_saved_config())
        self.config_data = saved
        self.games: list[dict] = saved["games"]
        self.current_game_id = str(saved.get("current_game_id") or self.games[0]["id"])
        current_game = self._current_game()
        saved_game_root = str(current_game.get("game_root_path", ""))
        detected_path = str(current_game.get("save_path", ""))
        saved_device_name = self._sanitize_device_name(str(saved.get("device_name", "")))

        self.local_info_cache: dict | None = None
        self.remote_info_cache: dict | None = None
        self.pending_restore_state = self._normalize_pending_restore_state(current_game.get("pending_restore"))
        self.download_backup_requested = False
        self.prompt_for_device_name = not bool(saved_device_name)

        self._build_ui(
            token=str(saved.get("token", "")),
            repo=str(saved.get("repo", "")),
            branch=str(saved.get("branch", "main")),
            game_name=str(current_game.get("name", "未命名游戏")),
            games=self.games,
            current_game_id=self.current_game_id,
            game_root=saved_game_root or str(self.script_dir),
            save_path=str(current_game.get("save_path") or detected_path),
            device_name=saved_device_name or self._default_device_name(),
            remote_zip_path=str(current_game.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH)),
            download_mode=str(current_game.get("download_mode", "overwrite")),
            backup_before_overwrite=bool(current_game.get("backup_before_overwrite", True)),
        )
        self._apply_styles()
        self._update_pending_restore_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start(100)

        QTimer.singleShot(200, self.refresh_local_info)
        QTimer.singleShot(300, self._prompt_for_device_name_if_needed)

    def _build_ui(
        self,
        token: str,
        repo: str,
        branch: str,
        game_name: str,
        games: list[dict],
        current_game_id: str,
        game_root: str,
        save_path: str,
        device_name: str,
        remote_zip_path: str,
        download_mode: str,
        backup_before_overwrite: bool,
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
        self.logs_tab = QWidget()
        self.notebook.addTab(self.overview_tab, "概览")
        self.notebook.addTab(self.settings_tab, "设置")
        self.notebook.addTab(self.logs_tab, "日志")

        self._build_overview_tab(download_mode, backup_before_overwrite)
        self._build_settings_tab(token, repo, branch, game_root, save_path, device_name, remote_zip_path)
        self._build_logs_tab()
        self._populate_game_selector(games, current_game_id)

    def _build_overview_tab(self, download_mode: str, backup_before_overwrite: bool) -> None:
        layout = QVBoxLayout(self.overview_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(14)

        suggestion_group = self._section_group("当前建议")
        suggestion_layout = QVBoxLayout(suggestion_group)
        self.suggestion_label = QLabel("建议：先刷新本地和云端信息。")
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setObjectName("SuggestionLabel")
        suggestion_layout.addWidget(self.suggestion_label)
        layout.addWidget(suggestion_group)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.refresh_all_button = QPushButton("刷新本地和云端信息")
        self.refresh_all_button.clicked.connect(self.refresh_all_info)
        self.upload_button = QPushButton("更新云存档")
        self.upload_button.clicked.connect(self.start_upload)
        self.download_button = QPushButton("从 GitHub 下载")
        self.download_button.clicked.connect(self.start_download)
        action_row.addWidget(self.refresh_all_button)
        action_row.addWidget(self.upload_button)
        action_row.addWidget(self.download_button)
        layout.addLayout(action_row)

        mode_group = self._section_group("下载方式")
        mode_layout = QVBoxLayout(mode_group)
        mode_row = QHBoxLayout()
        self.download_mode_group = QButtonGroup(self)
        self.overwrite_radio = QRadioButton("覆盖本地存档")
        self.new_folder_radio = QRadioButton("创建新存档文件夹")
        self.download_mode_group.addButton(self.overwrite_radio)
        self.download_mode_group.addButton(self.new_folder_radio)
        self.overwrite_radio.setChecked(download_mode == "overwrite")
        self.new_folder_radio.setChecked(download_mode == "new_folder")
        self.overwrite_radio.toggled.connect(self._on_download_mode_changed)
        self.new_folder_radio.toggled.connect(self._on_download_mode_changed)
        mode_row.addWidget(self.overwrite_radio)
        mode_row.addWidget(self.new_folder_radio)
        mode_row.addStretch(1)
        self.backup_before_overwrite_check = QCheckBox("覆盖前先备份本地存档（默认开启）")
        self.backup_before_overwrite_check.setChecked(backup_before_overwrite)
        self.backup_before_overwrite_check.toggled.connect(self._on_backup_option_changed)
        mode_layout.addLayout(mode_row)
        mode_layout.addWidget(self.backup_before_overwrite_check)
        layout.addWidget(mode_group)

        self.pending_action_group = self._section_group("覆盖后确认")
        pending_layout = QVBoxLayout(self.pending_action_group)
        self.pending_notice_label = QLabel("")
        self.pending_notice_label.setWordWrap(True)
        pending_layout.addWidget(self.pending_notice_label)
        pending_button_row = QHBoxLayout()
        self.confirm_backup_button = QPushButton("确认覆盖正确")
        self.confirm_backup_button.clicked.connect(self.confirm_backup_result)
        self.rollback_backup_button = QPushButton("回退存档")
        self.rollback_backup_button.clicked.connect(self.rollback_backup_result)
        pending_button_row.addWidget(self.confirm_backup_button)
        pending_button_row.addWidget(self.rollback_backup_button)
        pending_layout.addLayout(pending_button_row)
        layout.addWidget(self.pending_action_group)

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
        else:
            self.remote_text = text
        inner.addWidget(text)
        return box

    def _build_settings_tab(
        self,
        token: str,
        repo: str,
        branch: str,
        game_root: str,
        save_path: str,
        device_name: str,
        remote_zip_path: str,
    ) -> None:
        outer = QVBoxLayout(self.settings_tab)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(14)

        card = self._card()
        outer.addWidget(card)
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        intro = QLabel("本地及云端设置")
        intro.setWordWrap(True)
        intro.setObjectName("SecondaryLabel")
        grid.addWidget(intro, 0, 0, 1, 3)

        self.token_edit = QLineEdit(token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.repo_edit = QLineEdit(repo)
        self.branch_edit = QLineEdit(branch)
        self.game_root_edit = QLineEdit(game_root)
        self.device_name_edit = QLineEdit(device_name)
        self.remote_zip_path_edit = QLineEdit(remote_zip_path)
        self.save_path_label = QLabel(save_path)
        self.save_path_label.setWordWrap(True)
        self.open_save_folder_button = QPushButton("打开存档文件夹")
        self.open_save_folder_button.clicked.connect(self.open_save_folder)
        self.config_path_label = QLabel(f"配置会自动保存在：{self.config_path}")
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setObjectName("SecondaryLabel")

        self.device_name_edit.textChanged.connect(self._on_device_name_changed)

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
        self._add_labeled_entry(grid, 5, "设备信息", self.device_name_edit, hint="只允许中英文和数字")
        self._add_labeled_entry(grid, 6, "云端 zip 路径", self.remote_zip_path_edit, hint="例如 games-botw-save/save_backup_latest.zip")

        grid.addWidget(QLabel("当前检测到的存档目录"), 7, 0, alignment=Qt.AlignTop)
        grid.addWidget(self.save_path_label, 7, 1, 1, 2)
        grid.addWidget(self.config_path_label, 8, 0, 1, 3)
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
        widget: QLineEdit,
        hint: str | None = None,
        browse_callback=None,
        extra_button: QPushButton | None = None,
    ) -> None:
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(widget, row, 1)

        right = QHBoxLayout()
        right.setSpacing(6)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("SecondaryLabel")
            right.addWidget(hint_label)
        if browse_callback:
            button = QPushButton("浏览")
            button.clicked.connect(browse_callback)
            right.addWidget(button)
        if extra_button:
            right.addWidget(extra_button)
        right.addStretch(1)

        holder = QWidget()
        holder.setLayout(right)
        layout.addWidget(holder, row, 2)

    def _build_logs_tab(self) -> None:
        layout = QVBoxLayout(self.logs_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(14)

        top_card = self._card()
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(18, 14, 18, 14)
        text = QLabel("可查看详细运行日志")
        text.setWordWrap(True)
        text.setObjectName("SecondaryLabel")
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.clear_log)
        top_layout.addWidget(text, 1)
        top_layout.addWidget(self.clear_log_button)
        layout.addWidget(top_card)

        log_card = self._card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 12, 12, 12)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("LogText")
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_card, 1)

    def _apply_styles(self) -> None:
        font = QFont("Microsoft YaHei UI", 10)
        QApplication.instance().setFont(font)
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f7fb;
                color: #1f2a37;
            }
            QWidget#Card, QGroupBox {
                background: #ffffff;
                border: 1px solid #d7e1ee;
                border-radius: 16px;
            }
            QGroupBox {
                margin-top: 14px;
                padding-top: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 6px;
                color: #294a6b;
            }
            QLabel#TitleLabel {
                font-size: 24px;
                font-weight: 700;
                color: #17324d;
            }
            QLabel#SecondaryLabel {
                color: #60758b;
            }
            QLabel#SuggestionLabel {
                color: #0e5f3a;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton {
                min-height: 42px;
                border-radius: 12px;
                border: 1px solid #b9cae0;
                background: #ffffff;
                color: #21415f;
                padding: 0 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f1f7ff;
            }
            QPushButton:pressed {
                background: #e5f0ff;
            }
            QPushButton:disabled {
                color: #96a4b6;
                background: #eef2f7;
                border-color: #d7dfe8;
            }
            QPushButton[text="更新云存档"], QPushButton[text="从 GitHub 下载"] {
                background: #1f8fff;
                color: #ffffff;
                border-color: #1f8fff;
            }
            QPushButton[text="更新云存档"]:hover, QPushButton[text="从 GitHub 下载"]:hover {
                background: #117de6;
            }
            QPushButton[text="确认覆盖正确"] {
                background: #14b86a;
                color: #ffffff;
                border-color: #14b86a;
            }
            QPushButton[text="回退存档"] {
                background: #ff8b3d;
                color: #ffffff;
                border-color: #ff8b3d;
            }
            QLineEdit, QPlainTextEdit {
                background: #fbfdff;
                border: 1px solid #ccdae9;
                border-radius: 12px;
                padding: 10px 12px;
                selection-background-color: #9bc7ff;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border: 1px solid #4d9fff;
            }
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                background: #e8eff8;
                color: #48617d;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 10px 18px;
                margin-right: 6px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #17324d;
                font-weight: 700;
            }
            QProgressBar {
                background: #e8eef6;
                border: none;
                border-radius: 8px;
                min-height: 16px;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #14b86a, stop:1 #1f8fff);
            }
            QRadioButton, QCheckBox {
                spacing: 10px;
            }
            QSplitter::handle {
                background: transparent;
                height: 10px;
            }
            """
        )

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

    def _game_root(self) -> str:
        return self.game_root_edit.text().strip()

    def _save_path(self) -> str:
        return self.save_path_label.text().strip()

    def _device_name_text(self) -> str:
        return self.device_name_edit.text()

    def _remote_zip_path(self) -> str:
        return self.remote_zip_path_edit.text().strip() or DEFAULT_REMOTE_ZIP_PATH

    def _download_mode(self) -> str:
        return "overwrite" if self.overwrite_radio.isChecked() else "new_folder"

    def _normalize_config(self, saved: dict) -> dict:
        token = str(saved.get("token", ""))
        repo = str(saved.get("repo", ""))
        branch = str(saved.get("branch", "main") or "main")
        device_name = self._sanitize_device_name(str(saved.get("device_name", "")))

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
                remote_zip_path = str(item.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH))
                download_mode = str(item.get("download_mode", "overwrite"))
                backup_before_overwrite = bool(item.get("backup_before_overwrite", True))
                pending_restore = self._normalize_pending_restore_state(item.get("pending_restore"))
                detect_type = str(item.get("detect_type", "manual"))
                last_uploaded_at = str(item.get("last_uploaded_at", ""))
                games.append(
                    {
                        "id": game_id,
                        "name": game_name,
                        "game_root_path": game_root,
                        "save_path": save_path,
                        "remote_zip_path": remote_zip_path,
                        "download_mode": download_mode if download_mode in {"overwrite", "new_folder"} else "overwrite",
                        "backup_before_overwrite": backup_before_overwrite,
                        "pending_restore": pending_restore,
                        "detect_type": detect_type,
                        "last_uploaded_at": last_uploaded_at,
                    }
                )

        if not games:
            legacy_game_root = str(saved.get("game_root_path", ""))
            legacy_save_path = str(saved.get("save_path", ""))
            legacy_remote = str(saved.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH))
            legacy_mode = str(saved.get("download_mode", "overwrite"))
            legacy_backup = bool(saved.get("backup_before_overwrite", True))
            legacy_pending = self._normalize_pending_restore_state(saved.get("pending_restore"))
            games = [
                {
                    "id": DEFAULT_GAME_ID,
                    "name": "你的游戏",
                    "game_root_path": legacy_game_root,
                    "save_path": legacy_save_path,
                    "remote_zip_path": legacy_remote,
                    "download_mode": legacy_mode if legacy_mode in {"overwrite", "new_folder"} else "overwrite",
                    "backup_before_overwrite": legacy_backup,
                    "pending_restore": legacy_pending,
                    "detect_type": "manual",
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
            "device_name": device_name,
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
        game["game_root_path"] = self._game_root()
        game["save_path"] = self._save_path()
        game["remote_zip_path"] = self._remote_zip_path()
        game["download_mode"] = self._download_mode()
        game["backup_before_overwrite"] = self.backup_before_overwrite_check.isChecked()
        game["pending_restore"] = self.pending_restore_state

    def _load_current_game_into_ui(self) -> None:
        game = self._current_game()
        self.game_root_edit.setText(str(game.get("game_root_path", "")))
        self.save_path_label.setText(str(game.get("save_path", "")))
        self._refresh_open_save_folder_button_state()
        self.remote_zip_path_edit.setText(str(game.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH)))
        self.overwrite_radio.setChecked(str(game.get("download_mode", "overwrite")) == "overwrite")
        self.new_folder_radio.setChecked(str(game.get("download_mode", "overwrite")) == "new_folder")
        self.backup_before_overwrite_check.setChecked(bool(game.get("backup_before_overwrite", True)))
        self.pending_restore_state = self._normalize_pending_restore_state(game.get("pending_restore"))
        self._update_pending_restore_ui()

    def _sync_global_config_from_ui(self) -> None:
        self.config_data["token"] = self._token()
        self.config_data["repo"] = self._repo()
        self.config_data["branch"] = self._branch()
        self.config_data["device_name"] = self._get_device_name()
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
        self.compare_popup_shown = False
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
                "remote_zip_path": f"save_sync/{new_id}/save_backup_latest.zip",
                "download_mode": "overwrite",
                "backup_before_overwrite": True,
                "pending_restore": None,
                "detect_type": "manual",
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
        self._ensure_device_name_present()
        self._save_config()
        self.append_log("设置已保存。")
        self._show_info("已保存", f"设置已保存到：\n{self.config_path}")

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

    def detect_directory(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_warning("正在运行", "当前已有任务正在执行，请等待完成。")
            return

        root_text = self._game_root() or str(self.script_dir)
        if not Path(root_text).exists():
            self._show_warning("目录不存在", "当前游戏目录不存在，请先手动选择正确的游戏目录。")
            return

        self.set_busy(True)
        self.set_progress(0, "正在自动检测游戏目录里的存档...")
        self.append_log(f"开始自动检测存档目录，扫描根目录：{root_text}")
        self.worker_thread = threading.Thread(target=self._detect_directory_worker, daemon=True)
        self.worker_thread.start()

    def clear_log(self) -> None:
        self.log_text.clear()

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] {message}")

    def _set_text(self, widget: QPlainTextEdit, text: str) -> None:
        widget.setPlainText(text)

    def _save_config(self) -> None:
        self._update_current_game_from_ui()
        self._sync_global_config_from_ui()
        data = self.config_data
        try:
            save_config(self.config_path, data)
        except OSError as exc:
            self.append_log(f"保存配置失败：{exc}")

    def _load_saved_config(self) -> dict:
        return load_config(self.config_path)

    def set_progress(self, value: float, status: str | None = None) -> None:
        clamped_value = int(max(0.0, min(100.0, value)))
        if self.progress_dialog_bar is not None:
            self.progress_dialog_bar.setValue(clamped_value)
        if self.progress_dialog_percent_label is not None:
            self.progress_dialog_percent_label.setText(f"{clamped_value}%")
        if status and self.progress_dialog_status_label is not None:
            self.progress_dialog_status_label.setText(status)
        if status:
            self.status_label.setText(status)

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

        self.progress_dialog = dialog
        self.progress_dialog_status_label = status_label
        self.progress_dialog_bar = progress_bar
        self.progress_dialog_percent_label = percent_label
        dialog.show()

    def _hide_progress_dialog(self) -> None:
        if self.progress_dialog is not None:
            self.progress_dialog.close()
        self.progress_dialog = None
        self.progress_dialog_status_label = None
        self.progress_dialog_bar = None
        self.progress_dialog_percent_label = None

    def set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self._refresh_action_states()

    def refresh_all_info(self) -> None:
        self.compare_popup_shown = False
        self._save_config()
        self.refresh_local_info()
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
        self.compare_popup_shown = False
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

        self._ensure_device_name_present()
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
        if self.pending_restore_state:
            self._show_warning("请先处理当前覆盖", "当前有未确认的覆盖备份，请先点击“确认覆盖正确”或“回退存档”。")
            return
        if not self._validate_all_inputs(show_message=True):
            return

        self._ensure_device_name_present()
        self._save_config()
        self._show_compare_popup("download")
        mode_text = "覆盖本地存档" if self._download_mode() == "overwrite" else "创建新存档文件夹"
        self.download_backup_requested = self._download_mode() == "overwrite" and self.backup_before_overwrite_check.isChecked()
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
                self._show_error("远程路径为空", "请填写 GitHub 上的 zip 文件路径。")
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
                elif event_type == "detect_result":
                    result = payload
                    detected = str(result["save_path"])
                    game_root = str(result["game_root"])
                    self.game_root_edit.setText(game_root)
                    self.save_path_label.setText(detected)
                    self._save_config()
                    self.refresh_local_info()
                    self.set_busy(False)
                    self.set_progress(100, "自动检测完成。")
                    self.worker_thread = None
                    self._show_info("检测成功", f"游戏目录：\n{game_root}\n\n检测到存档目录：\n{detected}")
                elif event_type == "detect_error":
                    self.set_busy(False)
                    self.set_progress(0, "自动检测未找到存档目录。")
                    self.worker_thread = None
                    self._show_warning("未检测到", str(payload))
                elif event_type == "remote_done":
                    self.set_busy(False)
                    self.set_progress(100, "云端存档信息读取完成。")
                    self.worker_thread = None
                elif event_type == "remote_error":
                    self.remote_info_cache = None
                    self._set_text(self.remote_text, str(payload))
                    self._refresh_suggestion()
                    self.set_busy(False)
                    self.worker_thread = None
                elif event_type == "done":
                    self.set_busy(False)
                    self.set_progress(100, str(payload))
                    self.worker_thread = None
                    self.refresh_local_info()
                    self.refresh_remote_info_after_task()
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

    def _emit_detect_result(self, game_root: str, detected: str) -> None:
        self.event_queue.put(("detect_result", {"game_root": game_root, "save_path": detected}))

    def _emit_detect_error(self, message: str) -> None:
        self.event_queue.put(("detect_error", message))

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
            repo = self._repo()
            branch = self._branch()
            zip_path = self._remote_zip_path()
            json_path = self._metadata_path_for_zip(zip_path)

            self._emit_progress(20, "正在读取 GitHub 云端元数据...")
            info = self._get_remote_metadata(repo, json_path, branch)
            self._emit_log("云端存档信息已刷新。")
            self._emit_remote_info(info)
            self._emit_remote_done()
        except Exception as exc:
            self._emit_log(f"读取云端信息失败：{exc}")
            self._emit_remote_error(f"读取云端信息失败：{exc}")

    def _detect_directory_worker(self) -> None:
        try:
            root_path = Path(self._game_root() or str(self.script_dir))
            detected = self._scan_root_directory_for_save(root_path)
            if detected:
                self._emit_log(f"自动检测成功：{detected}")
                self._emit_detect_result(str(root_path), detected)
            else:
                self._emit_log("自动检测结束：未找到存档目录。")
                self._emit_detect_error("没有在当前游戏目录里找到存档目录，请手动重新选择游戏目录。")
        except Exception as exc:
            self._emit_log(f"自动检测失败：{exc}")
            self._emit_detect_error(f"自动检测失败：{exc}")

    def _upload_worker(self) -> None:
        temp_zip: str | None = None
        try:
            repo = self._repo()
            branch = self._branch()
            save_dir = Path(self._save_path())
            zip_path = self._remote_zip_path()
            json_path = self._metadata_path_for_zip(zip_path)

            self._emit_progress(5, "正在扫描本地存档文件...")
            files, total_bytes = self._collect_files(save_dir)
            if not files:
                raise RuntimeError("本地存档目录里没有可上传的文件。")

            local_info = self._build_local_info(save_dir)
            self.local_info_cache = local_info
            self._emit_log(f"准备打包 {len(files)} 个文件，总大小约 {self._format_size(total_bytes)}。")

            self._emit_progress(10, "正在打包存档...")
            temp_zip = self._zip_save_directory(save_dir, files, total_bytes)
            zip_sha256 = self._sha256_of_file(temp_zip)
            zip_size = os.path.getsize(temp_zip)

            metadata = self._build_upload_metadata(local_info, zip_sha256, zip_size, save_dir)
            self._emit_log(f"压缩包已生成：{self._format_size(zip_size)}，SHA256={zip_sha256[:16]}...")

            self._emit_progress(52, "正在读取压缩包内容...")
            zip_encoded = self._read_and_encode_file(temp_zip, start_percent=52, end_percent=68, status="正在编码 zip 压缩包...")

            self._emit_progress(69, "正在准备上传元数据...")
            json_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
            json_encoded = base64.b64encode(json_bytes).decode("ascii")

            self._emit_progress(74, "正在检查 GitHub 上是否已有旧备份...")
            existing_zip_sha = self._get_existing_file_sha(repo, zip_path, branch)
            existing_json_sha = self._get_existing_file_sha(repo, json_path, branch)

            self._emit_progress(82, "正在上传 zip 存档包...")
            self._put_file_to_github(
                repo,
                zip_path,
                branch,
                zip_encoded,
                f"Update Games cloud save zip {time.strftime('%Y-%m-%d %H:%M:%S')}",
                existing_zip_sha,
            )

            self._emit_progress(92, "正在上传时间说明 json...")
            self._put_file_to_github(
                repo,
                json_path,
                branch,
                json_encoded,
                f"Update Games cloud save metadata {time.strftime('%Y-%m-%d %H:%M:%S')}",
                existing_json_sha,
            )

            self._emit_log(f"上传完成：{repo}/{zip_path}")
            self._emit_log(f"元数据已更新：{repo}/{json_path}")
            self._current_game()["last_uploaded_at"] = metadata["uploaded_at"]
            self._save_config()
            self._emit_done("上传完成，云端存档和元数据都已更新。")
        except Exception as exc:
            self._emit_log(f"上传失败：{exc}")
            self._emit_error(str(exc))
        finally:
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.remove(temp_zip)
                except OSError:
                    pass

    def _download_worker(self) -> None:
        temp_zip: str | None = None
        temp_extract_dir: str | None = None
        pending_state: dict | None = None
        try:
            repo = self._repo()
            branch = self._branch()
            save_dir = Path(self._save_path())
            zip_path = self._remote_zip_path()
            mode = self._download_mode()

            self._emit_progress(8, "正在读取云端下载地址...")
            download_url = self._get_download_url(repo, zip_path, branch)

            self._emit_progress(18, "正在下载云端 zip 压缩包...")
            temp_zip = self._download_file(download_url)
            self._emit_log(f"云端压缩包下载完成：{self._format_size(os.path.getsize(temp_zip))}")

            self._emit_progress(60, "正在校验压缩包结构...")
            self._validate_zip_members(temp_zip)

            self._emit_progress(72, "正在解压云端存档...")
            temp_extract_dir = tempfile.mkdtemp(prefix="games_save_extract_")
            with zipfile.ZipFile(temp_zip, "r") as archive:
                self._extract_zip_with_timestamps(archive, Path(temp_extract_dir))

            extracted_root = Path(temp_extract_dir) / save_dir.name
            if not extracted_root.exists():
                extracted_root = Path(temp_extract_dir)

            if mode == "overwrite":
                destination_dir = save_dir
                if self.download_backup_requested:
                    self._emit_progress(78, "正在备份当前本地存档...")
                    pending_state = self._create_pending_backup_state(save_dir)
                    self._emit_log(f"已在软件目录生成本地副本：{pending_state['backup_dir']}")
                self._emit_log(f"下载模式：覆盖本地存档 -> {destination_dir}")
            else:
                destination_dir = self._build_new_save_folder(save_dir)
                self._emit_log(f"下载模式：创建新存档文件夹 -> {destination_dir}")

            self._emit_progress(82, "正在写入本地存档文件...")
            copied = self._copy_tree_with_progress(extracted_root, destination_dir)
            if pending_state:
                self._emit_pending_restore_state(pending_state)
            self._emit_log(f"已写入 {copied} 个文件到本地。")

            if mode == "overwrite":
                if pending_state:
                    self._emit_done("下载完成，已先备份本地存档再覆盖。请在界面上选择“确认覆盖正确”或“回退存档”。")
                else:
                    self._emit_done("下载完成，云端存档已经覆盖到本地。")
            else:
                self._emit_done(f"下载完成，已创建新的本地存档文件夹：{destination_dir}")
        except Exception as exc:
            if pending_state:
                pending_root = Path(pending_state["backup_dir"]).parent
                if pending_root.exists():
                    shutil.rmtree(pending_root, ignore_errors=True)
            self._emit_log(f"下载失败：{exc}")
            self._emit_error(str(exc))
        finally:
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.remove(temp_zip)
                except OSError:
                    pass
            if temp_extract_dir and os.path.exists(temp_extract_dir):
                shutil.rmtree(temp_extract_dir, ignore_errors=True)

    def _collect_files(self, save_dir: Path) -> tuple[list[Path], int]:
        return collect_files(save_dir)

    def _zip_save_directory(self, save_dir: Path, files: list[Path], total_bytes: int) -> str:
        fd, temp_zip = tempfile.mkstemp(prefix="games_save_", suffix=".zip")
        os.close(fd)

        processed = 0
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, file_path in enumerate(files, start=1):
                relative = file_path.relative_to(save_dir.parent)
                archive.write(file_path, arcname=str(relative))
                processed += file_path.stat().st_size
                percent = 10 + (processed / max(total_bytes, 1)) * 40
                self._emit_progress(percent, f"正在打包存档... {index}/{len(files)}")
        return temp_zip

    def _read_and_encode_file(self, file_path: str, start_percent: float, end_percent: float, status: str) -> str:
        size = os.path.getsize(file_path)
        processed = 0
        buffer = bytearray()
        with open(file_path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 256)
                if not chunk:
                    break
                buffer.extend(chunk)
                processed += len(chunk)
                progress = start_percent + (processed / max(size, 1)) * (end_percent - start_percent)
                self._emit_progress(progress, status)
        return base64.b64encode(buffer).decode("ascii")

    def _copy_tree_with_progress(self, source: Path, destination: Path) -> int:
        files: list[Path] = []
        for root, _, names in os.walk(source):
            for name in names:
                files.append(Path(root) / name)

        if not files:
            raise RuntimeError("解压后的备份里没有文件，无法恢复。")

        total = len(files)
        for index, src_file in enumerate(files, start=1):
            relative = src_file.relative_to(source)
            dst_file = destination / relative
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            percent = 82 + (index / total) * 16
            self._emit_progress(percent, f"正在写入本地存档文件... {index}/{total}")
        return total

    def _copy_directory_snapshot(self, source: Path, destination: Path) -> None:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination, copy_function=shutil.copy2)

    def _replace_directory_contents(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for child in destination.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, copy_function=shutil.copy2)
            else:
                shutil.copy2(child, target)

    def _extract_zip_with_timestamps(self, archive: zipfile.ZipFile, destination: Path) -> None:
        for member in archive.infolist():
            extracted_path = Path(archive.extract(member, path=destination))
            if member.is_dir():
                continue
            try:
                timestamp = time.mktime(member.date_time + (0, 0, -1))
                os.utime(extracted_path, (timestamp, timestamp))
            except (OverflowError, OSError, ValueError):
                pass

    def _build_new_save_folder(self, current_save_dir: Path) -> Path:
        parent = current_save_dir.parent
        base_name = f"{current_save_dir.name}_github_{time.strftime('%Y%m%d_%H%M%S')}"
        candidate = parent / base_name
        serial = 1
        while candidate.exists():
            candidate = parent / f"{base_name}_{serial}"
            serial += 1
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    def _build_local_info(self, save_dir: Path) -> dict:
        game = self._current_game()
        return {
            "uploaded_at": str(game.get("last_uploaded_at", "")),
        }

    def _build_upload_metadata(self, local_info: dict, zip_sha256: str, zip_size: int, save_dir: Path) -> dict:
        return {
            "uploaded_at": self._now_text(),
            "device_name": self._get_device_name(),
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
            if not uploaded_at:
                return ""
            return f"最近一次上传到云端的时间：{uploaded_at}"

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
        local_info = self.local_info_cache
        remote_info = self.remote_info_cache

        if not local_info:
            self.suggestion_label.setText("建议：先到设置页选择本地存档目录。")
            return
        if not remote_info:
            self.suggestion_label.setText("建议：本地信息已读取。现在可以刷新云端信息，或直接先上传第一份备份。")
            return

        local_time = str(local_info.get("uploaded_at", "")).strip()
        remote_time = str(remote_info.get("uploaded_at", "")).strip()
        local_timestamp = self._parse_time_text(local_time)
        remote_timestamp = self._parse_time_text(remote_time)

        if remote_info.get("not_uploaded"):
            message = "建议：云端尚未上传存档，可上传。"
        elif not local_time and not remote_time:
            message = "建议：本地和云端都还没有上传记录。"
        elif not remote_time:
            message = "建议：云端尚未上传存档，可上传。"
        elif not local_time:
            message = f"建议：云端已有上传记录。云端最近上传时间 {remote_time}。"
        elif local_timestamp is None or remote_timestamp is None:
            if local_time == remote_time:
                message = f"建议：本地和云端上传记录一致。最近上传时间 {local_time}。"
            else:
                message = f"建议：本地和云端上传记录不一致。本地最近上传时间 {local_time or '无'}，云端最近上传时间 {remote_time or '无'}。"
        elif local_timestamp > remote_timestamp:
            message = f"建议：本地上传记录比云端新，可上传。本地最近上传时间 {local_time}，云端最近上传时间 {remote_time}。"
        elif local_timestamp < remote_timestamp:
            message = f"建议：云端上传记录比本地新，可下载。云端最近上传时间 {remote_time}，本地最近上传时间 {local_time}。"
        else:
            message = f"建议：本地和云端上传记录一致。最近上传时间 {local_time}。"
        self.suggestion_label.setText(message)

    def _show_compare_popup(self, context: str) -> None:
        if not self.local_info_cache or not self.remote_info_cache:
            return
        if context == "auto" and self.compare_popup_shown:
            return

        local_latest = str(self.local_info_cache.get("uploaded_at", "")).strip() or "无"
        remote_latest = str(self.remote_info_cache.get("uploaded_at", "")).strip() or "无"
        suggestion = self._build_popup_suggestion(local_latest, remote_latest)
        self.compare_popup_shown = True
        self._show_info("上传记录对比建议", f"本地最近上传时间：{local_latest}\n云端最近上传时间：{remote_latest}\n\n{suggestion}")

    def _build_popup_suggestion(self, local_time: str, remote_time: str) -> str:
        local_timestamp = self._parse_time_text(local_time)
        remote_timestamp = self._parse_time_text(remote_time)

        if remote_time == "无":
            return "云端尚未上传存档，可上传。"
        if local_time == "无":
            return f"云端已有上传记录，最近上传时间 {remote_time}。"
        if local_timestamp is None or remote_timestamp is None:
            if local_time == remote_time:
                return "本地和云端上传记录一致。"
            return "本地和云端上传记录不一致，请人工确认。"
        if local_timestamp > remote_timestamp:
            return "本地上传记录比云端新，可上传。"
        if local_timestamp < remote_timestamp:
            return "云端上传记录比本地新，可下载。"
        return "本地和云端上传记录一致。"

    def _metadata_path_for_zip(self, zip_path: str) -> str:
        return metadata_path_for_zip(zip_path)

    def _get_remote_metadata(self, repo: str, json_path: str, branch: str) -> dict:
        try:
            return get_remote_metadata(self._token(), repo, json_path, branch)
        except RuntimeError as exc:
            if "404" in str(exc):
                return self._build_empty_remote_info()
            raise

    def _get_download_url(self, repo: str, zip_path: str, branch: str) -> str:
        return get_download_url(self._token(), repo, zip_path, branch)

    def _get_existing_file_sha(self, repo: str, remote_path: str, branch: str) -> str | None:
        return get_existing_file_sha(self._token(), repo, remote_path, branch)

    def _put_file_to_github(
        self,
        repo: str,
        remote_path: str,
        branch: str,
        encoded_content: str,
        commit_message: str,
        existing_sha: str | None,
    ) -> None:
        put_file_to_github(
            self._token(),
            repo,
            remote_path,
            branch,
            encoded_content,
            commit_message,
            existing_sha,
        )

    def _download_file(self, url: str) -> str:
        return download_file(url, self._emit_progress)

    def _validate_zip_members(self, zip_path: str) -> None:
        validate_zip_members(zip_path)

    def _sha256_of_file(self, file_path: str) -> str:
        return sha256_of_file(file_path)

    def _format_size(self, size: int) -> str:
        return format_size(size)

    def _now_text(self) -> str:
        return now_text()

    def _format_timestamp(self, timestamp: float) -> str:
        return format_timestamp(timestamp)

    def _parse_time_text(self, text: str) -> float | None:
        return parse_time_text(text)

    def _default_device_name(self) -> str:
        return default_device_name()

    def _sanitize_device_name(self, value: str) -> str:
        return sanitize_device_name(value)

    def _get_device_name(self) -> str:
        sanitized = self._sanitize_device_name(self._device_name_text())
        return sanitized or self._default_device_name()

    def _ensure_device_name_present(self) -> None:
        self.device_name_edit.setText(self._get_device_name())

    def _on_device_name_changed(self, *_args) -> None:
        current = self._device_name_text()
        sanitized = self._sanitize_device_name(current)
        if current != sanitized:
            cursor = self.device_name_edit.cursorPosition()
            self.device_name_edit.blockSignals(True)
            self.device_name_edit.setText(sanitized)
            self.device_name_edit.setCursorPosition(min(cursor, len(sanitized)))
            self.device_name_edit.blockSignals(False)

    def _prompt_for_device_name_if_needed(self) -> None:
        if not self.prompt_for_device_name:
            return
        self.prompt_for_device_name = False
        initial_value = self._device_name_text().strip() or self._default_device_name()
        response, ok = QInputDialog.getText(
            self,
            "设备信息",
            "首次运行请填写本机设备信息（只允许中英文和数字）：",
            text=initial_value,
        )
        if ok:
            self.device_name_edit.setText(self._sanitize_device_name(response) or initial_value)
            self._save_config()

    def _on_download_mode_changed(self) -> None:
        self._refresh_action_states()
        self._save_config()

    def _on_backup_option_changed(self) -> None:
        self._save_config()
        self._refresh_action_states()

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

    def _set_pending_restore_state(self, state: dict | None) -> None:
        self.pending_restore_state = self._normalize_pending_restore_state(state)
        self._update_pending_restore_ui()
        self._save_config()

    def _update_pending_restore_ui(self) -> None:
        pending = self.pending_restore_state
        if pending:
            self.pending_notice_label.setText(
                f"当前有未确认的覆盖备份。\n生成时间：{pending.get('created_at', '未知')}\n本地存档目录：{pending.get('source_save_dir', '未知')}\n副本位置：{pending.get('backup_dir', '未知')}"
            )
            self.pending_action_group.show()
        else:
            self.pending_notice_label.setText("")
            self.pending_action_group.hide()
        self._refresh_action_states()

    def _refresh_action_states(self) -> None:
        upload_state = not self.is_busy
        download_state = (not self.is_busy) and (not self.pending_restore_state)
        mode_state = (not self.is_busy) and (not self.pending_restore_state)
        backup_state = mode_state and self.overwrite_radio.isChecked()
        pending_state = bool(self.pending_restore_state) and (not self.is_busy)

        self.upload_button.setEnabled(upload_state)
        self.download_button.setEnabled(download_state)
        self.overwrite_radio.setEnabled(mode_state)
        self.new_folder_radio.setEnabled(mode_state)
        self.backup_before_overwrite_check.setEnabled(backup_state)
        self.confirm_backup_button.setEnabled(pending_state)
        self.rollback_backup_button.setEnabled(pending_state)

    def _pending_backup_dir(self) -> Path:
        return self.script_dir / PENDING_BACKUP_DIR_NAME / self.current_game_id

    def _create_pending_backup_state(self, save_dir: Path) -> dict:
        pending_root = self._pending_backup_dir()
        snapshot_dir = pending_root / "save_data"
        if pending_root.exists():
            shutil.rmtree(pending_root, ignore_errors=True)
        pending_root.mkdir(parents=True, exist_ok=True)
        self._copy_directory_snapshot(save_dir, snapshot_dir)
        return {
            "backup_dir": str(snapshot_dir),
            "source_save_dir": str(save_dir),
            "created_at": self._now_text(),
        }

    def confirm_backup_result(self) -> None:
        if not self.pending_restore_state:
            return
        pending_root = Path(self.pending_restore_state["backup_dir"]).parent
        if pending_root.exists():
            shutil.rmtree(pending_root, ignore_errors=True)
        self.append_log("已确认覆盖正确，已删除本地副本。")
        self._set_pending_restore_state(None)
        self._show_info("已确认", "已确认这次覆盖正确，本地副本已删除。")

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
            if not self.pending_restore_state:
                raise RuntimeError("当前没有可回退的本地副本。")
            backup_dir = Path(self.pending_restore_state["backup_dir"])
            save_dir = Path(self.pending_restore_state["source_save_dir"])
            if not backup_dir.exists():
                raise RuntimeError("本地副本已不存在，无法回退。")
            self._replace_directory_contents(backup_dir, save_dir)
            pending_root = backup_dir.parent
            if pending_root.exists():
                shutil.rmtree(pending_root, ignore_errors=True)
            self._emit_pending_restore_state(None)
            self._emit_done("已回退到下载前的本地副本存档。")
        except Exception as exc:
            self._emit_log(f"回退存档失败：{exc}")
            self._emit_error(str(exc))

    def _detect_default_save_path(self, saved_path: str, saved_game_root: str) -> str:
        if saved_path:
            path = Path(saved_path)
            if path.exists() and path.is_dir():
                return str(path)
        return ""

    def _quick_detect_in_script_dir(self) -> str:
        return self._find_best_save_path_in_root(self.script_dir)

    def _find_best_save_path_in_root(self, root: Path) -> str:
        if not root.exists():
            return ""
        candidates: list[Path] = []
        try:
            for path in root.rglob("game_data.sav"):
                parent = path.parent
                if not parent.name.isdigit():
                    continue
                save_root = parent.parent
                if (save_root / "option.sav").exists():
                    candidates.append(save_root)
        except (OSError, RuntimeError):
            return ""

        if not candidates:
            return ""

        unique_candidates = []
        seen = set()
        for path in candidates:
            text = str(path.resolve())
            if text not in seen:
                seen.add(text)
                unique_candidates.append(path)

        unique_candidates.sort(key=self._save_candidate_score, reverse=True)
        return str(unique_candidates[0])

    def _scan_root_directory_for_save(self, root: Path) -> str:
        all_dirs: list[Path] = []
        for current_root, dirs, _ in os.walk(root):
            all_dirs.append(Path(current_root))
            for dir_name in dirs:
                _ = dir_name

        total = max(len(all_dirs), 1)
        candidates: list[Path] = []

        for index, current_dir in enumerate(all_dirs, start=1):
            percent = 5 + (index / total) * 85
            self._emit_progress(percent, f"正在扫描目录... {index}/{total}")
            try:
                if (current_dir / "option.sav").exists():
                    slot_dirs = [child for child in current_dir.iterdir() if child.is_dir() and child.name.isdigit()]
                    if slot_dirs:
                        for slot_dir in slot_dirs:
                            if (slot_dir / "game_data.sav").exists():
                                candidates.append(current_dir)
                                break
            except OSError:
                pass

        if not candidates:
            return ""

        unique_candidates = []
        seen = set()
        for path in candidates:
            text = str(path.resolve())
            if text not in seen:
                seen.add(text)
                unique_candidates.append(path)

        unique_candidates.sort(key=self._save_candidate_score, reverse=True)
        return str(unique_candidates[0])

    def _save_candidate_score(self, path: Path) -> tuple[int, float]:
        score = 0
        digit_dirs = [child for child in path.iterdir() if child.is_dir() and child.name.isdigit()]
        score += len(digit_dirs)
        if (path / "option.sav").exists():
            score += 3
        if (path / "tracker" / "trackblock00.sav").exists():
            score += 5
        try:
            latest = max(file.stat().st_mtime for file in path.rglob("*") if file.is_file())
        except (ValueError, OSError):
            latest = 0.0
        return score, latest

    def _show_info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)


def main() -> None:
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    window = GamesCloudSaveApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
