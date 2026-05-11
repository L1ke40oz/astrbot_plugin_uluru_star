# 乌鲁鲁星 - 后端 API 文档

本文档列出所有可用的后端 API 接口，供前端开发参考。

基础地址: `http://{host}:{port}` (默认 `http://localhost:1016`)

所有 API 返回 JSON 格式，成功时包含 `"success": true`。

---

## 页面路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 主页面 (index.html) |
| GET | `/style.css` | 样式文件 |
| GET | `/app.js` | 前端脚本 |

静态文件加载优先级: `custom_templates/` > `templates/`

---

## 会话管理 (Session)

### POST `/api/session/start`

开始或恢复一本书的阅读会话。

**请求体:**
```json
{
  "book_id": "abc1234567",
  "book_title": "话桑麻"
}
```

- `book_id` 为空时仅确认连接，不创建会话。

**响应:**
```json
{
  "success": true,
  "session_active": true,
  "book_id": "abc1234567",
  "resumed": false,
  "message_count": 0
}
```

### POST `/api/session/heartbeat`

保持会话活跃。

**请求体:**
```json
{ "book_id": "abc1234567" }
```

**响应:**
```json
{ "success": true }
```

### POST `/api/session/end`

结束当前书的会话（空会话自动删除）。

**请求体:**
```json
{ "book_id": "abc1234567" }
```

**响应:**
```json
{ "success": true }
```

---

## 书籍管理 (Books)

### GET `/api/books`

获取书架上所有书籍列表。

**响应:**
```json
{
  "success": true,
  "books": [
    {
      "id": "abc1234567",
      "title": "话桑麻",
      "author": "作者名",
      "chapter_count": 42,
      "added_at": 1700000000.0
    }
  ]
}
```

### POST `/api/books/upload`

上传 epub 文件。

**请求:** `multipart/form-data`，字段名 `file`，仅接受 `.epub` 文件。

**响应:**
```json
{
  "success": true,
  "book": {
    "id": "abc1234567",
    "title": "话桑麻",
    "author": "作者名",
    "chapter_count": 42,
    "added_at": 1700000000.0
  }
}
```

### GET `/api/books/{book_id}/chapters`

获取某本书的章节列表（不含正文）。

**响应:**
```json
{
  "success": true,
  "chapters": [
    { "index": 0, "title": "第一章 初见", "text_preview": "..." },
    { "index": 1, "title": "第二章 重逢", "text_preview": "..." }
  ]
}
```

### GET `/api/books/{book_id}/chapters/{chapter_index}`

获取某章的 HTML 正文内容。

**响应:**
```json
{
  "success": true,
  "content": "<p>正文 HTML 内容...</p>"
}
```

### DELETE `/api/books/{book_id}`

删除一本书及其所有关联数据（划线、书评、笔记）。

**响应:**
```json
{ "success": true }
```

---

## 互动 (Interact)

以下接口会保存用户操作并触发 Bot 回复。

### POST `/api/interact/highlight`

划线并获取 Bot 回复。

**请求体:**
```json
{
  "session_token": "abc1234567",
  "book_id": "abc1234567",
  "chapter_index": 3,
  "text": "划线的文字内容",
  "context": "上下文文字（可选）"
}
```

**响应:**
```json
{
  "success": true,
  "highlight": {
    "id": "a1b2c3d4",
    "chapter_index": 3,
    "text": "划线的文字内容",
    "context": "...",
    "created_at": 1700000000.0
  },
  "bot_reply": "Bot 的回复文字"
}
```

### POST `/api/interact/review`

写书评并获取 Bot 回复。

**请求体:**
```json
{
  "session_token": "abc1234567",
  "book_id": "abc1234567",
  "chapter_index": 3,
  "content": "书评内容"
}
```

**响应:**
```json
{
  "success": true,
  "review": {
    "id": "a1b2c3d4",
    "chapter_index": 3,
    "content": "书评内容",
    "created_at": 1700000000.0
  },
  "bot_reply": "Bot 的回复文字"
}
```

### POST `/api/interact/note`

留纸条并获取 Bot 回复。

**请求体:**
```json
{
  "session_token": "abc1234567",
  "book_id": "abc1234567",
  "chapter_index": 3,
  "content": "纸条内容"
}
```

**响应:**
```json
{
  "success": true,
  "note": {
    "id": "a1b2c3d4",
    "chapter_index": 3,
    "content": "纸条内容",
    "created_at": 1700000000.0
  },
  "bot_reply": "Bot 的回复文字"
}
```

---

## 聊天 (Chat)

### GET `/api/chat/history?book_id={book_id}`

获取某本书的聊天记录。

**响应:**
```json
{
  "success": true,
  "messages": [
    {
      "id": "a1b2c3d4",
      "role": "user",
      "content": "消息内容",
      "timestamp": 1700000000.0,
      "metadata": {}
    },
    {
      "id": "e5f6g7h8",
      "role": "bot",
      "content": "Bot 回复",
      "timestamp": 1700000001.0,
      "metadata": {}
    }
  ]
}
```

### POST `/api/chat/send`

发送聊天消息并获取 Bot 回复。

**请求体:**
```json
{
  "book_id": "abc1234567",
  "content": "用户消息内容"
}
```

**响应:**
```json
{
  "success": true,
  "reply": "Bot 的回复文字"
}
```

---

## 数据查询 (Data)

### GET `/api/data/{book_id}/highlights`

获取某本书的所有划线。

**响应:**
```json
{
  "success": true,
  "highlights": [
    {
      "id": "a1b2c3d4",
      "chapter_index": 3,
      "text": "划线文字",
      "context": "上下文",
      "created_at": 1700000000.0
    }
  ]
}
```

### GET `/api/data/{book_id}/reviews`

获取某本书的所有书评。

**响应:**
```json
{
  "success": true,
  "reviews": [
    {
      "id": "a1b2c3d4",
      "chapter_index": 3,
      "content": "书评内容",
      "created_at": 1700000000.0
    }
  ]
}
```

### GET `/api/data/{book_id}/notes`

获取某本书的所有纸条。

**响应:**
```json
{
  "success": true,
  "notes": [
    {
      "id": "a1b2c3d4",
      "chapter_index": 3,
      "content": "纸条内容",
      "created_at": 1700000000.0
    }
  ]
}
```

---

## Bot 阅读进度

### GET `/api/bot-progress/{book_id}`

获取 Bot 对某本书的阅读进度百分比。

**响应:**
```json
{
  "success": true,
  "percent": 23
}
```

---

## 记忆管理 (Memory)

### GET `/api/memory/sessions`

列出所有书籍对话会话。

**响应:**
```json
{
  "success": true,
  "sessions": [
    {
      "book_id": "abc1234567",
      "book_title": "话桑麻",
      "message_count": 12,
      "last_active": 1700000000.0,
      "is_active": true
    }
  ]
}
```

### GET `/api/memory/sessions/{book_id}`

获取某本书的完整对话详情。

**响应:**
```json
{
  "success": true,
  "session": {
    "book_id": "abc1234567",
    "book_title": "话桑麻",
    "created_at": 1700000000.0,
    "last_active": 1700001000.0,
    "chat_history": [
      { "id": "a1b2c3d4", "role": "user", "content": "...", "timestamp": 1700000000.0 },
      { "id": "e5f6g7h8", "role": "bot", "content": "...", "timestamp": 1700000001.0 }
    ]
  }
}
```

### DELETE `/api/memory/sessions/{book_id}`

删除某本书的整段对话记录。

**响应:**
```json
{ "success": true }
```

### DELETE `/api/memory/sessions/{book_id}/messages/{message_id}`

删除某条具体消息。

**响应:**
```json
{ "success": true }
```

---

## 兼容接口 (Legacy)

以下接口为旧版兼容保留，功能与上方记忆管理接口等价。

| 方法 | 路径 | 等价于 |
|------|------|--------|
| GET | `/api/memory/archives` | `/api/memory/sessions` |
| GET | `/api/memory/archives/{session_id}` | `/api/memory/sessions/{book_id}` |
| DELETE | `/api/memory/archives/{session_id}` | `/api/memory/sessions/{book_id}` |
| DELETE | `/api/memory/archives/{session_id}/messages/{message_id}` | `/api/memory/sessions/{book_id}/messages/{message_id}` |
| GET | `/api/memory/active` | 获取当前活跃会话 |
| DELETE | `/api/memory/active/{session_id}/messages/{message_id}` | 删除活跃会话中的消息 |

---

## 静态文件

| 路径前缀 | 来源 | 说明 |
|----------|------|------|
| `/custom-static/` | `plugin_data/.../custom_templates/` | 用户自定义文件 |
| `/static/` | `plugin_dir/templates/` | 插件默认文件 |

---

## 错误响应

所有接口在出错时返回 HTTP 4xx 状态码：

```json
{ "detail": "错误描述" }
```

常见错误码:
- `400` - 请求参数缺失或格式错误
- `404` - 资源不存在（书籍、章节、会话等）
