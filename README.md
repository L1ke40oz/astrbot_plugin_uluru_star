# 乌鲁鲁星 (astrbot_plugin_shared_read)

这是属于你们的故事。

上传 epub/txt 书籍，在乌鲁鲁星里划线、写书评，他会像朋友一样陪你聊书中的故事。他也会自己读书，生成章节记忆，偶尔主动分享读后感。

## 功能概览

| 功能 | 说明 |
|------|------|
| 📚 书架管理 | 上传 epub/txt、自定义封面、分页浏览、删除 |
| 📖 在线阅读器 | 章节切换、右侧目录栏、阅读进度记忆 |
| ✦ 划线 | 选中文字划线，高亮显示，支持取消，自动通知 Bot |
| 📌 书签 | 放置书签标记阅读窗口起点，可视化高亮传入范围 |
| ✓ 打卡 | 章节完成打卡，触发 Bot 生成章节记忆摘要 |
| 📑 笔记 | 按书籍/章节查看所有划线，点击跳转回原文 |
| 💬 阅读聊天 | 可拖拽悬浮按钮 + 可拖拽聊天面板，与 Bot 聊书 |
| 🤖 Bot 自主阅读 | 后台自动推进进度 + 生成章节记忆，概率性主动分享 |
| 📖 Bot 按需阅读 | 用户可通过对话让 Bot 去读某章，读完自动生成记忆 |
| 🧠 章节记忆 | Bot 读完每章后生成 150-200 字摘要，持久化存储 |
| 🔧 LLM 工具 | Bot 可主动调用工具读书/回忆对话（按需加载） |
| 🗂 小窝 | 记忆管理、Bot 动态、纸条箱、连接状态 |
| 📊 阅读统计 | 主页展示共读数据（书架数、章节数、天数等） |
| 🎨 主题系统 | 6 种色系（星紫/雾蓝/樱粉/苔绿/暖茶/深色） |
| ✦ 粒子动效 | 可配置形状（爱心/四芒星/圆点）和颜色 |
| 💌 纸条箱 | 给 Bot 留纸条，持久化存储 |
| 📱 PWA | 支持添加到手机桌面 |
| 🔄 数据持久化 | 头像/昵称/封面/主题/进度全部服务端存储 |

## 架构

```
astrbot_plugin_shared_read/
├── main.py                     # 插件入口，注册钩子、工具和命令
├── _conf_schema.json           # 配置项定义
├── metadata.yaml               # 插件元数据
├── requirements.txt            # Python 依赖（ebooklib, beautifulsoup4, chardet）
├── API.md                      # 后端 API 接口文档
├── templates/                  # 前端文件（默认模板，随插件更新）
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── manifest.json           # PWA 配置
│   ├── sw.js                   # Service Worker
│   ├── planet.png              # 设置页装饰图
│   └── icons/                  # 底部导航图标
│       ├── tools.jpg
│       ├── placeholder.jpg
│       ├── notes.jpg
│       └── settings.jpg
└── core/
    ├── __init__.py
    ├── session_manager.py      # 按书籍管理对话会话（持久化）
    ├── book_manager.py         # epub/txt 解析、书籍/划线/笔记存储
    ├── chat_engine.py          # 阅读聊天，滑动窗口注入，调用 LLM
    ├── bot_reader.py           # Bot 自主阅读 + 章节记忆 + 主动消息
    └── webui_server.py         # FastAPI + uvicorn 独立 Web 服务
```

## 数据存储

所有持久化数据存储在 `data/plugin_data/astrbot_plugin_shared_read/`：

```
plugin_data/astrbot_plugin_shared_read/
├── books/
│   ├── epub_files/             # 原始 epub/txt 文件
│   ├── cache/                  # 解析后的章节 JSON 缓存
│   └── library.json            # 书架索引 + 划线/书评/纸条数据
├── sessions/
│   └── books/                  # 每本书一个 JSON 对话文件
│       └── {book_id}_{书名}.json
├── assets/                     # 用户自定义图片资源
├── custom_templates/           # 用户自定义前端文件（优先加载）
├── profile.json                # 用户数据（头像/昵称/封面/主题/粒子/纸条）
├── bot_reading_progress.json   # Bot 的阅读进度
├── user_reading_progress.json  # 用户的阅读进度
├── bot_chapter_memories.json   # Bot 的章节记忆摘要
└── target_session.json         # 主动消息的目标会话 ID
```

## 核心机制

### 1. 滑动窗口注入

聊天时不注入整章内容，而是根据用户阅读位置：
- 短章节（≤3000字）：全量注入
- 长章节：注入当前滚动位置前后 2000 字窗口
- 有书签时：注入书签位置到当前位置之间的内容
- 最大注入量 3000 字

### 2. 章节记忆系统

Bot 读完一章后（打卡/工具调用/自动推进），调用 LLM 生成 150-200 字摘要：
- 输入：章节原文 + 用户划线
- 输出：从 Bot 视角的故事总结 + 互动记忆
- 存储在 `bot_chapter_memories.json`
- 注入到 AstrBot 的 LLM 上下文中（最近 5 章/书）

### 3. 摘要注入（on_llm_request 钩子）

每次 QQ 等平台触发 LLM 时注入：
- 对话摘要（每本书一行）
- 书架信息（书名 + 双方进度）
- 章节记忆（最近几章的摘要）

### 4. LLM 工具

- **read_bookhouse_chapter** - 阅读章节（2000字/次，读完自动生成记忆）
- **recall_bookhouse_chat** - 回忆对话记录（最近15条）

### 5. Bot 自主阅读

后台定时任务（可配置间隔）：
1. 随机选一本未读完的书
2. 推进 1-2 章
3. 生成章节记忆摘要
4. 有概率发送主动消息

## 配置项

| 分组 | 配置 | 说明 | 默认值 |
|------|------|------|--------|
| WebUI | enabled | 启用网页服务 | true |
| WebUI | host | 监听地址 | 0.0.0.0 |
| WebUI | port | 端口 | 1016 |
| - | message_separator | 消息分段正则 | \\$ |
| Bot 阅读 | enabled | 启用自主阅读 | true |
| Bot 阅读 | reading_interval_min | 阅读间隔下限(分钟) | 120 |
| Bot 阅读 | reading_interval_max | 阅读间隔上限(分钟) | 300 |
| Bot 阅读 | message_probability | 主动消息概率(%) | 30 |
| Bot 阅读 | no_interrupt_minutes | 聊天保护时间(分钟) | 5 |
| 高级 | provider | 固定 Provider | (空=默认) |
| 高级 | persona | 固定人格 | (空=默认) |
| 高级 | persona_override | 手动人格覆盖 | (空) |

## 使用方式

1. 安装插件，确保依赖已安装
2. 在 QQ 发送 `/乌鲁鲁星` 获取访问地址
3. 浏览器打开地址，上传 epub/txt 开始阅读
4. 划线、聊天、打卡，Bot 会陪你一起读
5. 在 QQ 里也可以让 Bot 去读书、讨论书中内容

## 前端自定义

前端文件加载优先级：`custom_templates/` > `templates/`

图片资源加载优先级：`/assets/` (plugin_data) > `/static/` (templates)
