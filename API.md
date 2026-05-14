# Uluru Star — API Reference

Base URL: `http://{host}:{port}` (default `http://localhost:1016`)

All JSON endpoints return `{"success": true, ...}` on success.
Error responses use HTTP status codes (400, 404, 500) with `{"detail": "error message"}`.

---

## Page Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main SPA HTML page |
| GET | `/style.css` | Stylesheet |
| GET | `/app.js` | Main frontend script |
| GET | `/sw.js` | Service Worker (PWA offline cache) |
| GET | `/static/{filename}` | Static assets from templates directory |
| GET | `/custom-static/{filename}` | Custom template overrides (if directory exists) |
| GET | `/assets/{path}` | User-uploaded assets (photos, thumbnails) |

---

## Configuration

### GET /api/config/frontend

Returns frontend-relevant configuration values.

**Response:**
```json
{
  "success": true,
  "message_separator": "\\$",
  "pet_house_enabled": true
}
```

---

## Profile

### GET /api/profile

Get stored user profile data (avatars, nicknames, covers, theme, particles, notes).

**Response:**
```json
{
  "success": true,
  "profile": {
    "user_nickname": "你",
    "bot_nickname": "沈星回",
    "user_avatar": "data:image/png;base64,...",
    "bot_avatar": "data:image/png;base64,...",
    "theme": "blue",
    "particles": {"enabled": true, "shape": "heart", "color": "theme"},
    "covers": {"book_id_here": "data:image/..."},
    "note_box": [{"content": "...", "time": 1700000000000}],
    "footprints": [...],
    "fp_notes": [...]
  }
}
```

### POST /api/profile

Save profile data (merge update — only provided keys are updated).

**Request Body:** Any key-value pairs to merge into the profile.

```json
{"user_nickname": "新昵称", "theme": "pink"}
```

**Response:** `{"success": true}`

---

## Sessions

### POST /api/session/start

Start or resume a book reading session.

**Request Body:**
```json
{"book_id": "abc123", "book_title": "书名"}
```

If `book_id` is omitted, acknowledges connection without creating a session.

**Response:**
```json
{
  "success": true,
  "session_active": true,
  "book_id": "abc123",
  "resumed": false,
  "message_count": 12
}
```

### POST /api/session/heartbeat

Keep session alive.

**Request Body:** `{"book_id": "abc123"}`

**Response:** `{"success": true}` or `{"success": false}`

### POST /api/session/end

End the current session.

**Request Body:** `{"book_id": "abc123"}`

**Response:** `{"success": true}`

---

## Books

### GET /api/books

List all books on the shelf.

**Response:**
```json
{
  "success": true,
  "books": [
    {
      "id": "abc123",
      "title": "书名",
      "author": "作者",
      "chapter_count": 30,
      "added_at": 1700000000
    }
  ]
}
```

### POST /api/books/upload

Upload an epub or txt file.

**Request:** `multipart/form-data` with field `file`

**Constraints:** Only `.epub` and `.txt` files accepted.

**Response:**
```json
{"success": true, "book": {"id": "abc123", "title": "...", ...}}
```

### GET /api/books/{book_id}/chapters

Get chapter list for a book.

**Response:**
```json
{
  "success": true,
  "chapters": [
    {"index": 0, "title": "第一章 开始"}
  ]
}
```

### GET /api/books/{book_id}/chapters/{chapter_index}

Get chapter HTML content.

**Response:** `{"success": true, "content": "<p>Chapter text...</p>"}`

### DELETE /api/books/{book_id}

Delete a book and all associated data.

**Response:** `{"success": true}`

---

## Interactions

### POST /api/interact/highlight

Record a text highlight. Also saves a system note in chat history. If `auto_reply_on_highlight` is enabled, triggers bot reply via `respond_to_highlight`.

**Request Body:**
```json
{
  "session_token": "local",
  "book_id": "abc123",
  "chapter_index": 5,
  "text": "highlighted text",
  "context": "surrounding context text"
}
```

**Response:**
```json
{"success": true, "highlight": {"text": "...", "chapter_index": 5, ...}}
```

### POST /api/interact/review

Submit a chapter review. If `auto_reply_on_review` is enabled, triggers bot reply via LLM. Also triggers `generate_chapter_summary(force=True)` to regenerate the chapter memory.

**Request Body:**
```json
{
  "session_token": "local",
  "book_id": "abc123",
  "chapter_index": 5,
  "content": "review text"
}
```

**Response:**
```json
{"success": true, "review": {...}, "bot_reply": "Bot's response"}
```

**Side Effects:**
- Bot reply is saved into the review record's `bot_reply` field
- Chapter summary is regenerated with `force=True` (includes chat history + reviews + highlights)

### POST /api/interact/note

Submit a reading note. Triggers bot reply via LLM.

**Request Body:**
```json
{
  "session_token": "local",
  "book_id": "abc123",
  "chapter_index": 5,
  "content": "note text"
}
```

**Response:**
```json
{"success": true, "note": {...}, "bot_reply": "Bot's response"}
```

---

## Chat

### GET /api/chat/history?book_id={book_id}

Get chat history for a specific book.

**Response:**
```json
{
  "success": true,
  "messages": [
    {
      "id": "msg_001",
      "role": "user",
      "content": "message text",
      "timestamp": 1700000000,
      "metadata": {"type": "highlight", "silent": true}
    }
  ]
}
```

### POST /api/chat/send

Send a chat message and get bot reply.

**Request Body:**
```json
{
  "book_id": "abc123",
  "content": "message text",
  "chapter_index": 5,
  "scroll_percent": 45,
  "bookmark_percent": 20
}
```

- `scroll_percent` (0-100): Current scroll position in the chapter
- `bookmark_percent` (0-100, optional): Bookmark position for reading window

**Response:** `{"success": true, "reply": "Bot's response text"}`

**Sliding Window Logic:**
- Short chapters (≤3000 chars): full text injected into LLM context
- Long chapters: 2000-char window centered on scroll position
- With bookmark: window spans from bookmark to scroll position
- Maximum injection: 3000 chars

---

## Chapter Completion

### POST /api/chapter/complete

Mark a chapter as complete (打卡). Records checkin and triggers bot memory generation.

**Request Body:**
```json
{"book_id": "abc123", "chapter_index": 5}
```

**Response:** `{"success": true, "message": "打卡成功"}`

**Side Effects:**
- Records chapter in user's `checked_chapters_list` (duplicate checkins for same chapter are ignored)
- Triggers async LLM call to generate chapter memory summary (skipped if memory already exists)
- Does NOT directly affect user progress percentage (progress is high-water-mark based on chapters opened)

---

## Reading Progress

### GET /api/bot-progress/{book_id}

Get bot's reading progress percentage for a book.

**Response:** `{"success": true, "percent": 45}`

Note: Progress is based on completed chapters (those with generated memories).

### POST /api/user-progress/report

Report user's reading progress when opening a chapter.

**Request Body:**
```json
{
  "book_id": "abc123",
  "chapter_index": 5,
  "total_chapters": 30,
  "book_title": "书名"
}
```

**Response:** `{"success": true}`

Note: Uses high-water-mark mode — progress never goes backwards.

### GET /api/user-progress/{book_id}

Get user's reading progress percentage.

**Response:** `{"success": true, "percent": 20}`

### GET /api/reading-progress

Get bot and user reading progress for all books.

**Response:**
```json
{
  "success": true,
  "bot_progress": [
    {
      "book_title": "书名",
      "current_chapter": 10,
      "total_chapters": 30,
      "percentage": 33.3
    }
  ],
  "user_progress": [
    {
      "book_title": "书名",
      "current_chapter": 5,
      "total_chapters": 30,
      "percentage": 16.7
    }
  ]
}
```

---

## Statistics

### GET /api/stats

Get aggregate reading statistics.

**Response:**
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

---

## Bot Memories

### GET /api/bot-memories

Get all bot chapter memory summaries.

**Response:**
```json
{
  "success": true,
  "memories": [
    {
      "book_id": "abc123",
      "book_title": "书名",
      "chapter_index": 3,
      "summary": "150-200 char summary from bot's perspective..."
    }
  ]
}
```

---

## Data Queries

### GET /api/data/{book_id}/highlights

Get all highlights for a book.

**Response:** `{"success": true, "highlights": [...]}`

### DELETE /api/data/{book_id}/highlights

Delete a highlight by text match.

**Request Body:**
```json
{"text": "highlighted text", "chapter_index": 5}
```

**Response:** `{"success": true}`

### GET /api/data/{book_id}/reviews

Get all reviews for a book.

**Response:**
```json
{
  "success": true,
  "reviews": [
    {
      "chapter_index": 5,
      "content": "review text",
      "created_at": 1700000000,
      "bot_reply": "Bot's response to the review"
    }
  ]
}
```

### GET /api/data/{book_id}/notes

Get all notes for a book.

**Response:** `{"success": true, "notes": [...]}`

---

## Memory Management

### GET /api/memory/sessions

List all book sessions with metadata.

**Response:**
```json
{
  "success": true,
  "sessions": [
    {
      "book_id": "abc123",
      "book_title": "书名",
      "message_count": 25,
      "last_active": 1700000000,
      "is_active": true
    }
  ]
}
```

### GET /api/memory/sessions/{book_id}

Get full chat history of a book session.

**Response:**
```json
{
  "success": true,
  "session": {
    "book_id": "abc123",
    "book_title": "书名",
    "chat_history": [
      {"id": "msg_001", "role": "user", "content": "...", "timestamp": 1700000000}
    ]
  }
}
```

Note: Chat messages display using user-configured nicknames (from profile) rather than hardcoded names.

### DELETE /api/memory/sessions/{book_id}

Delete a book session entirely.

**Response:** `{"success": true}`

### DELETE /api/memory/sessions/{book_id}/messages/{message_id}

Delete a single message from a session.

**Response:** `{"success": true}`

---

## Legacy Memory Endpoints (Backward Compatibility)

These endpoints mirror the session endpoints above with different naming:

| Method | Path | Maps To |
|--------|------|---------|
| GET | `/api/memory/archives` | List sessions (with legacy field names) |
| GET | `/api/memory/archives/{session_id}` | Get session detail |
| DELETE | `/api/memory/archives/{session_id}` | Delete session |
| DELETE | `/api/memory/archives/{session_id}/messages/{message_id}` | Delete message |
| GET | `/api/memory/active` | Get currently active session |
| DELETE | `/api/memory/active/{session_id}/messages/{message_id}` | Delete active message |

---

## Footprints

### GET /api/footprints

Get all footprint items (photos, bot notes).

**Response:**
```json
{
  "success": true,
  "items": [
    {
      "id": "abc123def456",
      "type": "photo",
      "filename": "abc123def456.jpg",
      "caption": "",
      "created_at": 1700000000,
      "rotation": -3,
      "pos_x": 45.2,
      "pos_y": 30.5
    },
    {
      "id": "bot_1700000000_123",
      "type": "bot_note",
      "content": "Bot's casual thought...",
      "created_at": 1700000000,
      "rotation": 2
    }
  ]
}
```

### POST /api/footprints/upload

Upload a photo to the footprints board.

**Request:** `multipart/form-data` with field `file`

**Accepted formats:** .jpg, .jpeg, .png, .gif, .webp, .bmp

**Response:**
```json
{
  "success": true,
  "item": {
    "id": "abc123def456",
    "type": "photo",
    "filename": "abc123def456.jpg",
    "caption": "",
    "created_at": 1700000000,
    "rotation": 2,
    "pos_x": 45.2,
    "pos_y": 30.5
  }
}
```

**Side Effects:** Creates original + 300px-wide JPEG thumbnail in assets/footprints/.

### DELETE /api/footprints/{item_id}

Delete a photo from the footprints board (removes files and metadata).

**Response:** `{"success": true}`

### POST /api/footprints/{item_id}/position

Update a photo's position after drag.

**Request Body:**
```json
{"pos_x": 45.2, "pos_y": 30.5}
```

- `pos_x` (required): Horizontal position as percentage (0-95), clamped
- `pos_y` (required): Vertical position as percentage (0-95), clamped

**Response:** `{"success": true}`

### POST /api/footprints/note

Post a user sticky note. Bot will reply asynchronously (3-8s delay).

**Request Body:**
```json
{"content": "note text"}
```

**Response:**
```json
{
  "success": true,
  "item": {
    "id": "note_1700000000_123",
    "content": "note text",
    "created_at": 1700000000,
    "reply": null,
    "reply_at": null,
    "pos_x": 45.2,
    "pos_y": 30.5
  }
}
```

**Polling:** Frontend polls `GET /api/footprints/notes` every 2s (up to 30s) to check for bot reply.

### GET /api/footprints/notes

Get all sticky notes with bot replies.

**Response:**
```json
{
  "success": true,
  "notes": [
    {
      "id": "note_1700000000_123",
      "content": "user note text",
      "created_at": 1700000000,
      "reply": "bot reply text",
      "reply_at": 1700000005,
      "pos_x": 45.2,
      "pos_y": 30.5
    }
  ]
}
```

### DELETE /api/footprints/notes/{note_id}

Delete a sticky note and its bot reply.

**Response:** `{"success": true}`

### POST /api/footprints/notes/{note_id}/position

Update a sticky note's position after drag.

**Request Body:**
```json
{"pos_x": 45.2, "pos_y": 30.5}
```

- `pos_x` (required): Horizontal position as percentage (0-95), clamped
- `pos_y` (required): Vertical position as percentage (0-95), clamped

**Response:** `{"success": true}`

---

## Moments (动态)

### GET /api/footprints/moments

Get all moments (both bot-generated and user-posted).

**Response:**
```json
{
  "success": true,
  "moments": [
    {
      "id": "bot_1700000000_123",
      "type": "bot_note",
      "content": "Bot's casual thought",
      "created_at": 1700000000,
      "rotation": 2,
      "bot_liked": false,
      "user_liked": false,
      "like_count": 0,
      "replies": []
    },
    {
      "id": "user_1700000000_456",
      "type": "user_note",
      "content": "User's posted moment",
      "created_at": 1700000000,
      "bot_liked": true,
      "user_liked": false,
      "like_count": 1,
      "replies": [
        {"author": "bot", "content": "Bot's comment", "created_at": 1700000005}
      ]
    }
  ]
}
```

**Note:** Returns both `bot_note` and `user_note` type moments, sorted by creation time.

### POST /api/footprints/moments

Create a user moment. Bot will auto-like and reply asynchronously.

**Request Body:**
```json
{"content": "moment text"}
```

**Response:**
```json
{
  "success": true,
  "moment": {
    "id": "user_1700000000_456",
    "type": "user_note",
    "content": "moment text",
    "created_at": 1700000000,
    "bot_liked": false,
    "user_liked": false,
    "like_count": 0,
    "replies": []
  }
}
```

**Side Effects:** Bot auto-likes and generates a reply comment asynchronously.

**Polling:** Frontend polls `GET /api/footprints/moments` every 2s (up to 30s) to check for bot reply.

### POST /api/footprints/moments/{moment_id}/like

Toggle like on a moment. Bot and user likes are tracked independently.

**Request Body (optional):**
```json
{"actor": "user"}
```

If no actor specified, defaults to user. Bot likes are set automatically when reacting to user moments.

**Response:**
```json
{"success": true, "user_liked": true, "bot_liked": false}
```

**Fields:**
- `user_liked`: Whether the user has liked this moment
- `bot_liked`: Whether the bot has liked this moment

### POST /api/footprints/moments/{moment_id}/reply

Reply to a moment. Bot will reply back asynchronously.

**Request Body:**
```json
{
  "content": "reply text",
  "reply_to": "沈星回"
}
```

- `content` (required): Reply text
- `reply_to` (optional): Name of the person being replied to. When present, the reply is displayed as "A 回复 B：content"

**Response:** `{"success": true}`

**Side Effects:**
- Bot generates a reply comment asynchronously
- Bot reply includes full reply chain as conversation context for LLM

**Polling:** Frontend polls `GET /api/footprints/moments` every 2s (up to 30s) to check for bot reply.

---

## Pet House

All pet house endpoints check `getattr(self.plugin, "pet_house_manager", None)` and return HTTP 500 if the pet house feature is disabled.

### GET /api/pets

List all pets with time-based decay applied.

**Response:**
```json
{
  "success": true,
  "pets": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "小橘",
      "species": "cat",
      "hunger": 85,
      "mood": 72,
      "last_updated": 1700000000.0,
      "created_at": 1699000000.0,
      "photo_filename": "a1b2c3d4e5f6_photo.jpg",
      "notified": false,
      "customization_data": {
        "template_id": "pointy-ear-cat",
        "primary_color": "orange",
        "secondary_color": "cream",
        "pattern": "solid",
        "accessory": null
      },
      "animation_state": "happy"
    }
  ]
}
```

**Animation States:** `idle`, `happy`, `hungry`, `sad`

**Species:** `cat`, `dog`, `rabbit`, `hamster`

### POST /api/pets

Create a new pet.

**Request Body:**
```json
{"name": "小橘", "species": "cat"}
```

**Validation:**
- `name`: non-empty after trimming
- `species`: one of `cat`, `dog`, `rabbit`, `hamster`

**Response:**
```json
{"success": true, "pet": {...}}
```

### PUT /api/pets/{pet_id}

Update a pet's name.

**Request Body:** `{"name": "新名字"}`

**Response:** `{"success": true, "pet": {...}}`

### DELETE /api/pets/{pet_id}

Delete a pet (also removes photo file if exists).

**Response:** `{"success": true}`

### POST /api/pets/{pet_id}/feed

Feed a pet (increases hunger by 30, capped at 100).

**Response:**
```json
{
  "success": true,
  "pet": {...},
  "comment": "它吃得比我还香"
}
```

The `comment` field contains a random easter egg comment from the bot character.

**Note:** Feeding adds +30 to hunger (not instant full). Multiple feedings may be needed to fully restore a starving pet.

### POST /api/pets/{pet_id}/pet

Pet (摸摸) a pet (increases mood by 5-15).

**Response:**
```json
{
  "success": true,
  "pet": {...},
  "comment": "手感好吗"
}
```

### GET /api/pets/{pet_id}/photo

Serve a pet's ID photo file.

**Response:** Image file (FileResponse)

### POST /api/pets/{pet_id}/photo

Upload an ID photo for a pet.

**Request:** `multipart/form-data` with field `file`

**Accepted formats:** .jpg, .jpeg, .png, .gif, .webp

**Size limit:** 5MB

**Response:** `{"success": true, "filename": "a1b2c3d4e5f6_photo.jpg"}`

---

## Pet Customization

### GET /api/pets/{pet_id}/customization

Get a pet's customization data, normalized for compatibility.

Legacy pets (no customization_data) return species defaults. Invalid values (e.g., deleted templates) are replaced with safe fallbacks.

**Response:**
```json
{
  "success": true,
  "customization_data": {
    "template_id": "pointy-ear-cat",
    "primary_color": "orange",
    "secondary_color": "cream",
    "pattern": "solid",
    "accessory": null
  }
}
```

**Valid Values:**

| Field | Valid Options |
|-------|-------------|
| template_id (cat) | `pointy-ear-cat`, `round-face-cat` |
| template_id (dog) | `pointy-ear-dog`, `floppy-ear-dog`, `small-round-dog` |
| template_id (rabbit) | `standard-rabbit` |
| template_id (hamster) | `standard-hamster` |
| primary_color / secondary_color | `orange`, `black`, `white`, `gray`, `darkBrown`, `lightBrown`, `cream`, `ginger` |
| pattern | `solid`, `two-tone`, `tabby`, `cow` |
| accessory | `null`, `bell-collar`, `scarf`, `crown`, `sunglasses` |

### PUT /api/pets/{pet_id}/customization

Validate and update a pet's customization data.

**Request Body:**
```json
{
  "template_id": "round-face-cat",
  "primary_color": "black",
  "secondary_color": "white",
  "pattern": "cow",
  "accessory": "bell-collar"
}
```

All five fields are required. Values are validated against the pet's species.

**Response:** `{"success": true, "pet": {...}}`

**Error (400):** `{"detail": "invalid template_id for species: 'pointy-ear-dog' is not valid for 'cat'"}`

---

## LLM Tools (Bot-Invoked)

These are not HTTP endpoints but LLM function tools registered with AstrBot. The bot can invoke them during conversations.

### read_bookhouse_chapter

Read a chapter from the bookshelf. Called when user asks bot to read a book.

**Parameters:**
```json
{
  "book_title": "书名 (fuzzy match)",
  "chapter_index": 3
}
```

- `book_title` (required): Book title, supports fuzzy/partial matching
- `chapter_index` (optional): 0-based chapter index. Omit to continue from current progress.

**Behavior:**
- Returns up to 2000 chars per call (offset-based continuation)
- Updates bot reading progress on sequential reads
- Jump reads (non-sequential) don't advance the progress pointer
- Triggers chapter memory generation when a chapter is fully read

### recall_bookhouse_chat

Recall chat history from the reading companion.

**Parameters:**
```json
{
  "book_title": "书名 (fuzzy match, optional)"
}
```

- `book_title` (optional): Filter by book. Omit to get recent chats from all books.

**Behavior:**
- Returns last 15 messages per book session
- Limited to 3 most recent books if no filter specified
