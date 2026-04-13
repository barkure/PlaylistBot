from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    bot_token: str
    default_playlist: str
    bootstrap_playlists: list[str]
    database_path: Path


def load_settings(path: str | Path = "config/settings.json") -> Settings:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    bot_token = str(os.getenv("TELEGRAM_BOT_TOKEN") or raw.get("bot_token", "")).strip()
    if not bot_token or bot_token in {"PASTE_YOUR_BOT_TOKEN_HERE", "<SECRET>"}:
        raise ValueError("Please set a real bot_token in config/settings.json")

    bootstrap_playlists = [
        str(value).strip()
        for value in raw.get("bootstrap_playlists", ["favorites", "playlist"])
        if str(value).strip()
    ]
    default_playlist = str(raw.get("default_playlist", "playlist")).strip()
    if default_playlist not in bootstrap_playlists:
        bootstrap_playlists.append(default_playlist)
    database_path = Path(raw.get("database_path", "data/playlistbot.sqlite3"))

    return Settings(
        bot_token=bot_token,
        default_playlist=default_playlist,
        bootstrap_playlists=bootstrap_playlists,
        database_path=database_path,
    )


def dump_example_config(path: str | Path = "config/settings.json") -> None:
    example = {
        "bot_token": "PASTE_YOUR_BOT_TOKEN_HERE",
        "default_playlist": "播放列表",
        "bootstrap_playlists": ["我喜欢", "播放列表"],
        "database_path": "data/playlistbot.sqlite3",
    }

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8"
    )
