# PlaylistBot

一个用于在 Telegram 中整理和管理个人音乐播放列表的机器人。

- 先用 [@Music163bot](https://t.me/Music163bot) 这类机器人获取歌曲
- 再把歌曲转发给 PlaylistBot，它会让你选择保存到哪个播放列表
- 然后 PlaylistBot 会把这首歌发送到音乐群对应的话题里，并带上歌名和歌手标签，方便搜索

https://github.com/user-attachments/assets/dcf7b4f8-f57d-4563-b98b-e057638247de

## How to Use

### 配置

`config/settings.json`

```json
{
  "bot_token": "PASTE_YOUR_BOT_TOKEN_HERE",
  "default_playlist": "播放列表",
  "bootstrap_playlists": ["我喜欢", "播放列表"],
  "database_path": "data/playlistbot.sqlite3"
}
```

- `bot_token`: 建议用环境变量 `TELEGRAM_BOT_TOKEN` 注入
- `default_playlist`: 默认预选歌单
- `bootstrap_playlists`: 首次初始化时自动创建的歌单
- `database_path`: SQLite 数据库文件

### 运行

```bash
uv sync
TELEGRAM_BOT_TOKEN="你的_bot_token" uv run playlistbot
```

### 使用

1. 把机器人加入开启 `Topics` 的超级群
2. 给予机器人包括 `Manage Topics` 的管理员权限
3. 在群里发送 `/bind`：

- `/bind` 会绑定当前群，并自动创建默认歌单话题：
    - `我喜欢`
    - `播放列表`

4. 之后都在机器人私聊里操作：

- 转发音频给机器人
- 用 `/help` 查看使用说明
- 选择要保存到哪些播放列表
- 用 `/createplaylist 歌单名` 创建新歌单
- 用 `/manage` 管理歌单
- 用 `/unbind` 解绑当前音乐群（会二次确认）

### Commands

如果你希望在 Telegram 输入 `/` 时看到命令提示，可以在 [@BotFather](https://t.me/BotFather) 里配置 `Commands`：

```text
help - 查看帮助
bind - 绑定当前群聊
createplaylist - 创建播放列表
manage - 管理播放列表
unbind - 解绑当前群聊
```

### 说明

- 单群工具（自用），不做多群隔离
- 状态和去重记录保存在 `SQLite`，删除 `data/playlistbot.sqlite3` 后，下次启动会重新初始化
