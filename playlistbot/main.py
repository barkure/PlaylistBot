from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from playlistbot.config import load_settings
from playlistbot.dedup import DedupStore
from playlistbot.handlers import build_router
from playlistbot.pending import PendingStore
from playlistbot.state import StateStore


async def run() -> None:
    settings = load_settings()
    dedup_store = DedupStore(settings.database_path)
    pending_store = PendingStore()
    state_store = StateStore(settings.database_path)
    await dedup_store.load()
    await state_store.load()
    await state_store.ensure_bootstrap_playlists(
        settings.bootstrap_playlists,
        settings.default_playlist,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    proxy = (
        os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY")
    )
    session = AiohttpSession(proxy=proxy)
    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(settings, dedup_store, state_store, pending_store)
    )

    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
