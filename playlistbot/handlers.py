from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from playlistbot.config import Settings
from playlistbot.dedup import DedupStore
from playlistbot.pending import PendingSelection, PendingStore, TrackRef
from playlistbot.state import StateStore

logger = logging.getLogger(__name__)


def build_router(
    settings: Settings,
    dedup_store: DedupStore,
    state_store: StateStore,
    pending_store: PendingStore,
) -> Router:
    router = Router()

    def help_text() -> str:
        return (
            "PlaylistBot 使用说明\n\n"
            "群里：\n"
            "/bind - 绑定当前群聊\n\n"
            "私聊：\n"
            "/help - 查看帮助\n"
            "/createplaylist - 创建播放列表\n"
            "/manage - 管理播放列表\n"
            "/unbind - 解绑当前群聊\n\n"
            "日常使用：\n"
            "把音频转发给机器人，选择要保存到哪些播放列表即可。"
        )

    def get_callback_message(callback: CallbackQuery) -> Message | None:
        callback_message = callback.message
        if isinstance(callback_message, Message):
            return callback_message
        return None

    def build_manage_keyboard(
        state_default: str, state_threads: dict[str, int | None]
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        bound_names = sorted(
            name for name, thread_id in state_threads.items() if thread_id
        )
        unbound_names = sorted(
            name for name, thread_id in state_threads.items() if not thread_id
        )

        for name in bound_names:
            bound = state_threads.get(name)
            label = f"{'★ ' if name == state_default else ''}{name} [{'已绑定' if bound else '未绑定'}]"
            row = [
                InlineKeyboardButton(text=label, callback_data=f"noop:{name}"),
                InlineKeyboardButton(text="设为默认", callback_data=f"default:{name}"),
            ]
            row.append(
                InlineKeyboardButton(text="删除", callback_data=f"delete:{name}")
            )
            rows.append(row)

        if unbound_names:
            rows.append(
                [InlineKeyboardButton(text="待初始化", callback_data="noop:pending")]
            )
            for name in unbound_names:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"{name} [未绑定]", callback_data=f"noop:{name}"
                        ),
                        InlineKeyboardButton(
                            text="创建", callback_data=f"create:{name}"
                        ),
                    ]
                )
        rows.append([InlineKeyboardButton(text="刷新状态", callback_data="refresh")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def render_manage_text() -> str:
        state = await state_store.snapshot()
        lines = [
            "PlaylistBot 管理面板",
            f"chat_id: {state.source_chat_id}",
            f"default_playlist: {state.default_playlist}",
        ]
        bound_names = sorted(
            name for name, thread_id in state.playlist_threads.items() if thread_id
        )
        unbound_names = sorted(
            name for name, thread_id in state.playlist_threads.items() if not thread_id
        )
        if bound_names:
            lines.append("可用歌单:")
            for name in bound_names:
                lines.append(f"{name}: {state.playlist_threads.get(name)}")
        if unbound_names:
            lines.append("待初始化:")
            for name in unbound_names:
                lines.append(f"{name}: 未绑定")
        return "\n".join(lines)

    def build_picker_keyboard(
        prompt_message_id: int,
        state_default: str,
        state_threads: dict[str, int | None],
        selected_playlists: set[str],
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for name in sorted(state_threads):
            selected = name in selected_playlists
            text = f"{'✅' if selected else '⬜'} {name}"
            if name == state_default:
                text += " ★"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=text,
                        callback_data=f"pick:{prompt_message_id}:{name}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="保存", callback_data=f"save:{prompt_message_id}"
                ),
                InlineKeyboardButton(
                    text="取消", callback_data=f"cancel:{prompt_message_id}"
                ),
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def build_saved_track_keyboard(
        playlist: str, file_unique_id: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="从歌单移除",
                        callback_data=f"remove:{playlist}:{file_unique_id}",
                    )
                ]
            ]
        )

    def build_unbind_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="确认解绑", callback_data="unbind:confirm"),
                    InlineKeyboardButton(text="取消", callback_data="unbind:cancel"),
                ]
            ]
        )

    def render_picker_text(track: TrackRef, selected_playlists: set[str]) -> str:
        title = track.title or "Unknown Title"
        performer = track.performer or "Unknown Performer"
        chosen = ", ".join(sorted(selected_playlists)) or "未选择"
        return f"选择要保存到的歌单\n标题: {title}\n歌手: {performer}\n已选: {chosen}"

    def extract_track(message: Message) -> TrackRef | None:
        audio = message.audio
        if audio is None:
            return None

        title = audio.title
        performer = audio.performer
        body = "\n".join(
            part for part in [message.text, message.caption] if part
        ).strip()

        if not title or not performer:
            parsed_title, parsed_performer = parse_track_info(body)
            title = title or parsed_title
            performer = performer or parsed_performer

        title = title or "Unknown Title"
        performer = performer or "Unknown Performer"

        return TrackRef(
            file_id=audio.file_id,
            file_unique_id=audio.file_unique_id,
            title=title,
            performer=performer,
            duration=audio.duration,
            tags=build_tags(title, performer),
        )

    def parse_track_info(body: str) -> tuple[str | None, str | None]:
        if not body:
            return None, None

        lines = [line.strip() for line in body.splitlines() if line.strip()]
        for line in lines:
            if " - " in line:
                left, right = line.split(" - ", maxsplit=1)
                if left and right:
                    return left.strip(), right.strip()
            if "·" in line:
                left, right = line.split("·", maxsplit=1)
                if left.strip() and right.strip():
                    return left.strip(), right.strip()

        return None, None

    def build_tags(title: str, performer: str) -> list[str]:
        performer_tags = [make_tag(name) for name in split_performers(performer)]
        performer_tags = [tag for tag in performer_tags if tag != "#unknown"]
        title_tag = make_tag(title)
        tags = performer_tags + ([title_tag] if title_tag != "#unknown" else [])
        deduped_tags: list[str] = []
        for tag in tags:
            if tag not in deduped_tags:
                deduped_tags.append(tag)
        return deduped_tags or ["#unknown"]

    def split_performers(performer: str) -> list[str]:
        parts = re.split(r"\s*(?:/|&|,|、|，| feat\. | ft\. | x )\s*", performer, flags=re.IGNORECASE)
        cleaned = [part.strip() for part in parts if part.strip()]
        return cleaned or [performer]

    def make_tag(value: str) -> str:
        normalized = re.sub(r"\s+", "_", value.strip())
        normalized = re.sub(r"[^\w\u4e00-\u9fff_]+", "", normalized)
        return f"#{normalized}" if normalized else "#unknown"

    @router.message(CommandStart())
    async def start_handler(message: Message) -> None:
        await message.answer(help_text())

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await message.answer(help_text())

    @router.message(Command("bind"))
    async def bind_handler(message: Message) -> None:
        bot = message.bot
        if bot is None:
            return

        if message.chat.type != "supergroup":
            await message.answer("请在开启 Topics 的超级群里使用 /bind。")
            return

        state = await state_store.bind_chat(message.chat.id)
        created_playlists: list[str] = []
        skipped_playlists: list[str] = []
        missing_permission = False

        for playlist in settings.bootstrap_playlists:
            if state.playlist_threads.get(playlist):
                skipped_playlists.append(playlist)
                continue

            try:
                topic = await bot.create_forum_topic(
                    chat_id=message.chat.id,
                    name=playlist,
                )
            except TelegramBadRequest as exc:
                if "not enough rights" in str(exc).lower():
                    missing_permission = True
                    break
                raise

            state = await state_store.bind_playlist_thread(
                playlist, topic.message_thread_id
            )
            created_playlists.append(playlist)

        lines = [
            "已绑定当前群。",
            f"chat_id: {state.source_chat_id}",
        ]
        if created_playlists:
            lines.append("已自动创建歌单话题: " + "、".join(created_playlists))
        if skipped_playlists:
            lines.append("已存在，跳过创建: " + "、".join(skipped_playlists))
        if missing_permission:
            lines.append(
                "未能自动创建话题：请把机器人设为管理员，并开启 Manage Topics。"
            )
        lines.append("后续在私聊里用 /manage 管理歌单，转发音频给机器人即可。")
        await message.answer("\n".join(lines))

    @router.message(Command("unbind"))
    async def unbind_handler(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("请在和机器人的私聊里使用 /unbind。")
            return

        state = await state_store.snapshot()
        if state.source_chat_id is None:
            await message.answer("当前还没有绑定任何音乐群。")
            return

        await message.answer(
            "确认要解绑当前音乐群吗？\n"
            "解绑后会清空歌单话题绑定，后续需要重新 /bind。",
            reply_markup=build_unbind_keyboard(),
        )

    @router.message(Command("createplaylist"))
    async def createplaylist_handler(message: Message, command: CommandObject) -> None:
        bot = message.bot
        if bot is None:
            return

        if message.chat.type != "private":
            await message.answer("请在和机器人的私聊里使用 /createplaylist。")
            return

        args = (command.args or "").strip()
        if not args:
            await message.answer("用法: /createplaylist 歌单名")
            return

        playlist = args
        state = await state_store.snapshot()
        if state.source_chat_id is None:
            await message.answer("请先在音乐群里执行 /bind。")
            return

        topic_name = playlist
        try:
            topic = await bot.create_forum_topic(
                chat_id=state.source_chat_id, name=topic_name
            )
        except TelegramBadRequest as exc:
            if "not enough rights" in str(exc).lower():
                await message.answer(
                    "当前没有创建话题权限。请把机器人设为管理员，并开启 Manage Topics。"
                )
                return
            raise

        state = await state_store.bind_playlist_thread(
            playlist, topic.message_thread_id
        )
        await message.answer(
            "歌单话题已创建并绑定。\n"
            f"playlist: {playlist}\n"
            f"thread_id: {state.playlist_threads[playlist]}"
        )

    @router.message(Command("manage"))
    async def manage_handler(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("请在和机器人的私聊里使用 /manage。")
            return

        state = await state_store.snapshot()
        await message.answer(
            await render_manage_text(),
            reply_markup=build_manage_keyboard(
                state.default_playlist, state.playlist_threads
            ),
        )

    @router.callback_query(F.data == "refresh")
    async def refresh_callback(callback: CallbackQuery) -> None:
        state = await state_store.snapshot()
        callback_message = get_callback_message(callback)
        if callback_message:
            await callback_message.edit_text(
                await render_manage_text(),
                reply_markup=build_manage_keyboard(
                    state.default_playlist, state.playlist_threads
                ),
            )
        await callback.answer("已刷新")

    @router.callback_query(F.data == "unbind:confirm")
    async def unbind_confirm_callback(callback: CallbackQuery) -> None:
        callback_message = get_callback_message(callback)
        state = await state_store.snapshot()
        if state.source_chat_id is None:
            await callback.answer("当前没有已绑定的音乐群。", show_alert=True)
            if callback_message:
                await callback_message.edit_text("当前没有已绑定的音乐群。")
            return

        await state_store.unbind_chat()
        if callback_message:
            await callback_message.edit_text(
                "已解绑当前音乐群，并清空歌单话题绑定。\n"
                "如果后续还要继续使用，请重新在群里执行 /bind。"
            )
        await callback.answer("已解绑")

    @router.callback_query(F.data == "unbind:cancel")
    async def unbind_cancel_callback(callback: CallbackQuery) -> None:
        callback_message = get_callback_message(callback)
        if callback_message:
            await callback_message.edit_text("已取消解绑。")
        await callback.answer("已取消")

    @router.callback_query(F.data.startswith("noop:"))
    async def noop_callback(callback: CallbackQuery) -> None:
        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        playlist = data.split(":", maxsplit=1)[1]
        if playlist == "pending":
            await callback.answer("下面是还没创建完成的歌单。", show_alert=True)
            return
        state = await state_store.snapshot()
        thread_id = state.playlist_threads.get(playlist)
        text = (
            f"{playlist} 当前未绑定话题。"
            if thread_id is None
            else f"{playlist} 当前绑定话题 {thread_id}。"
        )
        await callback.answer(text, show_alert=True)

    @router.callback_query(F.data.startswith("default:"))
    async def default_callback(callback: CallbackQuery) -> None:
        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        playlist = data.split(":", maxsplit=1)[1]
        state = await state_store.set_default_playlist(playlist)
        callback_message = get_callback_message(callback)
        if callback_message:
            await callback_message.edit_text(
                await render_manage_text(),
                reply_markup=build_manage_keyboard(
                    state.default_playlist, state.playlist_threads
                ),
            )
        await callback.answer(f"默认歌单已切换到 {playlist}")

    @router.callback_query(F.data.startswith("create:"))
    async def create_callback(callback: CallbackQuery) -> None:
        bot = callback.bot
        if bot is None:
            await callback.answer("机器人上下文不可用", show_alert=True)
            return

        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        playlist = data.split(":", maxsplit=1)[1]
        state = await state_store.snapshot()
        callback_message = get_callback_message(callback)
        chat_id = state.source_chat_id or (
            callback_message.chat.id if callback_message else None
        )
        if chat_id is None:
            await callback.answer("请先执行 /bind", show_alert=True)
            return

        topic_name = playlist
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
        except TelegramBadRequest as exc:
            if "not enough rights" in str(exc).lower():
                await callback.answer("缺少 Manage Topics 权限", show_alert=True)
                return
            raise

        state = await state_store.bind_playlist_thread(
            playlist, topic.message_thread_id
        )
        if callback_message:
            await callback_message.edit_text(
                await render_manage_text(),
                reply_markup=build_manage_keyboard(
                    state.default_playlist, state.playlist_threads
                ),
            )
        await callback.answer(f"已创建 {playlist}")

    @router.callback_query(F.data.startswith("delete:"))
    async def delete_callback(callback: CallbackQuery) -> None:
        bot = callback.bot
        if bot is None:
            await callback.answer("机器人上下文不可用", show_alert=True)
            return

        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        playlist = data.split(":", maxsplit=1)[1]
        state = await state_store.snapshot()
        callback_message = get_callback_message(callback)
        chat_id = state.target_chat_id or (
            callback_message.chat.id if callback_message else None
        )
        thread_id = state.playlist_threads.get(playlist)
        if chat_id is None:
            await callback.answer("请先执行 /bind", show_alert=True)
            return
        if thread_id is None:
            await callback.answer("这个歌单还没有绑定话题", show_alert=True)
            return
        if playlist == state.default_playlist:
            await callback.answer(
                "不能删除当前默认歌单，请先切换默认歌单", show_alert=True
            )
            return

        try:
            await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "not enough rights" in message:
                await callback.answer("缺少 Manage Topics 权限", show_alert=True)
                return
            if "message thread not found" in message:
                await state_store.unbind_playlist_thread(playlist)
                await callback.answer("话题已不存在，已清理绑定", show_alert=True)
                return
            raise

        state = await state_store.unbind_playlist_thread(playlist)
        if callback_message:
            await callback_message.edit_text(
                await render_manage_text(),
                reply_markup=build_manage_keyboard(
                    state.default_playlist, state.playlist_threads
                ),
            )
        await callback.answer(f"已删除 {playlist}")

    @router.callback_query(F.data.startswith("pick:"))
    async def pick_callback(callback: CallbackQuery) -> None:
        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        _, prompt_message_id_raw, playlist = data.split(":", maxsplit=2)
        prompt_message_id = int(prompt_message_id_raw)
        selection = await pending_store.toggle_playlist(prompt_message_id, playlist)
        if selection is None:
            await callback.answer("这个待保存项已失效", show_alert=True)
            return

        state = await state_store.snapshot()
        callback_message = get_callback_message(callback)
        if callback_message:
            await callback_message.edit_text(
                render_picker_text(selection.track, selection.selected_playlists),
                reply_markup=build_picker_keyboard(
                    prompt_message_id,
                    state.default_playlist,
                    state.playlist_threads,
                    selection.selected_playlists,
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("save:"))
    async def save_callback(callback: CallbackQuery) -> None:
        bot = callback.bot
        if bot is None:
            await callback.answer("机器人上下文不可用", show_alert=True)
            return

        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        prompt_message_id = int(data.split(":", maxsplit=1)[1])
        selection = await pending_store.pop(prompt_message_id)
        if selection is None:
            await callback.answer("这个待保存项已失效", show_alert=True)
            return

        if not selection.selected_playlists:
            await pending_store.put(prompt_message_id, selection)
            await callback.answer("至少选择一个歌单", show_alert=True)
            return

        state = await state_store.snapshot()
        target_chat_id = state.target_chat_id
        if target_chat_id is None:
            await callback.answer("目标群未绑定", show_alert=True)
            return
        saved_to: list[str] = []
        skipped: list[str] = []
        for playlist in sorted(selection.selected_playlists):
            thread_id = state.playlist_threads.get(playlist)
            if thread_id is None:
                skipped.append(f"{playlist}(未绑定)")
                continue
            if await dedup_store.is_duplicate(selection.track, playlist):
                skipped.append(f"{playlist}(重复)")
                continue

            await bot.send_audio(
                chat_id=target_chat_id,
                audio=selection.track.file_id,
                message_thread_id=thread_id,
                title=selection.track.title,
                performer=selection.track.performer,
                duration=selection.track.duration,
                caption=(
                    f"歌单: {playlist}\n"
                    f"标题: {selection.track.title or 'Unknown Title'}\n"
                    f"歌手: {selection.track.performer or 'Unknown Performer'}\n"
                    f"{' '.join(selection.track.tags)}"
                ),
                reply_markup=build_saved_track_keyboard(
                    playlist,
                    selection.track.file_unique_id,
                ),
            )
            await dedup_store.remember(selection.track, playlist)
            saved_to.append(playlist)

        callback_message = get_callback_message(callback)
        if callback_message:
            lines = ["保存完成"]
            lines.append("已保存: " + (", ".join(saved_to) if saved_to else "无"))
            if skipped:
                lines.append("已跳过: " + ", ".join(skipped))
            await callback_message.edit_text("\n".join(lines))
        await callback.answer("已处理")

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel_callback(callback: CallbackQuery) -> None:
        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        prompt_message_id = int(data.split(":", maxsplit=1)[1])
        await pending_store.pop(prompt_message_id)
        callback_message = get_callback_message(callback)
        if callback_message:
            await callback_message.edit_text("已取消保存")
        await callback.answer("已取消")

    @router.callback_query(F.data.startswith("remove:"))
    async def remove_callback(callback: CallbackQuery) -> None:
        data = callback.data
        if not data:
            await callback.answer("缺少回调数据", show_alert=True)
            return
        _, playlist, file_unique_id = data.split(":", maxsplit=2)
        callback_message = get_callback_message(callback)
        if callback_message is None:
            await callback.answer("缺少消息上下文", show_alert=True)
            return

        try:
            await callback_message.delete()
        except TelegramBadRequest:
            await callback.answer(
                "删除失败，请确认机器人有删除消息权限", show_alert=True
            )
            return

        await dedup_store.forget(file_unique_id, playlist)
        await callback.answer(f"已从 {playlist} 移除")

    @router.message(F.audio)
    async def audio_handler(message: Message) -> None:
        bot = message.bot
        if bot is None:
            return

        state = await state_store.snapshot()

        if state.source_chat_id is None or state.target_chat_id is None:
            return

        if message.chat.type != "private":
            return

        if message.from_user and message.from_user.id == bot.id:
            return

        if not state.playlist_threads:
            logger.warning("No playlists available for selection")
            return

        track = extract_track(message)
        if track is None:
            return
        initial_selection = (
            {state.default_playlist}
            if state.default_playlist in state.playlist_threads
            else set()
        )
        logger.info(
            "Received audio: title=%s performer=%s from_chat=%s",
            track.title or "Unknown Title",
            track.performer or "Unknown Performer",
            message.chat.id,
        )
        prompt = await message.reply(
            render_picker_text(track, initial_selection),
            reply_markup=build_picker_keyboard(
                0,
                state.default_playlist,
                state.playlist_threads,
                initial_selection,
            ),
        )
        await pending_store.put(
            prompt.message_id,
            PendingSelection(
                track=track,
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
                selected_playlists=initial_selection,
            ),
        )
        await prompt.edit_reply_markup(
            reply_markup=build_picker_keyboard(
                prompt.message_id,
                state.default_playlist,
                state.playlist_threads,
                initial_selection,
            )
        )

    @router.message()
    async def debug_unhandled_message(message: Message) -> None:
        logger.info(
            "Unhandled message: chat_id=%s message_id=%s content_type=%s from_user_id=%s is_bot=%s has_audio=%s has_document=%s has_caption=%s",
            message.chat.id,
            message.message_id,
            message.content_type,
            message.from_user.id if message.from_user else None,
            message.from_user.is_bot if message.from_user else None,
            message.audio is not None,
            message.document is not None,
            bool(message.caption),
        )

    return router
