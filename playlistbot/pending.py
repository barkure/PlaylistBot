from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from aiogram.types import Audio


@dataclass(slots=True)
class TrackRef:
    file_id: str
    file_unique_id: str
    title: str | None
    performer: str | None
    duration: int | None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_audio(cls, audio: Audio) -> "TrackRef":
        return cls(
            file_id=audio.file_id,
            file_unique_id=audio.file_unique_id,
            title=audio.title,
            performer=audio.performer,
            duration=audio.duration,
        )


@dataclass(slots=True)
class PendingSelection:
    track: TrackRef
    source_chat_id: int
    source_message_id: int
    selected_playlists: set[str] = field(default_factory=set)


class PendingStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._items: dict[int, PendingSelection] = {}

    async def put(self, prompt_message_id: int, selection: PendingSelection) -> None:
        async with self._lock:
            self._items[prompt_message_id] = selection

    async def get(self, prompt_message_id: int) -> PendingSelection | None:
        async with self._lock:
            return self._items.get(prompt_message_id)

    async def toggle_playlist(
        self, prompt_message_id: int, playlist: str
    ) -> PendingSelection | None:
        async with self._lock:
            item = self._items.get(prompt_message_id)
            if item is None:
                return None
            if playlist in item.selected_playlists:
                item.selected_playlists.remove(playlist)
            else:
                item.selected_playlists.add(playlist)
            return item

    async def pop(self, prompt_message_id: int) -> PendingSelection | None:
        async with self._lock:
            return self._items.pop(prompt_message_id, None)
