import os
import re
import string
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
}

GENERIC_GAME_KEYWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "for",
    "with",
    "game",
    "games",
    "gaming",
    "save",
    "saves",
    "savedata",
    "save_data",
    "saved",
    "data",
    "user",
    "profile",
    "slot",
    "launcher",
    "prelauncher",
    "starter",
    "start",
    "client",
    "helper",
    "bootstrapper",
    "updater",
    "update",
    "setup",
    "install",
    "installer",
    "uninstall",
    "unins",
    "crashreporter",
    "emulator",
    "emu",
    "exe",
    "steam",
    "steamlibrary",
    "epic",
    "gog",
    "ubisoft",
    "origin",
    "ea",
    "rockstar",
    "xbox",
    "windows",
    "win",
    "win64",
    "win32",
    "x64",
    "x86",
    "bin",
    "binaries",
    "shipping",
    "release",
    "debug",
    "program",
    "files",
    "file",
    "users",
    "public",
    "appdata",
    "local",
    "locallow",
    "roaming",
    "documents",
    "desktop",
    "python",
    "programs",
    "apps",
    "cloud",
    "main",
    "my",
    "codex",
    "portable",
    "repack",
    "fitgirl",
    "dodi",
    "steamapps",
    "common",
}

GENERIC_GAME_KEYWORD_FRAGMENTS = {
    "launcher",
    "prelauncher",
    "bootstrapper",
    "updater",
    "installer",
    "uninstall",
    "crashreporter",
}

EMULATOR_EXECUTABLE_NAMES = {
    "cemu",
    "ryujinx",
    "yuzu",
    "suyu",
    "pcsx2",
    "pcsx2-qt",
    "rpcs3",
    "ppsspp",
    "ppssppwindows64",
    "dolphin",
    "retroarch",
}

SAVE_DIR_NAME_HINTS = {
    "save",
    "saves",
    "saved",
    "savedata",
    "save_data",
    "saved games",
    "profile",
    "profiles",
    "userdata",
    "user_data",
    "steam_settings",
    "settings",
}

GLOBAL_SEARCH_ROOT_NAMES = (
    "Games",
    "Game",
    "SteamLibrary",
    "SteamLibrary/steamapps/common",
    "steamapps/common",
    "Rockstar Games",
    "GOG Games",
    "Epic Games",
    "Program Files",
    "Program Files (x86)",
    "Documents",
    "Saved Games",
)

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
    keyword_score: int = 0
    matched_keywords: list[str] = field(default_factory=list)
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


def _is_generic_game_keyword(token: str) -> bool:
    if token in GENERIC_GAME_KEYWORDS or token in EMULATOR_EXECUTABLE_NAMES:
        return True
    return any(fragment in token for fragment in GENERIC_GAME_KEYWORD_FRAGMENTS)


def _split_keywords(value: str) -> set[str]:
    keywords: set[str] = set()
    tokens = re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", value.casefold())
    filtered_tokens: list[str] = []

    for token in tokens:
        if _is_generic_game_keyword(token):
            continue
        if token.isdigit():
            filtered_tokens.append(token)
            continue
        if len(token) < 2:
            continue
        filtered_tokens.append(token)
        keywords.add(token)

    compact = "".join(filtered_tokens)
    if len(compact) >= 4 and any(char.isalpha() for char in compact):
        keywords.add(compact)
    return keywords


def _nearest_useful_parent_name(path: Path, max_levels: int = 4) -> str:
    current = path.parent
    for _ in range(max_levels):
        if not current.name:
            return ""
        if _split_keywords(current.name):
            return current.name
        if current.parent == current:
            return ""
        current = current.parent
    return ""


def build_game_keywords(game_name: str, emulator_path: str, game_root: str, target_title: str = "") -> set[str]:
    keywords: set[str] = set()
    if not emulator_path:
        return keywords

    path = Path(emulator_path)
    if path.suffix:
        keywords.update(_split_keywords(path.stem))
    parent_name = _nearest_useful_parent_name(path)
    if parent_name:
        keywords.update(_split_keywords(parent_name))

    return keywords


def apply_game_keyword_boost(candidates: list[SaveDirCandidate], game_keywords: set[str], boost: int) -> list[SaveDirCandidate]:
    if not game_keywords:
        return candidates

    for candidate in candidates:
        path_text = str(candidate.folder).casefold()
        matched = sorted(keyword for keyword in game_keywords if keyword in path_text)
        if not matched:
            continue
        candidate.keyword_score = min(boost, 20 + 10 * len(matched))
        candidate.score += candidate.keyword_score
        candidate.matched_keywords = matched
        candidate.reason = f"{candidate.reason}；路径包含游戏相关关键词：{', '.join(matched[:5])}"

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def _is_skipped_directory(path: Path) -> bool:
    return any(part.casefold() in SKIP_DIR_NAMES for part in path.parts)


def _path_has_save_hint(path: Path) -> bool:
    name = path.name.casefold().replace("-", "_")
    return any(hint == name or hint in name for hint in SAVE_DIR_NAME_HINTS)


def _path_matches_keywords(path: Path, game_keywords: set[str]) -> bool:
    if not game_keywords:
        return False
    path_text = str(path).casefold()
    return any(keyword in path_text for keyword in game_keywords)


def _add_candidate(
    candidates: dict[str, SaveDirCandidate],
    folder: Path,
    score: int,
    source: str,
    reason: str,
) -> None:
    if not folder.exists() or not folder.is_dir() or _is_skipped_directory(folder):
        return
    key = _candidate_key(folder)
    candidate = SaveDirCandidate(
        folder=folder,
        score=score,
        reason=reason,
        source=source,
        exists=True,
    )
    previous = candidates.get(key)
    if previous is None or candidate.score > previous.score:
        candidates[key] = candidate


def _fixed_drive_roots() -> list[Path]:
    if os.name != "nt":
        return [Path("/")]
    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots


def _global_common_search_roots(context: dict[str, str]) -> list[Path]:
    roots: list[Path] = []
    for key in ("documents", "saved_games", "localappdata", "locallow", "appdata"):
        value = context.get(key, "")
        if value:
            path = Path(value)
            if path.exists() and path.is_dir() and path not in roots:
                roots.append(path)

    for drive in _fixed_drive_roots():
        for name in GLOBAL_SEARCH_ROOT_NAMES:
            path = drive / Path(name)
            if path.exists() and path.is_dir() and path not in roots:
                roots.append(path)
    return roots


def _walk_candidate_dirs(root: Path, max_depth: int, max_dirs: int) -> list[Path]:
    found: list[Path] = []
    root_depth = len(root.parts)
    for current_root, dir_names, _file_names in os.walk(root):
        current = Path(current_root)
        if len(current.parts) - root_depth >= max_depth:
            dir_names[:] = []
        dir_names[:] = [
            name for name in dir_names
            if name.casefold() not in SKIP_DIR_NAMES
        ]
        found.append(current)
        if len(found) >= max_dirs:
            break
    return found


def _game_install_roots(emulator_path: str) -> list[Path]:
    if not emulator_path:
        return []
    path = Path(emulator_path)
    if not path.exists():
        return []
    current = path.parent if path.is_file() else path
    roots: list[Path] = []
    for _ in range(4):
        if current.exists() and current.is_dir() and current not in roots:
            roots.append(current)
        if current.parent == current:
            break
        current = current.parent
    return roots


def _build_game_root_candidates(emulator_path: str, game_keywords: set[str]) -> list[SaveDirCandidate]:
    candidates: dict[str, SaveDirCandidate] = {}
    for root in _game_install_roots(emulator_path):
        for folder in _walk_candidate_dirs(root, max_depth=4, max_dirs=1200):
            if folder == root:
                continue
            if not _path_has_save_hint(folder):
                continue
            score = 135
            reason = "游戏根目录附近发现存档特征目录"
            if _path_matches_keywords(folder, game_keywords):
                score += 45
                reason += "，且路径包含游戏相关关键词"
            _add_candidate(candidates, folder, score, "游戏目录搜索", reason)
    return list(candidates.values())


def _build_global_keyword_candidates(context: dict[str, str], game_keywords: set[str]) -> list[SaveDirCandidate]:
    if not game_keywords:
        return []

    candidates: dict[str, SaveDirCandidate] = {}
    scanned_dirs = 0
    for root in _global_common_search_roots(context):
        for folder in _walk_candidate_dirs(root, max_depth=5, max_dirs=1000):
            scanned_dirs += 1
            if scanned_dirs > 12000:
                break
            if not _path_matches_keywords(folder, game_keywords):
                continue
            score = 125
            reason = "常见位置下发现路径包含游戏相关关键词"
            if _path_has_save_hint(folder):
                score += 35
                reason += "，且目录名像存档目录"
            _add_candidate(candidates, folder, score, "全局常见位置搜索", reason)
            if len(candidates) >= 80:
                return list(candidates.values())
        if scanned_dirs > 12000:
            break
    return list(candidates.values())


def build_reference_candidates(
    game_root: str,
    emulator_path: str,
    game_name: str = "",
    target_title: str = "",
) -> list[SaveDirCandidate]:
    context = build_path_context(game_root, emulator_path)
    executable_name = Path(emulator_path).name.casefold() if emulator_path else ""
    candidates: dict[str, SaveDirCandidate] = {}
    game_keywords = build_game_keywords(game_name, emulator_path, game_root, target_title)

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

    for candidate in _build_game_root_candidates(emulator_path, game_keywords):
        key = _candidate_key(candidate.folder)
        previous = candidates.get(key)
        if previous is None or candidate.score > previous.score:
            candidates[key] = candidate

    for candidate in _build_global_keyword_candidates(context, game_keywords):
        key = _candidate_key(candidate.folder)
        previous = candidates.get(key)
        if previous is None or candidate.score > previous.score:
            candidates[key] = candidate

    result = list(candidates.values())
    apply_game_keyword_boost(result, game_keywords, boost=70)
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


def build_change_candidates(changes: list[ChangedFile], game_keywords: set[str] | None = None) -> list[SaveDirCandidate]:
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

    apply_game_keyword_boost(candidates, game_keywords or set(), boost=45)
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
