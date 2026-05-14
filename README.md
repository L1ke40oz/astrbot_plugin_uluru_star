# 乌鲁鲁星 (astrbot_plugin_uluru_star)

这是属于你们的故事。

乌鲁鲁星是一个 AstrBot 阅读伴侣插件。上传 epub/txt 书籍后，Bot 会像朋友一样陪你一起读书——你可以划线、写书评、聊天讨论情节。Bot 也会自己读书、生成章节记忆，偶尔主动分享读后感。此外还有像素宠物屋、足迹板等社交功能。

## 功能概览

| 功能 | 说明 |
|------|------|
| 📚 书架管理 | 上传 epub/txt、自定义封面、分页浏览、删除 |
| 📖 在线阅读器 | 章节切换、右侧目录栏、阅读进度记忆 |
| ✦ 划线 | 选中文字划线，高亮显示，支持取消，自动通知 Bot |
| 📌 书签 | 放置书签标记阅读窗口起点，可视化高亮传入范围 |
| ✓ 打卡 | 章节完成打卡，触发 Bot 生成章节记忆摘要 |
| 📝 书评区 | 每章底部可写书评，Bot 自动回复，支持查看历史书评 |
| 📑 笔记 | 按书籍/章节查看所有划线，点击跳转回原文 |
| 💬 阅读聊天 | 可拖拽悬浮按钮 + 可拖拽聊天面板，与 Bot 聊书 |
| 🤖 Bot 自主阅读 | 后台自动推进进度 + 生成章节记忆，概率性主动分享 |
| 📖 Bot 按需阅读 | 用户可通过对话让 Bot 去读某章，读完自动生成记忆 |
| 🧠 章节记忆 | Bot 读完每章后生成 150-200 字摘要，持久化存储 |
| 🔧 LLM 工具 | Bot 可主动调用工具读书/回忆对话（按需加载） |
| 🐾 宠物屋 | 像素宠物养成：投喂、摸摸、捏宠物自定义外观 |
| 📸 足迹板 | 照片墙（含示例照片）、便签纸条、Bot/用户动态 |
| 🗂 小窝 | 记忆管理、动态、纸条箱、阅读进度、连接状态 |
| 📊 阅读统计 | 主页展示共读数据（书架数、章节数、天数等） |
| 🎨 主题系统 | 6 种预设色系 + 自定义颜色（色盘选取，自动生成完整主题） |
| ✦ 粒子动效 | 可配置形状（爱心/四芒星/圆点/雪花）和颜色 |
| 📱 PWA | 支持添加到手机桌面 |
| 🔄 数据持久化 | 头像/昵称/封面/主题/进度全部服务端存储 |
| ⚙️ 缓存管理 | 清除 Service Worker 缓存、重置本地状态 |

## 架构

```
astrbot_plugin_uluru_star/
├── main.py                     # 插件入口：注册钩子、LLM 工具、命令、后台任务
├── _conf_schema.json           # 配置项定义（AstrBot 配置面板用）
├── metadata.yaml               # 插件元数据
├── requirements.txt            # Python 依赖（ebooklib, beautifulsoup4, chardet）
├── API.md                      # 后端 API 接口文档
├── README.md                   # 本文件
├── templates/                  # 前端文件（默认模板，随插件更新）
│   ├── index.html              # 单页应用入口
│   ├── style.css               # 全局样式 + 主题变量
│   ├── app.js                  # 主逻辑（书架、阅读器、聊天、足迹、宠物屋）
│   ├── pet-customization-ui.js # 捏宠物面板 UI 交互
│   ├── pet-sprite-renderer.js  # 像素宠物渲染引擎（CSS box-shadow）
│   ├── pet-templates.js        # 宠物体型模板数据（16×16 网格）
│   ├── pet-palette.js          # 调色板、花纹、配饰定义
│   ├── manifest.json           # PWA 配置
│   ├── sw.js                   # Service Worker（离线缓存）
│   ├── planet.png              # 设置页装饰图
│   ├── icons/                  # 底部导航 + 手风琴卡片图标
│   │   └── example-photo.jpg   # 照片墙示例照片（无照片时展示）
│   └── avatars/                # 默认头像资源
│       ├── default-bot.png     # Bot 默认头像
│       └── default-user.png    # 用户默认头像
└── core/
    ├── __init__.py
    ├── session_manager.py      # 按书籍管理对话会话（持久化到 JSON）
    ├── book_manager.py         # epub/txt 解析、书籍/划线/笔记/书评存储
    ├── chat_engine.py          # 阅读聊天：滑动窗口注入 + 人格解析 + LLM 调用
    ├── bot_reader.py           # Bot 自主阅读 + 章节记忆 + 主动消息 + 动态生成
    ├── pet_house_manager.py    # 宠物屋：CRUD、饥饿/心情衰减、外观自定义验证
    └── webui_server.py         # FastAPI + uvicorn 独立 Web 服务（所有 API）
```

## 模块职责

### main.py — 插件入口

- 注册 AstrBot 插件（`@register`）
- 初始化所有核心组件
- 注册 `/乌鲁鲁星` 命令
- 注册 `on_llm_request` 钩子（注入书架提示 + 足迹上下文到 LLM）
- 注册 LLM 工具（`read_bookhouse_chapter`、`recall_bookhouse_chat`）
- 启动后台任务（WebUI、Bot 阅读、宠物通知）
- 管理 AstrBot 对话快照

### core/session_manager.py — 会话管理

- 每本书一个独立对话文件
- 支持创建/恢复/结束会话
- 心跳保活机制
- 对话历史增删查

### core/book_manager.py — 书籍管理

- 解析 epub（ebooklib + BeautifulSoup）和 txt（chardet 编码检测）
- 章节缓存（解析一次，后续直接读 JSON）
- 划线、书评、笔记的 CRUD
- 书架索引维护

### core/chat_engine.py — 聊天引擎

- 人格解析：插件配置覆盖 > 指定人格 > AstrBot 默认人格
- 滑动窗口注入：根据 scroll_percent / bookmark_percent 截取章节内容
- 调用 LLM 并维护会话上下文

### core/bot_reader.py — Bot 阅读器

- 后台定时阅读循环（可配置间隔）
- 章节记忆生成（调用 LLM 总结，含对话历史 + 书评 + 划线）
- 用户进度追踪（高水位模式）
- 主动消息发送（避免打断活跃对话）
- Bot 动态生成（足迹板碎碎念）
- 接收 `session_manager` 引用以读取聊天历史用于摘要生成

### core/pet_house_manager.py — 宠物屋管理

- 宠物 CRUD（创建/改名/删除）
- 时间衰减系统：饥饿 5/h、心情 3/h（仅饥饿<30时衰减）
- 投喂（饥饿+30，上限100）和摸摸（心情+5~15）
- 外观自定义验证与归一化
- 彩蛋评论池（投喂/摸摸时随机返回）
- 通知状态管理（心情<20 时触发 QQ 通知）

### core/webui_server.py — Web 服务

- FastAPI 应用，uvicorn 异步运行
- 模板加载优先级：custom_templates > templates
- 所有 REST API 端点
- 静态文件服务
- Profile 持久化
- 足迹上下文注入（便签 + 动态注入到 LLM 上下文）

### templates/ — 前端

前端是纯 JavaScript 单页应用（无构建工具），通过 `<script>` 标签按顺序加载：

1. `pet-templates.js` — 宠物体型数据
2. `pet-palette.js` — 颜色/花纹/配饰定义
3. `pet-sprite-renderer.js` — 渲染引擎
4. `pet-customization-ui.js` — 捏宠物 UI
5. `app.js` — 主应用逻辑

宠物渲染使用 CSS box-shadow 像素画技术：每个非透明像素变成一个 `Xpx Ypx 0 #color` 条目。

## 数据存储

所有持久化数据存储在 `data/plugin_data/astrbot_plugin_uluru_star/`：

```
plugin_data/astrbot_plugin_uluru_star/
├── books/
│   ├── epub_files/             # 原始 epub/txt 文件
│   ├── cache/                  # 解析后的章节 JSON 缓存
│   └── library.json            # 书架索引 + 划线/书评/纸条数据
├── sessions/
│   └── books/                  # 每本书一个 JSON 对话文件
│       └── {book_id}_{书名}.json
├── assets/                     # 用户上传的图片资源
│   └── footprints/
│       ├── originals/          # 原图
│       └── thumbs/             # 缩略图（300px 宽）
├── custom_templates/           # 用户自定义前端文件（优先加载）
├── profile.json                # 用户数据（头像/昵称/封面/主题/粒子/足迹/便签）
├── pet_house.json              # 宠物数据（所有宠物状态）
├── bot_reading_progress.json   # Bot 的阅读进度
├── user_reading_progress.json  # 用户的阅读进度
├── bot_chapter_memories.json   # Bot 的章节记忆摘要
└── target_session.json         # 主动消息的目标会话 ID
```

## 核心机制

### 1. LLM 上下文注入（on_llm_request 钩子）

每次 QQ 等平台触发 LLM 请求时，插件注入轻量书架提示：
- 书名列表 + 双方阅读进度百分比
- 最近 3 条便签内容（含 Bot 回复）
- 最近 3 条动态内容
- 提示 Bot 可调用工具获取详细内容
- 自动清理历史注入（防止上下文膨胀）

注入方式可通过 `injection_method` 配置：
- `system_prompt`：注入到不可见的系统提示词（默认）
- `user_message_before`：注入到用户消息前（可见）
- `user_message_after`：注入到用户消息后（可见）

设计原则：不在系统提示中塞大量内容，而是通过工具按需加载。

### 2. 滑动窗口注入（聊天时）

聊天时根据用户阅读位置注入章节内容：
- 短章节（≤3000字）：全量注入
- 长章节：注入当前滚动位置前后 2000 字窗口
- 有书签时：注入书签位置到当前位置之间的内容
- 最大注入量 3000 字

### 3. 章节记忆系统

Bot 读完一章后（打卡/工具调用/自动推进），调用 LLM 生成 150-200 字摘要：
- 输入：章节原文（前 1500 字）+ 用户划线 + 聊天历史（最近 20 条）+ 书评
- 输出：从 Bot 视角的故事总结 + 互动记忆
- 去重：已有记忆的章节不会重复生成（除非 `force=True`）
- Bot 进度严格基于已生成记忆的章节数
- 提交书评时自动触发 `generate_chapter_summary(force=True)` 重新生成

### 4. LLM 工具

| 工具名 | 用途 | 参数 |
|--------|------|------|
| `read_bookhouse_chapter` | 阅读章节内容 | `book_title`(必填), `chapter_index`(可选) |
| `recall_bookhouse_chat` | 回忆对话记录 | `book_title`(可选) |

工具支持续读（offset-based）：单次返回 2000 字，未读完的章节下次调用继续。

### 5. Bot 自主阅读

后台定时任务（可配置间隔 120-300 分钟）：
1. 选择一本有未读章节的书
2. 找到第一个没有记忆的章节
3. 调用 LLM 生成章节记忆
4. 记忆生成成功才算进度推进
5. 有概率发送主动消息（避免打断活跃对话）

### 6. 宠物屋系统

- 时间衰减：饥饿每小时 -5，心情每小时 -3（仅饥饿<30时衰减）
- 动画状态：mood<20→sad, hunger<30→hungry, mood>70→happy, 否则→idle
- 投喂：饥饿+30（上限100），非瞬间回满
- 摸摸：心情+5~15
- 外观自定义：体型模板 × 主色 × 副色 × 花纹 × 配饰
- 渲染：16×16 像素网格 → CSS box-shadow 字符串
- 通知：心情<20 时通过 QQ 发送提醒
- 可配置开关：`pet_house.enabled` 控制是否启用
- 空状态：无宠物时显示居中提示 "还没有宠物，添加一只吧 🐾"

### 7. Bot 动态与用户动态

后台定时任务（可配置间隔，默认 4-8 小时）：
- 结合时间、最近阅读、章节记忆生成碎碎念
- 保存到 profile.json 的 footprints 列表
- 前端以朋友圈风格展示，支持点赞和回复
- 动态使用 Bot 昵称 + 头像（来自 profile 配置）
- 动态 Tab 名称为"动态"

用户也可以发布动态：
- 点击 "+ 发动态" 按钮发布
- 用户动态显示用户头像和昵称
- Bot 自动点赞并异步回复评论
- Bot 和用户的点赞独立追踪（`bot_liked` / `user_liked`）

评论支持回复：
- 点击某条评论可回复该评论者
- 显示格式为 "A 回复 B：内容"
- Bot 回复时包含完整回复链作为对话上下文

### 8. 主动消息分段发送

Bot 发送主动消息时支持按 `message_separator` 正则分段：
- 每段独立发送，段间随机延迟 1-2.5 秒
- 模拟真人打字节奏，避免一次性大段文字

### 9. 选中划线上下文菜单

阅读器中选中文字后弹出浮动划线按钮：
- 使用 document 级别事件（mouseup/contextmenu/selectionchange）
- 兼容桌面端和移动端
- 右键选中文字时拦截默认菜单

### 10. 足迹板拖拽

照片墙和便签纸条支持拖拽定位：
- 拖拽后位置持久化到服务端
- 分页浏览照片（每页 6 张）
- 便签纸条同样支持拖拽和分页
- 无照片时展示内置示例照片（`icons/example-photo.jpg`），可删除（localStorage）、可拖拽

### 11. 用户进度与打卡

- 用户进度采用高水位模式（只进不退）
- 打卡（checkin）触发 Bot 生成章节记忆摘要
- 打卡去重：同一章节不会重复打卡
- 跳读不影响顺序进度指针

### 12. LLM 人格一致性

所有 LLM 调用统一使用 persona 作为 system_prompt：
- `chat_engine.py`：阅读聊天
- `bot_reader.py`：章节记忆生成、主动消息、Bot 动态
- `webui_server.py`：便签回复、动态回复、用户动态回应
- 人格解析优先级：插件覆盖 > 指定人格 > AstrBot 默认人格

### 13. 轻量书架注入 + 足迹上下文

LLM 上下文注入采用工具模式（非全量记忆注入）：
- 仅注入书名列表 + 双方进度百分比（~200字）
- 注入最近 3 条便签和最近 3 条动态
- 详细内容通过 `read_bookhouse_chapter` / `recall_bookhouse_chat` 工具按需加载
- 自动清理历史注入防止上下文膨胀
- 注入位置由 `injection_method` 配置控制

### 14. 自定义提示词

开启 `custom_prompts_enabled` 后，以下场景的 LLM 提示词可在配置面板中自定义：
- 便签回复（`prompt_note_reply`）
- 动态评论回复（`prompt_moment_reply`）
- 回应用户动态（`prompt_react_to_user_moment`）
- Bot 自动发动态（`prompt_bot_dynamics`）
- 章节总结（`prompt_chapter_summary`）

关闭时使用内置默认提示词。

### 15. 书评系统

每章底部有书评区：
- 显示该章已有书评列表
- 提交书评表单
- 提交后 Bot 自动回复（受 `auto_reply_on_review` 配置控制）
- Bot 回复保存在书评记录的 `bot_reply` 字段
- 提交书评同时触发章节记忆重新生成（`force=True`）

### 16. 异步回复轮询

便签和动态的 Bot 回复采用异步模式：
- 前端提交后每 2 秒轮询一次
- 最长轮询 30 秒
- 适用于便签回复和动态评论回复

### 17. 默认头像与主题

- Bot 和用户头像默认使用自定义图片（`avatars/default-bot.png`、`avatars/default-user.png`）
- 主题色盘新增 "✦" 自定义颜色点，点击打开原生取色器
- 选取颜色后自动生成完整主题色系

### 18. 记忆归档昵称

记忆详情页中的对话使用用户配置的昵称显示，而非硬编码名称。

## 配置项

| 分组 | 配置 | 说明 | 默认值 |
|------|------|------|--------|
| WebUI | enabled | 启用网页服务 | true |
| WebUI | host | 监听地址（0.0.0.0=局域网可访问） | 0.0.0.0 |
| WebUI | port | 端口 | 1016 |
| - | auto_reply_on_highlight | 划线时自动触发回复 | true |
| - | auto_reply_on_review | 书评时自动触发回复 | true |
| - | reply_to_message_channel | 同时发送到消息通道 | false |
| - | message_separator | 消息分段正则 | \\$ |
| Bot 阅读 | enabled | 启用自主阅读 | true |
| Bot 阅读 | reading_interval_min | 阅读间隔下限(分钟) | 120 |
| Bot 阅读 | reading_interval_max | 阅读间隔上限(分钟) | 300 |
| Bot 阅读 | message_probability | 主动消息概率(%) | 30 |
| Bot 阅读 | no_interrupt_minutes | 聊天保护时间(分钟) | 5 |
| Bot 阅读 | dynamics_interval_min | 动态间隔下限(小时) | 4 |
| Bot 阅读 | dynamics_interval_max | 动态间隔上限(小时) | 8 |
| 宠物屋 | enabled | 启用宠物屋功能 | true |
| 高级 | provider | 固定 Provider | (空=默认) |
| 高级 | persona | 固定人格 | (空=默认) |
| 高级 | persona_override | 手动人格覆盖 | (空) |
| 高级 | injection_method | 上下文注入方式 | system_prompt |
| 高级 | custom_prompts_enabled | 启用自定义提示词 | false |
| 高级 | prompt_note_reply | 便签回复提示词 | (内置默认) |
| 高级 | prompt_moment_reply | 动态评论回复提示词 | (内置默认) |
| 高级 | prompt_react_to_user_moment | 回应用户动态提示词 | (内置默认) |
| 高级 | prompt_bot_dynamics | Bot 自动发动态提示词 | (内置默认) |
| 高级 | prompt_chapter_summary | 章节总结提示词 | (内置默认) |

## 使用方式

1. 安装插件，确保依赖已安装（`pip install ebooklib beautifulsoup4 chardet`）
2. 在 QQ 发送 `/乌鲁鲁星` 获取访问地址（同时注册目标会话用于主动消息）
3. 浏览器打开地址，上传 epub/txt 开始阅读
4. 划线、聊天、打卡，Bot 会陪你一起读
5. 在 QQ 里也可以让 Bot 去读书、讨论书中内容

## 前端自定义

前端文件加载优先级：`custom_templates/` > `templates/`

将修改后的文件放入 `plugin_data/astrbot_plugin_uluru_star/custom_templates/` 即可覆盖默认模板，插件更新不会覆盖自定义文件。

### 设置页

设置页卡片顺序：外观设置 → 功能设置 → 其他。

设置中提供：
- **清除缓存**：清除 Service Worker 缓存并重新加载页面
- **重置本地状态**：仅清除插件相关的 localStorage 键值（不影响其他站点数据）

## 扩展指南

### 添加新的宠物物种

1. `pet_house_manager.py`：在 `PRESET_SPECIES` 和 `VALID_TEMPLATES` 中添加物种
2. `pet-templates.js`：添加 16×16 网格模板（含 patternMasks 和 accessoryAnchors）
3. `pet-palette.js`：在 `DEFAULT_CUSTOMIZATION` 中添加默认外观

### 添加新的配饰

1. `pet_house_manager.py`：在 `VALID_ACCESSORIES` 中添加 ID
2. `pet-palette.js`：在 `ACCESSORIES` 中添加像素数据
3. 每个模板的 `accessoryAnchors` 中添加锚点坐标

### 添加新的 LLM 工具

1. `main.py`：定义参数 schema 和 handler 函数
2. 在 `_register_read_tool()` 中注册 `FunctionTool`
3. Handler 签名：`async def handler(self, *args, **kwargs) -> str`

### 添加新的 API 端点

1. `webui_server.py`：在 `_setup_app()` 中添加路由
2. 更新 `API.md` 文档

### 修改人格/提示词

- 配置面板中开启 `custom_prompts_enabled` 后可自定义各场景提示词
- 配置面板中设置 `persona_override` 可直接覆盖系统提示
- 或通过 `persona` 选择 AstrBot 中已配置的人格
- 阅读相关指令在 `chat_engine.py` 的 `_build_system_prompt()` 中

### 添加默认头像/示例照片

- Bot 默认头像：`templates/avatars/default-bot.png`
- 用户默认头像：`templates/avatars/default-user.png`
- 照片墙示例照片：`templates/icons/example-photo.jpg`
