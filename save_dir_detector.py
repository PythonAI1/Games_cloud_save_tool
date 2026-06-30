import os
from dataclasses import dataclass, field
from pathlib import Path


SAVE_EXTENSIONS = {
    ".sav",
    ".save",
    ".dat",
    ".bin",
    ".db",
    ".slot",
    ".state",
    ".srm",
    ".dsv",
    ".rpgsave",
    ".rvdata",
    ".rvdata2",
    ".rxdata",
}

GOOD_NAME_HINTS = {
    "save",
    "saves",
    "savedata",
    "save_data",
    "saved games",
    "profile",
    "slot",
    "user",
    "mlc",
    "nand",
    "bis",
}

BAD_NAME_HINTS = {
    "cache",
    "shader",
    "logs",
    "log",
    "temp",
    "tmp",
    "crash",
    "dump",
    "screenshots",
    "screenshot",
    "video",
    "videos",
    "backup",
}

SKIP_DIR_NAMES = BAD_NAME_HINTS | {
    ".git",
    "__pycache__",
    "node_modules",
    "packages",
    "windows",
    "program files",
    "program files (x86)",
}

KNOWN_SAVE_LOCATION_RULES = [
    {
        "name": "Cemu Wii U",
        "match": ["cemu.exe"],
        "paths": [
            "{emulator_dir}/mlc01/usr/save",
            "{emulator_dir}/mlc/usr/save",
        ],
        "note": "Cemu 常见存档在 MLC 目录下。",
    },
    {
        "name": "Ryujinx Switch",
        "match": ["ryujinx.exe"],
        "paths": [
            "{appdata}/Ryujinx/bis/user/save",
            "{appdata}/Ryujinx/portable/bis/user/save",
        ],
        "note": "Ryujinx 存档通常在 bis/user/save 下。",
    },
    {
        "name": "Yuzu / Suyu Switch",
        "match": ["yuzu.exe", "suyu.exe"],
        "paths": [
            "{appdata}/yuzu/nand/user/save",
            "{appdata}/suyu/nand/user/save",
        ],
        "note": "Yuzu/Suyu 常见存档在 nand/user/save 下。",
    },
    {
        "name": "PCSX2 PS2",
        "match": ["pcsx2.exe", "pcsx2-qt.exe"],
        "paths": [
            "{documents}/PCSX2/memcards",
            "{documents}/PCSX2/sstates",
            "{emulator_dir}/memcards",
        ],
        "note": "PCSX2 多数游戏存在记忆卡文件中。",
    },
    {
        "name": "RPCS3 PS3",
        "match": ["rpcs3.exe"],
        "paths": [
            "{emulator_dir}/dev_hdd0/home/00000001/savedata",
            "{appdata}/rpcs3/dev_hdd0/home/00000001/savedata",
        ],
        "note": "RPCS3 常见存档在 dev_hdd0/home/.../savedata。",
    },
    {
        "name": "PPSSPP PSP",
        "match": ["ppssppwindows64.exe", "ppsspp.exe"],
        "paths": [
            "{documents}/PPSSPP/PSP/SAVEDATA",
            "{emulator_dir}/memstick/PSP/SAVEDATA",
        ],
        "note": "PPSSPP 常见存档在 PSP/SAVEDATA。",
    },
    {
        "name": "Dolphin GameCube / Wii",
        "match": ["dolphin.exe"],
        "paths": [
            "{documents}/Dolphin Emulator/GC",
            "{documents}/Dolphin Emulator/Wii/title",
            "{appdata}/Dolphin Emulator/GC",
            "{appdata}/Dolphin Emulator/Wii/title",
        ],
        "note": "Dolphin 的 GC 和 Wii 存档位置不同。",
    },
    {
        "name": "RetroArch",
        "match": ["retroarch.exe"],
        "paths": [
            "{emulator_dir}/saves",
            "{emulator_dir}/states",
            "{appdata}/RetroArch/saves",
            "{appdata}/RetroArch/states",
        ],
        "note": "RetroArch 通常区分 saves 和 states。",
    },
    {
        "name": "Steam / 普通 PC 游戏",
        "match": [],
        "paths": [
            "{user}/Documents/My Games",
            "{saved_games}",
            "{documents}",
            "{localappdata}",
            "{locallow}",
            "{appdata}",
            "{game_dir}",
        ],
        "note": "普通 PC 游戏位置差异较大，这些只是常见父目录。",
    },
]


@dataclass
class FileState:
    modified_at: float
    size: int


@dataclass
class ChangedFile:
    path: Path
    old_state: FileState | None
    new_state: FileState


@dataclass
class SaveDirCandidate:
    folder: Path
    score: int
    reason: str
    source: str
    exists: bool
    changed_files: list[ChangedFile] = field(default_factory=list)


def _existing_dir_or_empty(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    return str(path) if path.exists() and path.is_dir() else ""


def build_path_context(game_root: str, emulator_path: str) -> dict[str, str]:
    user = Path.home()
    documents = user / "Documents"
    saved_games = user / "Saved Games"
    appdata = os.getenv("APPDATA", "")
    localappdata = os.getenv("LOCALAPPDATA", "")
    locallow = str(Path(localappdata).parent / "LocalLow") if localappdata else ""
    emulator = Path(emulator_path) if emulator_path else None

    return {
        "user": str(user),
        "documents": _existing_dir_or_empty(str(documents)),
        "saved_games": _existing_dir_or_empty(str(saved_games)),
        "appdata": _existing_dir_or_empty(appdata),
        "localappdata": _existing_dir_or_empty(localappdata),
        "locallow": _existing_dir_or_empty(locallow),
        "emulator_dir": str(emulator.parent) if emulator is not None and emulator.exists() else "",
        "game_dir": _existing_dir_or_empty(game_root),
    }


def _expand_rule_path(template: str, context: dict[str, str]) -> Path | None:
    try:
        value = template.format(**context)
    except KeyError:
        return None
    value = value.strip()
    if not value:
        return None
    return Path(value.replace("/", os.sep))


def _candidate_key(path: Path) -> str:
    return str(path).casefold()


def build_reference_candidates(game_root: str, emulator_path: str) -> list[SaveDirCandidate]:
    context = build_path_context(game_root, emulator_path)
    executable_name = Path(emulator_path).name.casefold() if emulator_path else ""
    candidates: dict[str, SaveDirCandidate] = {}

    for rule in KNOWN_SAVE_LOCATION_RULES:
        matches = {item.casefold() for item in rule["match"]}
        is_generic_rule = not matches
        is_matched_rule = bool(executable_name and executable_name in matches)
        if not is_generic_rule and not is_matched_rule:
            continue

        for template in rule["paths"]:
            folder = _expand_rule_path(template, context)
            if folder is None:
                continue
            exists = folder.exists() and folder.is_dir()
            score = 80 if is_matched_rule else 35
            if exists:
                score += 20
            key = _candidate_key(folder)
            reason = f"{rule['name']}：{rule['note']}"
            candidate = SaveDirCandidate(
                folder=folder,
                score=score,
                reason=reason,
                source="规则参考",
                exists=exists,
            )
            previous = candidates.get(key)
            if previous is None or candidate.score > previous.score:
                candidates[key] = candidate

    result = list(candidates.values())
    result.sort(key=lambda item: (item.score, item.exists), reverse=True)
    return result


def collect_scan_roots(game_root: str, emulator_path: str) -> list[Path]:
    context = build_path_context(game_root, emulator_path)
    roots: list[Path] = []

    for key in ("game_dir", "emulator_dir", "documents", "saved_games", "localappdata", "locallow", "appdata"):
        value = context.get(key, "")
        if not value:
            continue
        path = Path(value)
        if path.exists() and path.is_dir() and path not in roots:
            roots.append(path)

    return roots


def take_snapshot(roots: list[Path], max_files: int = 60000, max_depth: int = 8) -> dict[str, FileState]:
    snapshot: dict[str, FileState] = {}
    scanned = 0

    for root in roots:
        root_depth = len(root.parts)
        for current_root, dir_names, file_names in os.walk(root):
            current_path = Path(current_root)
            if len(current_path.parts) - root_depth >= max_depth:
                dir_names[:] = []
            dir_names[:] = [
                name for name in dir_names
                if name.casefold() not in SKIP_DIR_NAMES
            ]

            for file_name in file_names:
                if scanned >= max_files:
                    return snapshot
                path = current_path / file_name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                snapshot[str(path)] = FileState(modified_at=stat.st_mtime, size=stat.st_size)
                scanned += 1

    return snapshot


def diff_snapshots(before: dict[str, FileState], after: dict[str, FileState]) -> list[ChangedFile]:
    changes: list[ChangedFile] = []

    for path_text, new_state in after.items():
        old_state = before.get(path_text)
        if old_state is None:
            changes.append(ChangedFile(Path(path_text), None, new_state))
            continue
        if old_state.modified_at != new_state.modified_at or old_state.size != new_state.size:
            changes.append(ChangedFile(Path(path_text), old_state, new_state))

    return changes


def build_change_candidates(changes: list[ChangedFile]) -> list[SaveDirCandidate]:
    grouped: dict[Path, list[ChangedFile]] = {}
    for change in changes:
        grouped.setdefault(change.path.parent, []).append(change)

    candidates: list[SaveDirCandidate] = []
    for folder, files in grouped.items():
        folder_text = str(folder).casefold()
        file_names = [item.path.name.casefold() for item in files]
        extensions = {item.path.suffix.casefold() for item in files}
        score = 100
        reasons: list[str] = ["检测到运行后文件变化"]

        if extensions & SAVE_EXTENSIONS:
            score += 40
            reasons.append("包含常见存档扩展名")
        if any(hint in folder_text for hint in GOOD_NAME_HINTS):
            score += 30
            reasons.append("目录名像存档目录")
        if any(hint in folder_text for hint in BAD_NAME_HINTS):
            score -= 60
            reasons.append("目录名像缓存或日志目录")
        if len(files) <= 20:
            score += 15
            reasons.append("修改文件数量合理")
        else:
            score -= 15
            reasons.append("修改文件较多，可能不是单纯存档")
        if any("config" in name or "setting" in name for name in file_names):
            score -= 8
            reasons.append("包含配置类文件")

        candidates.append(
            SaveDirCandidate(
                folder=folder,
                score=score,
                reason="；".join(reasons),
                source="变化检测",
                exists=folder.exists() and folder.is_dir(),
                changed_files=files,
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def merge_candidates(*groups: list[SaveDirCandidate]) -> list[SaveDirCandidate]:
    merged: dict[str, SaveDirCandidate] = {}
    for group in groups:
        for candidate in group:
            key = _candidate_key(candidate.folder)
            previous = merged.get(key)
            if previous is None or candidate.score > previous.score:
                merged[key] = candidate

    result = list(merged.values())
    result.sort(key=lambda item: item.score, reverse=True)
    return result
