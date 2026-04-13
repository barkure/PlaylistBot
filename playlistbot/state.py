from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RuntimeState:
    source_chat_id: int | None = None
    target_chat_id: int | None = None
    default_playlist: str = "playlist"
    playlist_threads: dict[str, int | None] = field(default_factory=dict)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._state = RuntimeState()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS playlists (
                name TEXT PRIMARY KEY,
                thread_id INTEGER
            );
            """
        )
        connection.commit()

    async def load(self) -> None:
        async with self._lock:
            with self._connect() as connection:
                self._initialize(connection)
                state_rows = {
                    row["key"]: row["value"]
                    for row in connection.execute("SELECT key, value FROM bot_state")
                }
                playlist_rows = {
                    str(row["name"]): (
                        int(row["thread_id"]) if row["thread_id"] is not None else None
                    )
                    for row in connection.execute(
                        "SELECT name, thread_id FROM playlists"
                    )
                }
            self._state = RuntimeState(
                source_chat_id=int(state_rows["source_chat_id"])
                if state_rows.get("source_chat_id")
                else None,
                target_chat_id=int(state_rows["target_chat_id"])
                if state_rows.get("target_chat_id")
                else None,
                default_playlist=state_rows.get("default_playlist", "playlist"),
                playlist_threads=playlist_rows,
            )

    async def ensure_bootstrap_playlists(
        self, playlist_names: list[str], default_playlist: str
    ) -> RuntimeState:
        async with self._lock:
            self._state.default_playlist = default_playlist
            for name in playlist_names:
                if name not in self._state.playlist_threads:
                    self._state.playlist_threads[name] = None
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def bind_chat(self, chat_id: int) -> RuntimeState:
        async with self._lock:
            self._state.source_chat_id = chat_id
            self._state.target_chat_id = chat_id
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def unbind_chat(self) -> RuntimeState:
        async with self._lock:
            self._state.source_chat_id = None
            self._state.target_chat_id = None
            for name in list(self._state.playlist_threads):
                self._state.playlist_threads[name] = None
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def bind_playlist_thread(self, playlist: str, thread_id: int) -> RuntimeState:
        async with self._lock:
            self._state.playlist_threads[playlist] = thread_id
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def unbind_playlist_thread(self, playlist: str) -> RuntimeState:
        async with self._lock:
            self._state.playlist_threads.pop(playlist, None)
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def set_default_playlist(self, playlist: str) -> RuntimeState:
        async with self._lock:
            self._state.default_playlist = playlist
            await self._save_unlocked()
            return self.snapshot_unlocked()

    async def snapshot(self) -> RuntimeState:
        async with self._lock:
            return self.snapshot_unlocked()

    async def _save_unlocked(self) -> None:
        with self._connect() as connection:
            self._initialize(connection)
            state_items = {
                "source_chat_id": ""
                if self._state.source_chat_id is None
                else str(self._state.source_chat_id),
                "target_chat_id": ""
                if self._state.target_chat_id is None
                else str(self._state.target_chat_id),
                "default_playlist": self._state.default_playlist,
            }
            connection.executemany(
                "REPLACE INTO bot_state(key, value) VALUES (?, ?)",
                list(state_items.items()),
            )
            connection.execute("DELETE FROM playlists")
            connection.executemany(
                "INSERT INTO playlists(name, thread_id) VALUES (?, ?)",
                [
                    (name, thread_id)
                    for name, thread_id in self._state.playlist_threads.items()
                ],
            )
            connection.commit()

    def snapshot_unlocked(self) -> RuntimeState:
        return RuntimeState(
            source_chat_id=self._state.source_chat_id,
            target_chat_id=self._state.target_chat_id,
            default_playlist=self._state.default_playlist,
            playlist_threads=dict(self._state.playlist_threads),
        )
