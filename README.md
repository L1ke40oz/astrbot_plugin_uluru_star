# 乌鲁鲁星 (astrbot_plugin_shared_read)

这是属于你们的故事。

上传 epub 书籍，在乌鲁鲁星里划线、写书评，他会像朋友一样陪你聊书中的故事。他也会自己读书，偶尔主动分享读后感。

## 功能概览

| 功能 | 说明 |
|------|------|
| 📚 书架管理 | 上传 epub、自定义封面、分页浏览、删除 |
| 📖 在线阅读器 | 章节切换、右侧目录栏、阅读进度记忆 |
| ✦ 划线 | 选中文字划线，淡蓝色高亮，支持取消 |
| 📑 笔记 | 按书籍/章节查看所有划线，支持删除 |
| 💬 阅读聊天 | 可拖拽悬浮按钮 + 可拖拽聊天面板，与 Bot 聊书 |
| 🤖 Bot 自主阅读 | 后台自动推进进度，概率性主动分享感想 |
| 📖 Bot 按需阅读 | 用户可通过对话让 Bot 去读某章，进度实时更新 |
| 🧠 摘要注入 | 对话摘要自动注入 LLM 上下文，省 token |
| 🔧 LLM 工具 | Bot 可主动调用工具读书/回忆对话（按需加载完整内容） |
| 🗂 记忆管理 | 工具箱面板可视化查看/删除对话记录 |

## 架构

```
astrbot_plugin_shared_read/
├── main.py                     # 插件入口，注册钩子、工具和命令
├── _conf_schema.json           # 配置项定义
├── metadata.yaml               # 插件元数据
├── requirements.txt            # Python 依赖
├── API.md                      # 后端 API 接口文档（供前端开发参考）
├── templates/                  # 前端文件（默认模板，随插件更新）
│   ├── index.html
│   ├── style.css
│   └── app.js
└── core/
    ├── __init__.py
    ├── session_manager.py      # 按书籍管理对话会话（持久化）
    ├── book_manager.py         # epub 解析、书籍/划线/笔记存储
    ├── chat_engine.py          # 阅读聊天，调用 LLM
    ├── bot_reader.py           # Bot 自主阅读 + 主动消息 + 按需阅读
    └── webui_server.py         # FastAPI + uvicorn 独立 Web 服务
```

## 数据存储

所有持久化数据存储在 `data/plugin_data/astrbot_plugin_shared_read/`，**插件更新不会覆盖此目录**：

```
plugin_data/astrbot_plugin_shared_read/
├── books/
│   ├── epub_files/             # 原始 epub 文件
│   ├── cache/                  # 解析后的章节 JSON 缓存
│   └── library.json            # 书架索引 + 划线/书评/纸条数据
├── sessions/
│   └── books/                  # 每本书一个 JSON 对话文件
│       ├── {book_id}.json      # 某本书的聊天记录
│       └── astrbot_chat.json   # QQ 对话快照（自动生成）
├── custom_templates/           # 用户自定义前端文件（优先加载）
├── bot_reading_progress.json   # Bot 的阅读进度（含章内偏移）
└── target_session.json         # 主动消息的目标会话 ID
```

## 前端自定义

前端文件加载优先级：`custom_templates/` > `templates/`

用户可以将修改后的 HTML/CSS/JS 放到 `plugin_data/.../custom_templates/` 目录中，插件更新不会覆盖这些文件。

**操作步骤：**
1. 将 `templates/` 中的文件复制到 `plugin_data/astrbot_plugin_shared_read/custom_templates/`
2. 在 `custom_templates/` 中修改前端文件
3. 刷新浏览器即可看到效果

后端 API 接口文档见 [API.md](./API.md)，方便重做前端时参考可用接口。

## 核心机制

### 1. 会话管理（按书籍绑定）

- 每本书有独立的对话文件，互不干扰
- 会话是持久化的，刷新页面不会丢失
- 打开阅读器时自动加载对应书的聊天记录
- 空会话不持久化（只有有消息时才写入磁盘）

### 2. 摘要注入（on_llm_request 钩子）

每次用户在 QQ 等平台发消息触发 LLM 时：
1. 清理上一轮注入的旧内容（通过标记头尾识别）
2. 生成所有书籍会话的摘要（每本书一行，~50字）
3. 注入书架信息（书名 + Bot 阅读进度）
4. 注入到 `req.system_prompt` 末尾

每次请求只多几百字，大幅节省 token。

### 3. LLM 工具（按需加载完整内容）

注册了两个 function-calling 工具，LLM 自己判断什么时候需要调用：

- **read_bookhouse_chapter** - 阅读书架上的书籍章节（2000字/次，支持续读）
- **recall_bookhouse_chat** - 回忆某本书的完整聊天记录（最近15条）

### 4. Bot 自主阅读

后台定时任务：
1. 每 2-5 小时（可配置）随机选一本未读完的书
2. 推进 1-2 章，更新进度
3. 有概率（默认 30%）生成一条读后感发送到用户的聊天窗口
4. 发送前检查用户是否在活跃聊天中，避免打断

### 5. 对话快照（AstrBot → 乌鲁鲁星）

用户打开 WebUI 时，自动从 AstrBot 的对话历史中提取最近 20 条消息，保存为虚拟会话文件，在工具箱记忆管理面板中可见。

## 配置项

| 分组 | 配置 | 说明 | 默认值 |
|------|------|------|--------|
| WebUI | enabled | 启用网页服务 | true |
| WebUI | host | 监听地址 | 0.0.0.0 |
| WebUI | port | 端口 | 1016 |
| - | auto_reply_on_highlight | 划线时回复 | true |
| - | auto_reply_on_review | 书评时回复 | true |
| - | auto_reply_on_note | 纸条时回复 | true |
| - | reply_to_message_channel | 同时发到 QQ | false |
| Bot 阅读 | enabled | 启用自主阅读 | true |
| Bot 阅读 | reading_interval_min | 阅读间隔下限(分钟) | 120 |
| Bot 阅读 | reading_interval_max | 阅读间隔上限(分钟) | 300 |
| Bot 阅读 | message_probability | 主动消息概率(%) | 30 |
| Bot 阅读 | no_interrupt_minutes | 聊天保护时间(分钟) | 5 |
| 高级 | provider | 固定 Provider | (空=默认) |
| 高级 | persona | 固定人格 | (空=默认) |
| 高级 | persona_override | 手动人格覆盖 | (空) |

## 使用方式

1. 安装插件，确保 `ebooklib` 和 `beautifulsoup4` 已安装
2. 在 AstrBot 配置中调整设置
3. 在 QQ 发送 `/乌鲁鲁星` 获取访问地址（同时注册主动消息目标）
4. 浏览器打开地址，上传 epub 开始阅读
5. 划线、聊天，Bot 会陪你一起读
6. 在 QQ 里也可以让 Bot 去读书、讨论书中内容

## Token 优化策略

- 平时只注入摘要（每本书一行，总共几百字）
- LLM 需要细节时才通过工具按需加载完整内容
- 章节内容截取 2000 字/次，支持续读
- 对话记录通过工具按需获取，不常驻上下文
