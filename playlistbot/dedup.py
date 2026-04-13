from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from playlistbot.pending import TrackRef


class DedupStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._seen: set[str] = set()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_signatures (
                signature TEXT PRIMARY KEY
            )
            """
        )
        connection.commit()

    async def load(self) -> None:
        async with self._lock:
            with self._connect() as connection:
                self._initialize(connection)
                self._seen = {
                    str(row["signature"])
                    for row in connection.execute(
                        "SELECT signature FROM dedup_signatures"
                    )
                }

    async def is_duplicate(self, track: TrackRef, playlist: str) -> bool:
        async with self._lock:
            return any(
                signature in self._seen
                for signature in build_signatures(track, playlist)
            )

    async def remember(self, track: TrackRef, playlist: str) -> None:
        async with self._lock:
            self._seen.update(build_signatures(track, playlist))
            await self._save_unlocked()

    async def forget(self, file_unique_id: str, playlist: str) -> None:
        async with self._lock:
            prefix = f"file:{playlist}:{file_unique_id}"
            self._seen = {item for item in self._seen if item != prefix}
            await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        with self._connect() as connection:
            self._initialize(connection)
            connection.execute("DELETE FROM dedup_signatures")
            connection.executemany(
                "INSERT INTO dedup_signatures(signature) VALUES (?)",
                [(signature,) for signature in sorted(self._seen)],
            )
            connection.commit()


def build_signatures(track: TrackRef, playlist: str) -> set[str]:
    return {f"file:{playlist}:{track.file_unique_id}"}
