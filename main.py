import runpy
import sys
from pathlib import Path


def _launcher_game_id(args: list[str]) -> str | None:
    for index, arg in enumerate(args):
        if arg == "--launch-game-id" and index + 1 < len(args):
            return args[index + 1].strip() or None
        if arg.startswith("--launch-game-id="):
            return arg.split("=", 1)[1].strip() or None
    return None


def _run_bound_game_launcher(game_id: str) -> None:
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    launcher_script = bundle_dir / "game_cloud_save_launcher.pyw"
    if not launcher_script.is_file():
        raise FileNotFoundError(f"主程序内未找到启动器逻辑：{launcher_script}")
    sys.argv = [str(launcher_script), "--game-id", game_id]
    runpy.run_path(str(launcher_script), run_name="__main__")


def main() -> None:
    game_id = _launcher_game_id(sys.argv[1:])
    if game_id:
        _run_bound_game_launcher(game_id)
        return

    from app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
