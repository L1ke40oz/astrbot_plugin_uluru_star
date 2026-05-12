# 乌鲁鲁星 - 后端 API 文档

Base URL: `http://{host}:{port}` (默认 `http://localhost:1016`)

## 页面路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 主页 HTML |
| GET | `/style.css` | 样式表 |
| GET | `/app.js` | 前端脚本 |
| GET | `/sw.js` | Service Worker |
| GET | `/static/{filename}` | 静态资源（templates 目录） |
| GET | `/assets/{filename}` | 用户资源（plugin_data/assets 目录） |

## 配置 API

### GET /api/config/frontend
返回前端需要的配置值。

**响应：**
```json
{
  "success": true,
  "message_separator": "\\$"
}
```

## Profile API

### GET /api/profile
获取持久化的用户数据。

**响应：**
```json
{
  "success": true,
  "profile": {
    "user_nickname": "你",
    "bot_nickname": "沈星回",
    "user_avatar": "data:image/...",
    "bot_avatar": "data:image/...",
    "theme": "blue",
    "particles": {"enabled": true, "shape": "heart", "color": "theme"},
    "covers": {"book_id": "data:image/..."},
    "note_box": [{"content": "...", "time": 1778000000}]
  }
}
```

### POST /api/profile
保存用户数据（合并更新）。

**请求体：** 任意 key-value，会合并到现有 profile 中。

## Session API

### POST /api/session/start
开始或恢复书籍会话。

**请求体：**
```json
{"book_id": "abc123", "book_title": "书名"}
```

### POST /api/session/heartbeat
心跳保活。

**请求体：** `{"book_id": "abc123"}`

### POST /api/session/end
结束会话。

**请求体：** `{"book_id": "abc123"}`

## 书籍 API

### GET /api/books
获取书架列表。

**响应：**
```json
{
  "success": true,
  "books": [
    {"id": "abc123", "title": "书名", "author": "作者", "chapter_count": 30, "added_at": 1778000000}
  ]
}
```

### POST /api/books/upload
上传 epub 或 txt 文件。

**请求：** multipart/form-data, field: `file`

### GET /api/books/{book_id}/chapters
获取章节目录。

**响应：**
```json
{
  "success": true,
  "chapters": [
    {"index": 0, "title": "第一章", "text_preview": "..."}
  ]
}
```

### GET /api/books/{book_id}/chapters/{chapter_index}
获取章节 HTML 内容。

**响应：** `{"success": true, "content": "<p>...</p>"}`

### DELETE /api/books/{book_id}
删除书籍。

## 交互 API

### POST /api/interact/highlight
记录划线（存入 library + 对话历史）。

**请求体：**
```json
{
  "session_token": "local",
  "book_id": "abc123",
  "chapter_index": 5,
  "text": "划线的文字",
  "context": "上下文"
}
```

### POST /api/interact/review
提交书评（触发 Bot 回复）。

### POST /api/interact/note
提交纸条（触发 Bot 回复）。

## 聊天 API

### GET /api/chat/history?book_id={book_id}
获取某本书的聊天记录。

**响应：**
```json
{
  "success": true,
  "messages": [
    {"id": "abc", "role": "user", "content": "...", "timestamp": 1778000000, "metadata": {}}
  ]
}
```

### POST /api/chat/send
发送聊天消息。

**请求体：**
```json
{
  "book_id": "abc123",
  "content": "消息内容",
  "chapter_index": 5,
  "scroll_percent": 45,
  "bookmark_percent": 20
}
```

**响应：** `{"success": true, "reply": "Bot 的回复"}`

**滑动窗口逻辑：**
- `scroll_percent`: 用户当前滚动位置（0-100）
- `bookmark_percent`: 书签位置（可选）
- 短章节全量注入，长章节截取窗口（最大 3000 字）

## 打卡 API

### POST /api/chapter/complete
章节打卡，触发 Bot 生成章节记忆。

**请求体：**
```json
{"book_id": "abc123", "chapter_index": 5}
```

**响应：** `{"success": true, "message": "打卡成功"}`

**副作用：** 后台异步调用 LLM 生成章节摘要，存入 `bot_chapter_memories.json`。

## 进度 API

### GET /api/bot-progress/{book_id}
获取 Bot 阅读进度百分比。

### POST /api/user-progress/report
上报用户阅读进度。

**请求体：**
```json
{"book_id": "abc123", "chapter_index": 5, "total_chapters": 30, "book_title": "书名"}
```

### GET /api/user-progress/{book_id}
获取用户阅读进度百分比。

## 统计 API

### GET /api/stats
获取阅读统计数据。

**响应：**
```json
{
  "success": true,
  "stats": {
    "total_books": 4,
    "total_chapters": 100,
    "user_chapters_read": 15,
    "bot_chapters_read": 20,
    "reading_days": 7,
    "highlights_count": 12,
    "total_messages": 45
  }
}
```

## 记忆管理 API

### GET /api/memory/sessions
列出所有书籍会话。

### GET /api/memory/sessions/{book_id}
获取某本书的完整会话详情。

### DELETE /api/memory/sessions/{book_id}
删除某本书的会话。

### DELETE /api/memory/sessions/{book_id}/messages/{message_id}
删除单条消息。

## Legacy API（兼容旧版）

### GET /api/memory/archives
### GET /api/memory/archives/{session_id}
### DELETE /api/memory/archives/{session_id}
### DELETE /api/memory/archives/{session_id}/messages/{message_id}
### GET /api/memory/active
### DELETE /api/memory/active/{session_id}/messages/{message_id}

## 数据查询 API

### GET /api/data/{book_id}/highlights
### GET /api/data/{book_id}/reviews
### GET /api/data/{book_id}/notes
