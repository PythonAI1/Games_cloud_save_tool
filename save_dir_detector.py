import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path


SAVE_EXTENSIONS = {
    ".sav",
    ".save",
    ".slot",
    ".state",
    ".srm",
    ".dsv",
    ".rpgsave",
    ".rvdata",
    ".rvdata2",
    ".rxdata",
}

WEAK_SAVE_EXTENSIONS = {
    ".dat",
    ".bin",
    ".db",
}

SAVE_FEATURE_FILE_NAMES = {
    "user.dat",
    "account.dat",
    "sav.dat",
    "save.dat",
    "game_data",
    "gamedata",
    "progress",
    "checkpoint",
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
    "assetcache",
    "persistentdownloaddir",
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

TERMINAL_SAVE_HINT_SCORES = (
    ("save/saves/savedata", 60, {"save", "saves", "savedata", "savegame", "savegames", "gamesaves"}),
    ("profile/profiles", 45, {"profile", "profiles", "playerprofile", "playerprofiles"}),
    ("userdata/user_data", 25, {"userdata", "user_data"}),
    ("steam_settings", 25, {"steam_settings", "steamsettings"}),
    ("remote", 15, {"remote"}),
    ("slot/slots", 15, {"slot", "slots"}),
    ("checkpoint/checkpoints", 12, {"checkpoint", "checkpoints"}),
    ("autosave/autosaves", 12, {"autosave", "autosaves"}),
    ("settings/config", 5, {"settings", "config"}),
)

NO_GAME_KEYWORD_SCORE_CAP = 120

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


def _normalized_terminal_name(path: Path) -> str:
    return re.sub(r"[\s_\-]+", "", path.name.casefold())


def _normalized_match_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def _terminal_save_hint_score(path: Path, has_game_keyword: bool) -> tuple[int, str]:
    name = _normalized_terminal_name(path)
    for label, score, hints in TERMINAL_SAVE_HINT_SCORES:
        normalized_hints = {re.sub(r"[\s_\-]+", "", hint) for hint in hints}
        if name not in normalized_hints and not any(name.startswith(hint) for hint in normalized_hints):
            continue
        if label == "profile/profiles" and not has_game_keyword:
            return 10, "末端目录名像 profile，但未命中游戏关键词，降权处理"
        if label == "settings/config" and not has_game_keyword:
            return 0, ""
        return score, f"末端目录名命中存档特征：{label}"
    return 0, ""


def _terminal_bad_hint_score(path: Path) -> tuple[int, str]:
    name = _normalized_terminal_name(path)
    if any(hint in name for hint in ("cache", "shader", "log", "temp", "tmp", "crash", "dump")):
        return -80, "末端目录名像缓存、日志或崩溃目录"
    if any(hint in name for hint in ("screenshot", "screenshots", "video", "videos", "replay", "replays")):
        return -50, "末端目录名像截图、视频或回放目录"
    return 0, ""


def _looks_like_id_directory(path: Path) -> bool:
    name = path.name.casefold()
    if name.isdigit() and len(name) >= 4:
        return True
    return bool(re.fullmatch(r"[0-9a-f]{8,}", name))


def _file_has_save_feature(path: Path) -> bool:
    name = path.name.casefold()
    stem = path.stem.casefold()
    if path.suffix.casefold() in SAVE_EXTENSIONS:
        return True
    if name in SAVE_FEATURE_FILE_NAMES or stem in SAVE_FEATURE_FILE_NAMES:
        return True
    if path.suffix.casefold() in WEAK_SAVE_EXTENSIONS:
        return any(token in name for token in ("savedata", "game_data", "gamedata", "progress", "checkpoint"))
    return False


def _direct_save_file_count(folder: Path, max_files: int = 200) -> int:
    count = 0
    scanned = 0
    try:
        for item in folder.iterdir():
            if not item.is_file():
                continue
            scanned += 1
            if _file_has_save_feature(item):
                count += 1
            if scanned >= max_files:
                break
    except OSError:
        return 0
    return count


def _child_id_dirs_with_save_files(folder: Path) -> list[Path]:
    result: list[Path] = []
    try:
        children = [item for item in folder.iterdir() if item.is_dir() and _looks_like_id_directory(item)]
    except OSError:
        return result
    for child in children:
        if _direct_save_file_count(child) > 0:
            result.append(child)
    return result


def _looks_like_save_slot_directory(path: Path) -> bool:
    if _looks_like_id_directory(path):
        return True
    name = _normalized_terminal_name(path)
    if _terminal_save_hint_score(path, has_game_keyword=True)[0] >= 12:
        return True
    return bool(re.fullmatch(r"(auto|manual|quick)?save\d+", name))


def _child_save_slot_dirs_with_save_files(folder: Path) -> list[Path]:
    result: list[Path] = []
    try:
        children = [item for item in folder.iterdir() if item.is_dir() and _looks_like_save_slot_directory(item)]
    except OSError:
        return result
    for child in children:
        if _direct_save_file_count(child) > 0:
            result.append(child)
    return result


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _score_root_location(folder: Path, context: dict[str, str], emulator_path: str) -> tuple[int, str]:
    for root in _game_install_roots(emulator_path):
        if _is_relative_to(folder, root):
            return 35, "位于游戏安装目录内或附近"

    checks = (
        ("saved_games", 25, "位于 Saved Games"),
        ("documents", 30, "位于 Documents / My Games"),
        ("localappdata", 20, "位于 AppData/Local"),
        ("appdata", 18, "位于 AppData/Roaming"),
        ("locallow", 18, "位于 AppData/LocalLow"),
    )
    for key, score, reason in checks:
        value = context.get(key, "")
        if value and _is_relative_to(folder, Path(value)):
            return score, reason

    path_text = str(folder).casefold().replace("/", "\\")
    if "\\steam\\userdata\\" in path_text or "\\steam\\userdata" in path_text:
        return 15, "位于 Steam userdata"
    if "programdata" in path_text or "\\public\\" in path_text:
        return 8, "位于 ProgramData 或 Public Documents"
    return 0, ""


def _score_relative_depth(folder: Path, context: dict[str, str], emulator_path: str) -> tuple[int, str]:
    roots: list[Path] = _game_install_roots(emulator_path)
    for key in ("saved_games", "documents", "localappdata", "appdata", "locallow"):
        value = context.get(key, "")
        if value:
            roots.append(Path(value))

    depths: list[int] = []
    for root in roots:
        try:
            depths.append(len(folder.resolve().relative_to(root.resolve()).parts))
        except (OSError, ValueError):
            continue
    if not depths:
        return 0, ""
    depth = min(depths)
    if depth > 8:
        return -20, "相对搜索根目录层级过深"
    if depth > 6:
        return -10, "相对搜索根目录层级略深"
    return 0, ""


def _matched_game_keywords(path: Path, game_keywords: set[str]) -> list[str]:
    if not game_keywords:
        return []
    path_text = str(path).casefold()
    compact_path_text = _normalized_match_text(path_text)
    return sorted(
        keyword for keyword in game_keywords
        if keyword in path_text or _normalized_match_text(keyword) in compact_path_text
    )


def _is_generic_game_keyword(token: str) -> bool:
    if token in GENERIC_GAME_KEYWORDS or token in EMULATOR_EXECUTABLE_NAMES:
        return True
    return any(fragment in token for fragment in GENERIC_GAME_KEYWORD_FRAGMENTS)


def _split_keywords(value: str, prefer_compact: bool = False) -> set[str]:
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
        if prefer_compact:
            return {compact}
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
        keywords.update(_split_keywords(path.stem, prefer_compact=True))
    parent_name = _nearest_useful_parent_name(path)
    if parent_name:
        keywords.update(_split_keywords(parent_name, prefer_compact=True))

    return keywords


def apply_game_keyword_boost(candidates: list[SaveDirCandidate], game_keywords: set[str], boost: int) -> list[SaveDirCandidate]:
    if not game_keywords:
        return candidates

    for candidate in candidates:
        matched = _matched_game_keywords(candidate.folder, game_keywords)
        if not matched:
            continue
        terminal_text = candidate.folder.name.casefold()
        terminal_matched = [keyword for keyword in matched if keyword in terminal_text]
        candidate.keyword_score = boost + min(30, max(0, len(matched) - 1) * 10)
        if terminal_matched:
            candidate.keyword_score += 30
        candidate.score += candidate.keyword_score
        candidate.matched_keywords = matched
        candidate.reason = f"{candidate.reason}；路径包含游戏相关关键词：{', '.join(matched[:5])}"

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def _is_skipped_directory(path: Path) -> bool:
    return any(part.casefold() in SKIP_DIR_NAMES for part in path.parts)


def _path_has_save_hint(path: Path) -> bool:
    return _terminal_save_hint_score(path, has_game_keyword=False)[0] > 0


def _path_matches_keywords(path: Path, game_keywords: set[str]) -> bool:
    if not game_keywords:
        return False
    path_text = str(path).casefold()
    compact_path_text = _normalized_match_text(path_text)
    return any(
        keyword in path_text or _normalized_match_text(keyword) in compact_path_text
        for keyword in game_keywords
    )


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


def _add_keyword_tree_save_candidates(
    candidates: dict[str, SaveDirCandidate],
    keyword_root: Path,
    source: str,
    base_score: int,
    max_depth: int = 6,
    max_dirs: int = 2000,
) -> int:
    added = 0
    for folder in _walk_candidate_dirs(keyword_root, max_depth=max_depth, max_dirs=max_dirs):
        if _terminal_bad_hint_score(folder)[0] < 0:
            continue

        save_hint_score = _terminal_save_hint_score(folder, has_game_keyword=True)[0]
        direct_save_files = _direct_save_file_count(folder)
        child_save_id_dirs = _child_id_dirs_with_save_files(folder)
        child_save_slot_dirs = _child_save_slot_dirs_with_save_files(folder)

        if len(child_save_slot_dirs) >= 2:
            reason = "多个同级 ID 或存档槽位子目录包含存档特征文件，推荐父目录避免遗漏账号或槽位"
            _add_candidate(candidates, folder, base_score + 125, source, reason)
            added += 1
            continue

        if len(child_save_id_dirs) == 1 and save_hint_score > 0:
            child = child_save_id_dirs[0]
            reason = "唯一 ID 子目录包含存档特征文件，推荐实际存档目录"
            _add_candidate(candidates, child, base_score + 130, source, reason)
            added += 1
            continue

        if direct_save_files > 0:
            if _looks_like_id_directory(folder):
                reason = "ID 目录中直接包含存档特征文件"
                score = base_score + 125
            elif save_hint_score > 0:
                reason = "存档特征目录中直接包含存档特征文件"
                score = base_score + 90
            else:
                reason = "目录中直接包含存档特征文件"
                score = base_score + 60
            _add_candidate(candidates, folder, score, source, reason)
            added += 1
            continue

        if save_hint_score > 0:
            reason = "游戏关键词目录下发现存档特征子目录"
            _add_candidate(candidates, folder, base_score + 55, source, reason)
            added += 1

    return added


def _apply_reference_scoring(
    candidates: list[SaveDirCandidate],
    context: dict[str, str],
    emulator_path: str,
    game_keywords: set[str],
) -> list[SaveDirCandidate]:
    apply_game_keyword_boost(candidates, game_keywords, boost=80)
    for candidate in candidates:
        has_game_keyword = bool(candidate.matched_keywords)

        terminal_score, terminal_reason = _terminal_save_hint_score(candidate.folder, has_game_keyword)
        if terminal_score:
            candidate.score += terminal_score
            candidate.reason = f"{candidate.reason}；{terminal_reason}"

        root_score, root_reason = _score_root_location(candidate.folder, context, emulator_path)
        if root_score:
            candidate.score += root_score
            candidate.reason = f"{candidate.reason}；{root_reason}"

        depth_score, depth_reason = _score_relative_depth(candidate.folder, context, emulator_path)
        if depth_score:
            candidate.score += depth_score
            candidate.reason = f"{candidate.reason}；{depth_reason}"

        bad_score, bad_reason = _terminal_bad_hint_score(candidate.folder)
        if bad_score:
            candidate.score += bad_score
            candidate.reason = f"{candidate.reason}；{bad_reason}"

        if not has_game_keyword and candidate.score > NO_GAME_KEYWORD_SCORE_CAP:
            candidate.score = NO_GAME_KEYWORD_SCORE_CAP
            candidate.reason = f"{candidate.reason}；未命中游戏关键词，参考候选分数封顶"

    candidates.sort(key=lambda item: (bool(item.matched_keywords), item.score, item.exists), reverse=True)
    return candidates


def _fixed_drive_roots() -> list[Path]:
    if os.name != "nt":
        return [Path("/")]
    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots


def fixed_drive_roots() -> list[Path]:
    return _fixed_drive_roots()


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
    install_roots = _game_install_roots(emulator_path)
    keyword_roots = [root for root in install_roots if _path_matches_keywords(root, game_keywords)]
    roots = keyword_roots or install_roots[:1]
    for root in roots:
        _add_keyword_tree_save_candidates(candidates, root, "游戏目录搜索", 80, max_depth=6, max_dirs=1800)
    return list(candidates.values())


def _build_global_keyword_candidates(context: dict[str, str], game_keywords: set[str]) -> list[SaveDirCandidate]:
    if not game_keywords:
        return []

    candidates: dict[str, SaveDirCandidate] = {}
    scanned_dirs = 0
    for root in _global_common_search_roots(context):
        for folder in _walk_candidate_dirs(root, max_depth=2, max_dirs=6000):
            if folder == root or not _path_matches_keywords(folder, game_keywords):
                continue
            _add_keyword_tree_save_candidates(candidates, folder, "全局常见位置搜索", 65, max_depth=7, max_dirs=1800)
            if len(candidates) >= 80:
                return list(candidates.values())

        for folder in _walk_candidate_dirs(root, max_depth=5, max_dirs=1000):
            scanned_dirs += 1
            if scanned_dirs > 12000:
                break
            if not _path_matches_keywords(folder, game_keywords):
                continue
            _add_keyword_tree_save_candidates(candidates, folder, "全局常见位置搜索", 50, max_depth=6, max_dirs=1200)
            if len(candidates) >= 80:
                return list(candidates.values())
        if scanned_dirs > 12000:
            break
    return list(candidates.values())


def build_deep_scan_candidates(
    roots: list[Path],
    game_keywords: set[str],
    progress_callback=None,
    max_dirs_per_root: int = 30000,
    max_keyword_roots: int = 120,
) -> list[SaveDirCandidate]:
    if not game_keywords:
        return []

    candidates: dict[str, SaveDirCandidate] = {}
    scanned_dirs = 0
    keyword_roots = 0
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        root_depth = len(root.parts)
        dirs_in_root = 0
        for current_root, dir_names, _file_names in os.walk(root):
            current = Path(current_root)
            dirs_in_root += 1
            scanned_dirs += 1
            if len(current.parts) - root_depth >= 10:
                dir_names[:] = []
            dir_names[:] = [
                name for name in dir_names
                if name.casefold() not in SKIP_DIR_NAMES
            ]

            if progress_callback and scanned_dirs % 100 == 0:
                if progress_callback(scanned_dirs, str(current)) is False:
                    return _apply_reference_scoring(list(candidates.values()), {}, "", game_keywords)

            if _path_matches_keywords(current, game_keywords):
                keyword_roots += 1
                _add_keyword_tree_save_candidates(
                    candidates,
                    current,
                    "磁盘扫描",
                    60,
                    max_depth=7,
                    max_dirs=1800,
                )
                if len(candidates) >= 120 or keyword_roots >= max_keyword_roots:
                    return _apply_reference_scoring(list(candidates.values()), {}, "", game_keywords)

            if dirs_in_root >= max_dirs_per_root:
                break

    return _apply_reference_scoring(list(candidates.values()), {}, "", game_keywords)


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

    result = _apply_reference_scoring(list(candidates.values()), context, emulator_path, game_keywords)
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


def _changed_files_have_save_feature(files: list[ChangedFile]) -> bool:
    return any(_file_has_save_feature(item.path) for item in files)


def _collect_changed_slot_parent_candidates(
    grouped: dict[Path, list[ChangedFile]],
) -> list[SaveDirCandidate]:
    parent_to_changed_slot_dirs: dict[Path, list[Path]] = {}
    changed_files_by_slot_dir: dict[Path, list[ChangedFile]] = {}

    for folder, files in grouped.items():
        if not _looks_like_save_slot_directory(folder) or not _changed_files_have_save_feature(files):
            continue
        parent_to_changed_slot_dirs.setdefault(folder.parent, []).append(folder)
        changed_files_by_slot_dir[folder] = files

    candidates: list[SaveDirCandidate] = []
    for parent, changed_slot_dirs in parent_to_changed_slot_dirs.items():
        sibling_slot_dirs_with_save_files = _child_save_slot_dirs_with_save_files(parent)
        sibling_keys = {_candidate_key(folder) for folder in sibling_slot_dirs_with_save_files}
        changed_keys = {_candidate_key(folder) for folder in changed_slot_dirs}
        all_evidence_slot_dirs = sibling_keys | changed_keys
        changed_files = [
            changed_file
            for folder in changed_slot_dirs
            for changed_file in changed_files_by_slot_dir.get(folder, [])
        ]

        if len(all_evidence_slot_dirs) >= 2:
            candidates.append(
                SaveDirCandidate(
                    folder=parent,
                    score=230,
                    reason="多个同级 ID 或存档槽位目录发生变化或包含强存档文件，推荐父目录避免遗漏账号或槽位",
                    source="变化检测",
                    exists=parent.exists() and parent.is_dir(),
                    changed_files=changed_files,
                )
            )
            continue

        folder = changed_slot_dirs[0]
        candidates.append(
            SaveDirCandidate(
                folder=folder,
                score=220,
                reason="ID 或存档槽位目录中检测到强存档文件变化，推荐实际存档目录",
                source="变化检测",
                exists=folder.exists() and folder.is_dir(),
                changed_files=changed_files_by_slot_dir.get(folder, []),
            )
        )

    return candidates


def build_change_candidates(
    changes: list[ChangedFile],
    game_keywords: set[str] | None = None,
    scan_roots: list[Path] | None = None,
) -> list[SaveDirCandidate]:
    grouped: dict[Path, list[ChangedFile]] = {}
    for change in changes:
        grouped.setdefault(change.path.parent, []).append(change)

    candidates: dict[str, SaveDirCandidate] = {}
    game_keywords = game_keywords or set()
    for folder, files in grouped.items():
        file_names = [item.path.name.casefold() for item in files]
        extensions = {item.path.suffix.casefold() for item in files}
        matched_keywords = _matched_game_keywords(folder, game_keywords)
        has_game_keyword = bool(matched_keywords)
        score = 100
        reasons: list[str] = ["检测到运行后文件变化"]

        if extensions & SAVE_EXTENSIONS:
            score += 40
            reasons.append("包含强存档扩展名")

        terminal_score, terminal_reason = _terminal_save_hint_score(folder, has_game_keyword)
        if terminal_score:
            score += terminal_score
            reasons.append(terminal_reason)

        strong_terminal_hint = terminal_score >= 25
        if extensions & WEAK_SAVE_EXTENSIONS and (has_game_keyword or strong_terminal_hint):
            score += 15
            reasons.append("包含弱存档扩展名，且有游戏关键词或存档目录特征")

        bad_score, bad_reason = _terminal_bad_hint_score(folder)
        if bad_score:
            score += bad_score
            reasons.append(bad_reason)

        if scan_roots:
            depths: list[int] = []
            for root in scan_roots:
                try:
                    depths.append(len(folder.resolve().relative_to(root.resolve()).parts))
                except (OSError, ValueError):
                    continue
            if depths:
                depth = min(depths)
                if depth > 8:
                    score -= 20
                    reasons.append("相对扫描根目录层级过深")
                elif depth > 6:
                    score -= 10
                    reasons.append("相对扫描根目录层级略深")

        if len(files) <= 20:
            score += 15
            reasons.append("修改文件数量合理")
        elif len(files) <= 100:
            score += 5
            reasons.append("修改文件数量中等")
        else:
            score -= 10
            reasons.append("修改文件较多，可能不是单纯存档")

        new_file_count = sum(1 for item in files if item.old_state is None)
        if new_file_count > len(files) / 2 and strong_terminal_hint:
            score += 10
            reasons.append("新增文件较多，且目录名像存档目录")
        if any("save" in name or "profile" in name or "slot" in name for name in file_names):
            score += 15
            reasons.append("文件名包含存档相关词")
        if any("config" in name or "setting" in name for name in file_names):
            score -= 8
            reasons.append("包含配置类文件")

        candidate = SaveDirCandidate(
            folder=folder,
            score=score,
            reason="；".join(reasons),
            source="变化检测",
            exists=folder.exists() and folder.is_dir(),
            changed_files=files,
        )
        candidates[_candidate_key(folder)] = candidate

    for candidate in _collect_changed_slot_parent_candidates(grouped):
        key = _candidate_key(candidate.folder)
        previous = candidates.get(key)
        if previous is None or candidate.score > previous.score:
            candidates[key] = candidate

    result = list(candidates.values())
    apply_game_keyword_boost(result, game_keywords or set(), boost=80)
    result.sort(key=lambda item: item.score, reverse=True)
    return result


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
