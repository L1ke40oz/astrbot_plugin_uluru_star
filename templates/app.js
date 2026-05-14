/**
 * 乌鲁鲁星 - 首页 + 书架（分页）
 */

var API_BASE = window.location.origin;
var SESSION_KEY = "shared_read_session_token";
var USER_NICKNAME_KEY = "shared_read_user_nickname";
var BOT_NICKNAME_KEY = "shared_read_bot_nickname";
var USER_AVATAR_KEY = "shared_read_user_avatar";
var BOT_AVATAR_KEY = "shared_read_bot_avatar";
var COVER_PREFIX = "shared_read_cover_";
var PROGRESS_PREFIX = "shared_read_progress_";
var HIGHLIGHTS_KEY = "shared_read_highlights";
var HEARTBEAT_INTERVAL = 30000;
var BOOKS_PER_PAGE = 8; // 4 columns x 2 rows

var state = {
  sessionToken: null,
  currentBookId: null,
  currentBookTitle: null,
  heartbeatTimer: null,
  books: [],
  currentPage: 0,
  messageSeparator: null, // regex for splitting bot messages into multiple bubbles
};

// ==================== Init ====================

async function init() {
  await loadProfileFromServer();
  restoreProfile();
  await loadFrontendConfig();
  await loadBooks();
  await syncHighlightsFromServer();
  // restore last selected book (after books are loaded so we can get the title)
  var lastBook = localStorage.getItem("shared_read_progress_selected_book");
  if (lastBook && state.books.find(function (b) { return b.id === lastBook; })) {
    state.currentBookId = lastBook;
    var book = state.books.find(function (b) { return b.id === lastBook; });
    state.currentBookTitle = book ? book.title : "";
    await startSession(lastBook);
  }
  bindEvents();
  loadFootprints();
}

// ==================== Profile Sync ====================

async function loadProfileFromServer() {
  try {
    var res = await apiGet("profile");
    if (res.success && res.profile) {
      var p = res.profile;
      // sync server data into localStorage so restoreProfile picks it up
      if (p.user_nickname) localStorage.setItem(USER_NICKNAME_KEY, p.user_nickname);
      if (p.bot_nickname) localStorage.setItem(BOT_NICKNAME_KEY, p.bot_nickname);
      if (p.user_avatar) localStorage.setItem(USER_AVATAR_KEY, p.user_avatar);
      if (p.bot_avatar) localStorage.setItem(BOT_AVATAR_KEY, p.bot_avatar);
      // restore theme
      if (p.theme !== undefined) {
        localStorage.setItem("shared_read_theme", p.theme);
        if (p.theme) {
          document.documentElement.setAttribute("data-theme", p.theme);
        } else {
          document.documentElement.removeAttribute("data-theme");
        }
      }
      // restore custom color
      if (p.custom_color) {
        localStorage.setItem("shared_read_custom_color", p.custom_color);
        if (p.theme === "custom") {
          applyCustomThemeColor(p.custom_color);
        }
      }
      // restore particle settings
      if (p.particles) {
        localStorage.setItem("shared_read_particles", JSON.stringify(p.particles));
      }
      // restore book covers
      if (p.covers) {
        Object.keys(p.covers).forEach(function (bookId) {
          localStorage.setItem(COVER_PREFIX + bookId, p.covers[bookId]);
        });
      }
    }
  } catch (e) {
    // offline or first run, use localStorage as-is
  }
}

async function syncHighlightsFromServer() {
  // Sync highlights from server to localStorage for cross-device consistency.
  // Server is the source of truth; local highlights are merged in.
  try {
    if (!state.books || state.books.length === 0) return;
    var serverAll = {};
    for (var i = 0; i < state.books.length; i++) {
      var bookId = state.books[i].id;
      var bookTitle = state.books[i].title || "";
      var res = await apiGet("data/" + bookId + "/highlights");
      if (res.success && res.highlights && res.highlights.length > 0) {
        // Convert server format to localStorage format
        serverAll[bookId] = res.highlights.map(function (h) {
          return {
            chapter: h.chapter_index,
            chapterTitle: h.chapter_title || h.chapterTitle || ("第" + ((h.chapter_index || 0) + 1) + "章"),
            text: h.text,
            context: h.context || "",
            time: h.created_at ? h.created_at * 1000 : Date.now(),
          };
        });
      }
    }
    if (Object.keys(serverAll).length === 0) return;
    // Server data replaces local data for books that have server highlights
    var localAll = getAllHighlights();
    Object.keys(serverAll).forEach(function (bookId) {
      localAll[bookId] = serverAll[bookId];
    });
    localStorage.setItem(HIGHLIGHTS_KEY, JSON.stringify(localAll));
  } catch (e) {
    // offline, keep localStorage as-is
  }
}

function saveProfileToServer(key, value) {
  var data = {};
  data[key] = value;
  apiPost("profile", data).catch(function () {});
}

function saveCoversToServer(bookId, coverData) {
  // fetch current covers, merge, and save
  apiGet("profile").then(function (res) {
    var covers = (res.success && res.profile && res.profile.covers) || {};
    covers[bookId] = coverData;
    apiPost("profile", { covers: covers }).catch(function () {});
  }).catch(function () {});
}

function restoreProfile() {
  var userNick = localStorage.getItem(USER_NICKNAME_KEY);
  if (userNick) {
    document.getElementById("user-nickname").textContent = userNick;
    document.getElementById("progress-user-name").textContent = userNick;
  }
  var botNick = localStorage.getItem(BOT_NICKNAME_KEY);
  if (botNick) {
    document.getElementById("bot-nickname").textContent = botNick;
    document.getElementById("progress-bot-name").textContent = botNick;
  }
  var userAvatar = localStorage.getItem(USER_AVATAR_KEY);
  if (userAvatar) setAvatarImage("user", userAvatar);
  var botAvatar = localStorage.getItem(BOT_AVATAR_KEY);
  if (botAvatar) setAvatarImage("bot", botAvatar);
}

// ==================== Frontend Config ====================

async function loadFrontendConfig() {
  try {
    var result = await apiGet("config/frontend");
    if (result.success && result.message_separator) {
      try {
        state.messageSeparator = new RegExp(result.message_separator);
      } catch (e) {
        console.warn("Invalid message_separator regex:", result.message_separator);
      }
    }
    // hide pet house section if disabled in config
    if (result.success && result.pet_house_enabled === false) {
      var petSection = document.getElementById("pet-house-section");
      if (petSection) petSection.style.display = "none";
    }
  } catch (e) {
    // use default if config fetch fails
    state.messageSeparator = /\$/;
  }
}

// ==================== Session ====================

var _capsuleShown = false;

async function startSession(bookId) {
  try {
    var body = {};
    if (bookId) {
      var book = state.books.find(function (b) { return b.id === bookId; });
      body.book_id = bookId;
      body.book_title = book ? book.title : "";
      state.currentBookId = bookId;
      state.currentBookTitle = body.book_title;
    }
    var result = await apiPost("session/start", body);
    if (result.success) {
      if (!_capsuleShown) {
        showCapsule();
        _capsuleShown = true;
      }
      startHeartbeat();
    }
  } catch (e) {
    console.error("Session start failed:", e);
  }
}

function startHeartbeat() {
  if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
  state.heartbeatTimer = setInterval(async function () {
    if (!state.currentBookId) return;
    try {
      await apiPost("session/heartbeat", { book_id: state.currentBookId });
    } catch (e) {}
  }, HEARTBEAT_INTERVAL);
}

document.addEventListener("visibilitychange", function () {
  if (document.hidden) {
    if (state.heartbeatTimer) { clearInterval(state.heartbeatTimer); state.heartbeatTimer = null; }
  } else {
    startHeartbeat();
  }
});

window.addEventListener("beforeunload", function () {
  if (state.currentBookId) {
    navigator.sendBeacon(
      API_BASE + "/api/session/end",
      JSON.stringify({ book_id: state.currentBookId })
    );
  }
});

// ==================== Capsule ====================

function showCapsule() {
  var el = document.getElementById("capsule-toast");
  el.classList.add("show");
  setTimeout(function () { el.classList.remove("show"); }, 5000);
}

// ==================== Avatar ====================

function setAvatarImage(who, dataUrl) {
  var img = document.getElementById(who + "-avatar-img");
  var fallback = document.getElementById(who + "-avatar-fallback");
  img.src = dataUrl;
  img.classList.add("has-src");
  img.style.display = "block";
  fallback.style.display = "none";
}

function handleAvatarChange(who, file) {
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function (e) {
    setAvatarImage(who, e.target.result);
    localStorage.setItem(who === "user" ? USER_AVATAR_KEY : BOT_AVATAR_KEY, e.target.result);
    saveProfileToServer(who === "user" ? "user_avatar" : "bot_avatar", e.target.result);
  };
  reader.readAsDataURL(file);
}

// ==================== Nickname ====================

function startNicknameEdit(who) {
  var el = document.getElementById(who + "-nickname");
  el.contentEditable = "true";
  el.classList.add("editing");
  el.focus();
  var range = document.createRange();
  range.selectNodeContents(el);
  var sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function finishNicknameEdit(who) {
  var el = document.getElementById(who + "-nickname");
  el.contentEditable = "false";
  el.classList.remove("editing");
  var defaultName = who === "bot" ? "沈星回" : "你";
  var name = el.textContent.trim() || defaultName;
  el.textContent = name;
  localStorage.setItem(who === "user" ? USER_NICKNAME_KEY : BOT_NICKNAME_KEY, name);
  saveProfileToServer(who === "user" ? "user_nickname" : "bot_nickname", name);
  document.getElementById("progress-" + who + "-name").textContent = name;
}

// ==================== Bookshelf ====================

async function loadBooks() {
  try {
    var result = await apiGet("books");
    if (result.success) {
      state.books = result.books;
      var total = getTotalPages();
      if (state.currentPage >= total) {
        state.currentPage = Math.max(0, total - 1);
      }
      renderPage();
      populateProgressBookSelect();
    }
  } catch (e) {
    console.error("Failed to load books:", e);
  }
}

function getTotalPages() {
  return Math.max(1, Math.ceil(state.books.length / BOOKS_PER_PAGE));
}

function renderPage() {
  var grid = document.getElementById("shelf-grid");
  var empty = document.getElementById("shelf-empty");
  var books = state.books;

  if (books.length === 0) {
    grid.style.display = "none";
    empty.style.display = "block";
    renderPagination();
    return;
  }

  grid.style.display = "grid";
  empty.style.display = "none";

  var start = state.currentPage * BOOKS_PER_PAGE;
  var pageBooks = books.slice(start, start + BOOKS_PER_PAGE);

  grid.innerHTML = pageBooks.map(function (book) {
    var coverData = localStorage.getItem(COVER_PREFIX + book.id) || "";
    var coverHtml = coverData
      ? '<img src="' + coverData + '" alt="" />'
      : '<span class="cover-fallback">📖</span>';

    return '<div class="book-item" data-book-id="' + book.id + '">' +
      '<div class="book-cover">' +
        coverHtml +
        '<button class="book-delete-btn" data-book-id="' + book.id + '" title="删除">×</button>' +
        '<button class="cover-upload-btn" data-book-id="' + book.id + '" title="换封面">🖼</button>' +
      '</div>' +
      '<span class="book-name" title="' + escapeAttr(book.title) + '">' + escapeHtml(book.title) + '</span>' +
    '</div>';
  }).join("");

  // bind cover upload
  grid.querySelectorAll(".cover-upload-btn").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var bookId = btn.dataset.bookId;
      var input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.onchange = function () {
        if (!input.files[0]) return;
        var reader = new FileReader();
        reader.onload = function (ev) {
          localStorage.setItem(COVER_PREFIX + bookId, ev.target.result);
          saveCoversToServer(bookId, ev.target.result);
          renderPage();
        };
        reader.readAsDataURL(input.files[0]);
      };
      input.click();
    });
  });

  // bind delete
  grid.querySelectorAll(".book-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      deleteBook(btn.dataset.bookId);
    });
  });

  // bind book click → open reader
  grid.querySelectorAll(".book-item").forEach(function (item) {
    item.addEventListener("click", function () {
      openReader(item.dataset.bookId);
    });
  });

  renderPagination();
}

function renderPagination() {
  var container = document.getElementById("shelf-pagination");
  var total = getTotalPages();
  var prevBtn = document.getElementById("btn-prev-page");
  var nextBtn = document.getElementById("btn-next-page");
  var navWrapper = document.getElementById("shelf-nav");

  // hide nav if only one page or no books
  if (total <= 1) {
    navWrapper.style.display = "none";
    return;
  }

  navWrapper.style.display = "flex";

  // show/hide arrows
  if (state.currentPage === 0) {
    prevBtn.classList.add("hidden");
  } else {
    prevBtn.classList.remove("hidden");
  }

  if (state.currentPage >= total - 1) {
    nextBtn.classList.add("hidden");
  } else {
    nextBtn.classList.remove("hidden");
  }

  // dots
  container.innerHTML = "";
  for (var i = 0; i < total; i++) {
    var dot = document.createElement("button");
    dot.className = "page-dot" + (i === state.currentPage ? " active" : "");
    dot.dataset.page = i;
    dot.addEventListener("click", function () {
      state.currentPage = parseInt(this.dataset.page);
      renderPage();
    });
    container.appendChild(dot);
  }
}

function goToPage(page) {
  var total = getTotalPages();
  if (page < 0 || page >= total || page === state.currentPage) return;

  var grid = document.getElementById("shelf-grid");
  var goingNext = page > state.currentPage;

  grid.classList.remove("slide-in", "slide-out-left", "slide-out-right");
  grid.classList.add(goingNext ? "slide-out-left" : "slide-out-right");

  setTimeout(function () {
    state.currentPage = page;
    renderPage();

    grid.style.transition = "none";
    grid.classList.remove("slide-out-left", "slide-out-right");
    grid.classList.add(goingNext ? "slide-out-right" : "slide-out-left");

    void grid.offsetWidth;

    grid.style.transition = "";
    grid.classList.remove("slide-out-left", "slide-out-right");
    grid.classList.add("slide-in");
  }, 250);
}

// ==================== Upload ====================

async function uploadBook(file) {
  if (!file || !(file.name.endsWith(".epub") || file.name.endsWith(".txt") || file.name.endsWith(".pdf"))) return;
  var formData = new FormData();
  formData.append("file", file);
  try {
    var resp = await fetch(API_BASE + "/api/books/upload", { method: "POST", body: formData });
    var result = await resp.json();
    if (result.success) await loadBooks();
  } catch (e) {
    console.error("Upload failed:", e);
  }
}

async function deleteBook(bookId) {
  if (!confirm("确定删除这本书吗？")) return;
  try {
    await fetch(API_BASE + "/api/books/" + bookId, { method: "DELETE" });
    localStorage.removeItem(COVER_PREFIX + bookId);
    localStorage.removeItem(PROGRESS_PREFIX + bookId);
    var all = getAllHighlights();
    if (all[bookId]) {
      delete all[bookId];
      localStorage.setItem(HIGHLIGHTS_KEY, JSON.stringify(all));
    }
    await loadBooks();
  } catch (e) {
    console.error("Delete failed:", e);
  }
}

// ==================== Swipe support (mobile, horizontal drag like home screen) ====================

(function () {
  var startX = 0;
  var currentX = 0;
  var isDragging = false;
  var grid = null;
  var wrapper = null;
  var threshold = 0.33;

  document.addEventListener("DOMContentLoaded", function () {
    wrapper = document.querySelector(".shelf-grid-wrapper");
    if (!wrapper) return;

    wrapper.addEventListener("touchstart", function (e) {
      grid = document.getElementById("shelf-grid");
      if (!grid) return;
      startX = e.touches[0].clientX;
      currentX = startX;
      isDragging = true;
      grid.style.transition = "none";
    }, { passive: true });

    wrapper.addEventListener("touchmove", function (e) {
      if (!isDragging || !grid) return;
      currentX = e.touches[0].clientX;
      var diff = currentX - startX;
      var total = getTotalPages();

      if (state.currentPage === 0 && diff > 0) diff = diff * 0.3;
      if (state.currentPage >= total - 1 && diff < 0) diff = diff * 0.3;

      grid.style.transform = "translateX(" + diff + "px)";
      grid.style.opacity = Math.max(0.4, 1 - Math.abs(diff) / wrapper.offsetWidth);
    }, { passive: true });

    wrapper.addEventListener("touchend", function () {
      if (!isDragging || !grid) return;
      isDragging = false;

      var diff = currentX - startX;
      var wrapperWidth = wrapper.offsetWidth;
      var total = getTotalPages();

      grid.style.transition = "";

      if (Math.abs(diff) > wrapperWidth * threshold) {
        if (diff < 0 && state.currentPage < total - 1) {
          grid.style.transform = "";
          grid.style.opacity = "";
          goToPage(state.currentPage + 1);
          return;
        } else if (diff > 0 && state.currentPage > 0) {
          grid.style.transform = "";
          grid.style.opacity = "";
          goToPage(state.currentPage - 1);
          return;
        }
      }

      grid.style.transform = "translateX(0)";
      grid.style.opacity = "1";
    }, { passive: true });
  });
})();

// ==================== Tab Navigation ====================

function switchTab(tabName) {
  document.querySelectorAll(".tab-view").forEach(function (el) {
    el.classList.remove("active");
  });
  var target = document.getElementById("tab-" + tabName);
  if (target) target.classList.add("active");

  document.querySelectorAll(".nav-item").forEach(function (el) {
    el.classList.remove("active");
  });
  var navBtn = document.getElementById("nav-" + tabName);
  if (navBtn) navBtn.classList.add("active");

  // render notes when switching to notes tab
  if (tabName === "notes") renderNotesTab();
  // load data when switching to tools (小窝) tab
  if (tabName === "tools") {
    loadMemoryData();
    loadBotActivity();
    loadNoteBox();
    loadReadingProgress();
    loadPetHouse();
  }
  // load footprints when switching to footprints tab
  if (tabName === "footprints") loadFootprints();
  // load stats on home tab
  if (tabName === "home") {
    loadStats();
  }
  // load connection info on settings tab
  if (tabName === "settings") {
    loadConnectionInfo();
    // Bind cache management buttons
    var clearCacheBtn = document.getElementById("btn-clear-cache");
    if (clearCacheBtn && !clearCacheBtn._bound) {
      clearCacheBtn._bound = true;
      clearCacheBtn.addEventListener("click", clearCacheAndReload);
    }
    var clearLsBtn = document.getElementById("btn-clear-localstorage");
    if (clearLsBtn && !clearLsBtn._bound) {
      clearLsBtn._bound = true;
      clearLsBtn.addEventListener("click", clearLocalStorage);
    }
  }
}

// ==================== Events ====================

function bindEvents() {
  // avatar
  document.getElementById("bot-avatar-wrapper").addEventListener("click", function () {
    document.getElementById("bot-avatar-input").click();
  });
  document.getElementById("user-avatar-wrapper").addEventListener("click", function () {
    document.getElementById("user-avatar-input").click();
  });
  document.getElementById("bot-avatar-input").addEventListener("change", function (e) {
    handleAvatarChange("bot", e.target.files[0]); e.target.value = "";
  });
  document.getElementById("user-avatar-input").addEventListener("change", function (e) {
    handleAvatarChange("user", e.target.files[0]); e.target.value = "";
  });

  // nickname
  document.getElementById("btn-edit-bot-nickname").addEventListener("click", function () {
    var el = document.getElementById("bot-nickname");
    if (el.classList.contains("editing")) finishNicknameEdit("bot");
    else startNicknameEdit("bot");
  });
  document.getElementById("btn-edit-user-nickname").addEventListener("click", function () {
    var el = document.getElementById("user-nickname");
    if (el.classList.contains("editing")) finishNicknameEdit("user");
    else startNicknameEdit("user");
  });

  ["bot", "user"].forEach(function (who) {
    var el = document.getElementById(who + "-nickname");
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); finishNicknameEdit(who); }
      if (e.key === "Escape") {
        var key = who === "user" ? USER_NICKNAME_KEY : BOT_NICKNAME_KEY;
        el.textContent = localStorage.getItem(key) || (who === "bot" ? "沈星回" : "你");
        finishNicknameEdit(who);
      }
    });
    el.addEventListener("blur", function () {
      if (el.classList.contains("editing")) finishNicknameEdit(who);
    });
  });

  // upload
  document.getElementById("btn-upload").addEventListener("click", function () {
    document.getElementById("file-input").click();
  });
  document.getElementById("file-input").addEventListener("change", function (e) {
    uploadBook(e.target.files[0]);
    e.target.value = "";
  });

  // shelf nav arrows
  document.getElementById("btn-prev-page").addEventListener("click", function () {
    goToPage(state.currentPage - 1);
  });
  document.getElementById("btn-next-page").addEventListener("click", function () {
    goToPage(state.currentPage + 1);
  });

  // bottom nav tabs
  document.querySelectorAll(".nav-item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tab = btn.dataset.tab;
      if (tab) switchTab(tab);
    });
  });

  // set home as active on load
  switchTab("home");

  // Footprints sub-tab switching
  document.querySelectorAll(".fp-tab-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      document.querySelectorAll(".fp-tab-btn").forEach(function(b) { b.classList.remove("active"); });
      document.querySelectorAll(".fp-panel").forEach(function(p) { p.classList.remove("active"); });
      btn.classList.add("active");
      var panel = document.getElementById("fp-" + btn.dataset.fptab);
      if (panel) panel.classList.add("active");
      // load content for the selected sub-tab
      if (btn.dataset.fptab === "photos") loadFootprints();
      else if (btn.dataset.fptab === "notes") loadFpNotes();
      else if (btn.dataset.fptab === "moments") loadFpMoments();
    });
  });

  // Footprints photo upload
  var fpBtn = document.getElementById("footprints-upload-btn");
  var fpInput = document.getElementById("footprints-file-input");
  if (fpBtn && fpInput) {
    fpBtn.addEventListener("click", function() { fpInput.click(); });
    fpInput.addEventListener("change", function() {
      if (fpInput.files && fpInput.files[0]) {
        uploadFootprint(fpInput.files[0]);
        fpInput.value = "";
      }
    });
  }

  // Footprints write note
  var fpWriteBtn = document.getElementById("fp-write-note-btn");
  if (fpWriteBtn) {
    fpWriteBtn.addEventListener("click", function() {
      var text = prompt("写一张便签给他：");
      if (text && text.trim()) {
        postFpNote(text.trim());
      }
    });
  }

  // Footprints post moment
  var fpMomentBtn = document.getElementById("fp-post-moment-btn");
  if (fpMomentBtn) {
    fpMomentBtn.addEventListener("click", function() {
      var text = prompt("发一条动态：");
      if (text && text.trim()) {
        postUserMoment(text.trim());
      }
    });
  }

  // reader events
  bindReaderEvents();
}

// ==================== Reader ====================

var readerState = {
  bookId: null,
  bookTitle: "",
  chapters: [],
  currentChapter: 0,
  chatOpen: false,
  scrollPercent: 0,
  bookmarks: {},       // { "bookId:chapterIndex": percent }
  completedChapters: {}, // { "bookId:chapterIndex": true }
};

function openReader(bookId) {
  readerState.bookId = bookId;
  var book = state.books.find(function (b) { return b.id === bookId; });
  readerState.bookTitle = book ? book.title : "";

  // start/resume session for this book
  state.currentBookId = bookId;
  state.currentBookTitle = readerState.bookTitle;
  localStorage.setItem("shared_read_progress_selected_book", bookId);
  startSession(bookId);

  // clear chat and load this book's history
  loadChatHistory(bookId);

  // show reader, hide main content + bottom nav
  document.getElementById("reader-view").classList.add("open");
  document.querySelector(".page-content").style.display = "none";
  document.querySelector(".bottom-nav").style.display = "none";

  loadChapters(bookId);
}

async function loadChatHistory(bookId) {
  var container = document.getElementById("chat-bubble-messages");
  container.innerHTML = '<div class="chat-bubble-welcome">划线或留言，我都在 ✦</div>';

  if (!bookId) return;

  try {
    var res = await apiGet("chat/history?book_id=" + encodeURIComponent(bookId));
    if (res.success && res.messages && res.messages.length > 0) {
      // filter out silent system messages (highlights, etc.)
      var visibleMessages = res.messages.filter(function (msg) {
        if (msg.metadata && msg.metadata.silent) return false;
        if (msg.content && msg.content.indexOf("[系统提示]") === 0) return false;
        return true;
      });
      if (visibleMessages.length > 0) {
        container.innerHTML = "";
        visibleMessages.forEach(function (msg) {
          appendChatMsg(msg.role === "user" ? "user" : "bot", msg.content);
        });
      }
    }
  } catch (e) {
    console.error("Failed to load chat history:", e);
  }
}

function closeReader() {
  document.getElementById("reader-view").classList.remove("open");
  document.querySelector(".page-content").style.display = "";
  document.querySelector(".bottom-nav").style.display = "";
  closeSidebar();
  closeChatBubble();
}

async function loadChapters(bookId) {
  try {
    var result = await apiGet("books/" + bookId + "/chapters");
    if (result.success) {
      readerState.chapters = result.chapters;
      renderChapterList();
      var saved = getReadingProgress(bookId);
      var startChapter = saved && saved.chapter < result.chapters.length ? saved.chapter : 0;
      if (result.chapters.length > 0) loadChapterContent(startChapter);
    }
  } catch (e) {
    document.getElementById("reader-body").innerHTML = '<p class="reader-placeholder">加载失败</p>';
  }
}

function renderChapterList() {
  var list = document.getElementById("reader-chapter-list");
  list.innerHTML = readerState.chapters.map(function (ch, i) {
    return '<li data-index="' + i + '"' + (i === readerState.currentChapter ? ' class="active"' : '') + '>' +
      escapeHtml(ch.title) + '</li>';
  }).join("");

  list.querySelectorAll("li").forEach(function (li) {
    li.addEventListener("click", function () {
      loadChapterContent(parseInt(li.dataset.index));
      closeSidebar();
    });
  });
}

async function loadChapterContent(index) {
  readerState.currentChapter = index;
  var ch = readerState.chapters[index];
  document.getElementById("reader-chapter-name").textContent = ch ? ch.title : "";

  document.querySelectorAll(".reader-chapter-list li").forEach(function (li) {
    li.classList.toggle("active", parseInt(li.dataset.index) === index);
  });

  var prevBtn = document.getElementById("reader-prev-ch");
  var nextBtn = document.getElementById("reader-next-ch");
  prevBtn.style.visibility = index > 0 ? "visible" : "hidden";
  nextBtn.style.visibility = index < readerState.chapters.length - 1 ? "visible" : "hidden";

  try {
    var result = await apiGet("books/" + readerState.bookId + "/chapters/" + index);
    if (result.success) {
      document.getElementById("reader-body").innerHTML = result.content;
      document.getElementById("reader-body").scrollTop = 0;
      readerState.scrollPercent = 0;
      applyHighlights();
      updateBookmarkButton();
      updateCheckinButton();
      // Append review section at the end of chapter
      renderChapterReviews(readerState.bookId, index);
    }
  } catch (e) {
    document.getElementById("reader-body").innerHTML = '<p class="reader-placeholder">加载失败</p>';
  }

  saveReadingProgress();

  // report user progress to backend
  var book = state.books.find(function (b) { return b.id === readerState.bookId; });
  apiPost("user-progress/report", {
    book_id: readerState.bookId,
    chapter_index: index,
    total_chapters: readerState.chapters.length,
    book_title: book ? book.title : "",
  }).catch(function () {});
}

// Sidebar
function openSidebar() {
  document.getElementById("reader-sidebar").classList.add("open");
  document.getElementById("reader-sidebar-overlay").classList.add("open");
}

function closeSidebar() {
  document.getElementById("reader-sidebar").classList.remove("open");
  document.getElementById("reader-sidebar-overlay").classList.remove("open");
}

// Chat bubble
function toggleChatBubble() {
  var panel = document.getElementById("chat-bubble-panel");
  var fab = document.getElementById("chat-fab");
  readerState.chatOpen = !readerState.chatOpen;

  if (readerState.chatOpen) {
    positionChatPanel();
    panel.classList.add("open");
    fab.classList.add("hidden");
  } else {
    closeChatBubble();
  }
}

function closeChatBubble() {
  readerState.chatOpen = false;
  document.getElementById("chat-bubble-panel").classList.remove("open");
  document.getElementById("chat-fab").classList.remove("hidden");
}

function positionChatPanel() {
  var fab = document.getElementById("chat-fab");
  var panel = document.getElementById("chat-bubble-panel");
  var fabRect = fab.getBoundingClientRect();
  var panelW = 300;
  var panelH = 420;

  var left = fabRect.left + fabRect.width / 2 - panelW / 2;
  var top = fabRect.top - panelH - 12;

  if (left < 8) left = 8;
  if (left + panelW > window.innerWidth - 8) left = window.innerWidth - panelW - 8;
  if (top < 8) {
    top = fabRect.bottom + 12;
  }
  if (top + panelH > window.innerHeight - 8) {
    top = window.innerHeight - panelH - 8;
  }

  panel.style.left = left + "px";
  panel.style.top = top + "px";
}

// Make chat panel draggable by its header
function initDraggableChatPanel() {
  var panel = document.getElementById("chat-bubble-panel");
  var handle = document.getElementById("chat-bubble-drag-handle");
  if (!handle || !panel) return;

  var isDragging = false;
  var dragTimeout = null;
  var startX, startY, panelStartX, panelStartY;

  function onPointerDown(e) {
    if (e.target.closest(".chat-bubble-close")) return;

    dragTimeout = setTimeout(function () {
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      var rect = panel.getBoundingClientRect();
      panelStartX = rect.left;
      panelStartY = rect.top;
      handle.style.cursor = "grabbing";
      e.preventDefault();
    }, 300);
  }

  function onPointerMove(e) {
    if (!isDragging) return;
    e.preventDefault();
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;
    var newLeft = panelStartX + dx;
    var newTop = panelStartY + dy;

    var maxLeft = window.innerWidth - panel.offsetWidth - 4;
    var maxTop = window.innerHeight - panel.offsetHeight - 4;
    newLeft = Math.max(4, Math.min(newLeft, maxLeft));
    newTop = Math.max(4, Math.min(newTop, maxTop));

    panel.style.left = newLeft + "px";
    panel.style.top = newTop + "px";
  }

  function onPointerUp() {
    if (dragTimeout) { clearTimeout(dragTimeout); dragTimeout = null; }
    isDragging = false;
    handle.style.cursor = "grab";
  }

  handle.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
}

// Draggable FAB
function initDraggableFab() {
  var fab = document.getElementById("chat-fab");
  var isDragging = false;
  var wasDragged = false;
  var startX, startY, fabX, fabY;

  fabX = window.innerWidth - 72;
  fabY = window.innerHeight - 80;
  fab.style.left = fabX + "px";
  fab.style.top = fabY + "px";

  function onStart(clientX, clientY) {
    isDragging = true;
    wasDragged = false;
    startX = clientX - fabX;
    startY = clientY - fabY;
    fab.style.transition = "none";
  }

  function onMove(clientX, clientY) {
    if (!isDragging) return;
    wasDragged = true;
    fabX = clientX - startX;
    fabY = clientY - startY;

    fabX = Math.max(0, Math.min(window.innerWidth - 48, fabX));
    fabY = Math.max(0, Math.min(window.innerHeight - 48, fabY));

    fab.style.left = fabX + "px";
    fab.style.top = fabY + "px";
  }

  function onEnd() {
    isDragging = false;
    fab.style.transition = "";

    var midX = window.innerWidth / 2;
    if (fabX + 24 < midX) {
      fabX = 16;
    } else {
      fabX = window.innerWidth - 64;
    }
    fab.style.left = fabX + "px";

    if (!wasDragged) {
      toggleChatBubble();
    }
  }

  fab.addEventListener("mousedown", function (e) {
    e.preventDefault();
    onStart(e.clientX, e.clientY);
  });
  document.addEventListener("mousemove", function (e) {
    onMove(e.clientX, e.clientY);
  });
  document.addEventListener("mouseup", function () {
    if (isDragging) onEnd();
  });

  fab.addEventListener("touchstart", function (e) {
    var t = e.touches[0];
    onStart(t.clientX, t.clientY);
  }, { passive: true });
  document.addEventListener("touchmove", function (e) {
    if (!isDragging) return;
    var t = e.touches[0];
    onMove(t.clientX, t.clientY);
  }, { passive: true });
  document.addEventListener("touchend", function () {
    if (isDragging) onEnd();
  }, { passive: true });

  window.addEventListener("resize", function () {
    fabX = Math.min(fabX, window.innerWidth - 64);
    fabY = Math.min(fabY, window.innerHeight - 64);
    fab.style.left = fabX + "px";
    fab.style.top = fabY + "px";
  });
}

async function sendChatMessage() {
  var textarea = document.getElementById("chat-bubble-textarea");
  var content = textarea.value.trim();
  if (!content) return;

  textarea.value = "";
  appendChatMsg("user", content);

  var thinkingId = appendChatMsg("bot", "", "thinking");

  // insert typing dots into the thinking bubble
  var thinkingEl = document.getElementById(thinkingId);
  if (thinkingEl) {
    thinkingEl.innerHTML = '<span class="typing-dots"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></span>';
  }

  try {
    var bookId = readerState.bookId || state.currentBookId;
    var body = {
      book_id: bookId,
      content: content,
      chapter_index: readerState.currentChapter,
      scroll_percent: readerState.scrollPercent || 0,
    };
    // include bookmark if set for current chapter
    var bmKey = bookId + ":" + readerState.currentChapter;
    if (readerState.bookmarks[bmKey] !== undefined) {
      body.bookmark_percent = readerState.bookmarks[bmKey];
    }
    var result = await apiPost("chat/send", body);
    removeChatMsg(thinkingId);
    if (result.success && result.reply) {
      appendChatMsg("bot", result.reply);
    } else {
      appendChatMsg("bot", "（没有收到回复）");
    }
  } catch (e) {
    removeChatMsg(thinkingId);
    appendChatMsg("bot", "（网络不太好，等会儿再试~）");
  }
}

function appendChatMsg(role, text, extraClass) {
  var container = document.getElementById("chat-bubble-messages");
  var welcome = container.querySelector(".chat-bubble-welcome");
  if (welcome) welcome.remove();

  // split bot messages by separator into multiple bubbles
  if (role === "bot" && !extraClass && state.messageSeparator && text) {
    var segments = text.split(state.messageSeparator).map(function (s) { return s.trim(); }).filter(function (s) { return s.length > 0; });
    if (segments.length > 1) {
      var lastId = null;
      segments.forEach(function (seg) {
        lastId = _appendSingleBubble(container, role, seg, extraClass);
      });
      return lastId;
    }
  }

  return _appendSingleBubble(container, role, text, extraClass);
}

function _appendSingleBubble(container, role, text, extraClass) {
  var div = document.createElement("div");
  div.className = "chat-msg " + role + (extraClass ? " " + extraClass : "");
  var msgId = "msg-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
  div.id = msgId;
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return msgId;
}

function removeChatMsg(msgId) {
  if (!msgId) return;
  var el = document.getElementById(msgId);
  if (el) el.remove();
}

// Bind reader events
function bindReaderEvents() {
  document.getElementById("reader-back-btn").addEventListener("click", closeReader);
  document.getElementById("reader-sidebar-toggle").addEventListener("click", openSidebar);
  document.getElementById("reader-sidebar-close").addEventListener("click", closeSidebar);
  document.getElementById("reader-sidebar-overlay").addEventListener("click", closeSidebar);

  document.getElementById("reader-prev-ch").addEventListener("click", function () {
    if (readerState.currentChapter > 0) loadChapterContent(readerState.currentChapter - 1);
  });
  document.getElementById("reader-next-ch").addEventListener("click", function () {
    if (readerState.currentChapter < readerState.chapters.length - 1) loadChapterContent(readerState.currentChapter + 1);
  });

  document.getElementById("chat-fab").addEventListener("click", function (e) {
    // only toggle if not dragged (handled in initDraggableFab)
  });
  document.getElementById("chat-bubble-close").addEventListener("click", closeChatBubble);
  document.getElementById("chat-bubble-send").addEventListener("click", sendChatMessage);
  document.getElementById("chat-bubble-textarea").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
  });

  // highlight button
  document.getElementById("reader-highlight-btn").addEventListener("click", doHighlight);

  // bookmark button
  document.getElementById("reader-bookmark-btn").addEventListener("click", doBookmark);

  // checkin button
  document.getElementById("reader-checkin-btn").addEventListener("click", doCheckin);

  // track scroll position in reader body
  var readerBody = document.getElementById("reader-body");
  readerBody.addEventListener("scroll", function () {
    var el = readerBody;
    var scrollable = el.scrollHeight - el.clientHeight;
    if (scrollable > 0) {
      readerState.scrollPercent = Math.round((el.scrollTop / scrollable) * 100);
    } else {
      readerState.scrollPercent = 0;
    }
    // update reading overlay if bookmark is active
    var key = readerState.bookId + ":" + readerState.currentChapter;
    if (readerState.bookmarks[key] !== undefined) {
      updateReadingOverlay();
    }
  });

  // restore bookmarks and completed chapters from localStorage
  try {
    var savedBm = localStorage.getItem("shared_read_bookmarks");
    if (savedBm) readerState.bookmarks = JSON.parse(savedBm);
  } catch (e) {}
  try {
    var savedComp = localStorage.getItem("shared_read_completed");
    if (savedComp) readerState.completedChapters = JSON.parse(savedComp);
  } catch (e) {}

  initDraggableFab();
  initDraggableChatPanel();

  // === Selection context menu (floating highlight button) ===
  var selMenu = document.createElement("div");
  selMenu.id = "selection-context-menu";
  selMenu.className = "selection-context-menu";
  selMenu.innerHTML = '<button class="sel-menu-btn" id="sel-menu-highlight">✦ 划线</button>';
  selMenu.style.display = "none";
  document.body.appendChild(selMenu);

  selMenu.querySelector("#sel-menu-highlight").addEventListener("click", function (e) {
    e.stopPropagation();
    doHighlight();
    selMenu.style.display = "none";
  });

  // Show menu on text selection (works for both mouse and touch)
  var readerBodyEl = document.getElementById("reader-body");

  function showSelectionMenu() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      selMenu.style.display = "none";
      return;
    }
    // Only show if selection is inside reader body
    var anchor = sel.anchorNode;
    if (!anchor || !readerBodyEl.contains(anchor)) {
      selMenu.style.display = "none";
      return;
    }
    // Cache selection text for iOS (clicking button clears selection)
    window._cachedSelectionText = sel.toString().trim();

    var range = sel.getRangeAt(0);
    var rect = range.getBoundingClientRect();
    // Position above the selection (fixed positioning relative to viewport)
    selMenu.style.display = "block";
    var menuRect = selMenu.getBoundingClientRect();
    var top = rect.top - menuRect.height - 8;
    if (top < 10) top = rect.bottom + 8; // below if no room above
    var left = Math.max(10, rect.left + rect.width / 2 - menuRect.width / 2);
    selMenu.style.left = left + "px";
    selMenu.style.top = top + "px";
  }

  // Desktop: show on mouseup after selection (use document level, filter by reader-body)
  document.addEventListener("mouseup", function () {
    if (!document.getElementById("reader-view").classList.contains("open")) return;
    setTimeout(showSelectionMenu, 50);
  });

  // Desktop: right-click with selection
  document.addEventListener("contextmenu", function (e) {
    if (!document.getElementById("reader-view").classList.contains("open")) return;
    var sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) {
      var anchor = sel.anchorNode;
      if (anchor && document.getElementById("reader-body").contains(anchor)) {
        e.preventDefault();
        showSelectionMenu();
      }
    }
  });

  // Mobile: show on selectionchange + cache text for iOS
  document.addEventListener("selectionchange", function () {
    if (!document.getElementById("reader-view").classList.contains("open")) return;
    // cache selection text immediately (for iOS where clicking buttons clears selection)
    var sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) {
      var anchor = sel.anchorNode;
      if (anchor && document.getElementById("reader-body").contains(anchor)) {
        window._cachedSelectionText = sel.toString().trim();
      }
    }
    setTimeout(showSelectionMenu, 100);
  });

  // Hide menu when clicking elsewhere
  document.addEventListener("mousedown", function (e) {
    if (!selMenu.contains(e.target)) {
      selMenu.style.display = "none";
    }
  });
}

// ==================== Bookmark ====================

function doBookmark() {
  if (!readerState.bookId) return;
  var key = readerState.bookId + ":" + readerState.currentChapter;
  var btn = document.getElementById("reader-bookmark-btn");

  if (readerState.bookmarks[key] !== undefined) {
    // clear bookmark
    delete readerState.bookmarks[key];
    btn.classList.remove("active");
    btn.textContent = "📌";
    showBookmarkToast("书签已取消");
    removeReadingOverlay();
  } else {
    // set bookmark at current scroll position
    readerState.bookmarks[key] = readerState.scrollPercent;
    btn.classList.add("active");
    btn.textContent = "📌 已标记";
    showBookmarkToast("书签已放置 · 高亮区域为传入内容");
    updateReadingOverlay();
  }
  localStorage.setItem("shared_read_bookmarks", JSON.stringify(readerState.bookmarks));
}

function updateBookmarkButton() {
  var btn = document.getElementById("reader-bookmark-btn");
  if (!btn) return;
  var key = readerState.bookId + ":" + readerState.currentChapter;
  if (readerState.bookmarks[key] !== undefined) {
    btn.classList.add("active");
    btn.textContent = "📌 已标记";
    updateReadingOverlay();
  } else {
    btn.classList.remove("active");
    btn.textContent = "📌";
    removeReadingOverlay();
  }
}

function showBookmarkToast(text) {
  var el = document.getElementById("capsule-toast");
  var textEl = el.querySelector(".capsule-text");
  var oldText = textEl.textContent;
  textEl.textContent = text;
  el.classList.add("show");
  setTimeout(function () {
    el.classList.remove("show");
    textEl.textContent = oldText;
  }, 2500);
}

// ==================== Reading Overlay (visual indicator) ====================

function updateReadingOverlay() {
  var body = document.getElementById("reader-body");
  if (!body || !readerState.bookId) return;

  var key = readerState.bookId + ":" + readerState.currentChapter;
  var bookmarkPct = readerState.bookmarks[key];
  if (bookmarkPct === undefined) {
    removeReadingOverlay();
    return;
  }

  var scrollable = body.scrollHeight - body.clientHeight;
  var bookmarkPx = scrollable > 0 ? (bookmarkPct / 100) * scrollable : 0;
  var currentPx = body.scrollTop + body.clientHeight;

  // overlay covers from bookmark position to current viewport bottom
  var top = bookmarkPx;
  var height = currentPx - bookmarkPx;
  if (height < 0) height = 0;

  var overlay = body.querySelector(".reading-window-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "reading-window-overlay";
    body.style.position = "relative";
    body.appendChild(overlay);
  }

  overlay.style.top = top + "px";
  overlay.style.height = height + "px";
}

function removeReadingOverlay() {
  var body = document.getElementById("reader-body");
  if (!body) return;
  var overlay = body.querySelector(".reading-window-overlay");
  if (overlay) overlay.remove();
}

// ==================== Chapter Checkin (打卡) ====================

function doCheckin() {
  if (!readerState.bookId) return;
  var key = readerState.bookId + ":" + readerState.currentChapter;
  var btn = document.getElementById("reader-checkin-btn");

  if (readerState.completedChapters[key]) return; // already completed

  btn.disabled = true;

  apiPost("chapter/complete", {
    book_id: readerState.bookId,
    chapter_index: readerState.currentChapter,
  }).then(function (res) {
    if (res.success) {
      readerState.completedChapters[key] = true;
      localStorage.setItem("shared_read_completed", JSON.stringify(readerState.completedChapters));
      btn.classList.add("completed");
      btn.textContent = "✓ 已打卡";
      showCheckinToast();
    } else {
      btn.disabled = false;
    }
  }).catch(function () {
    btn.disabled = false;
  });
}

function updateCheckinButton() {
  var btn = document.getElementById("reader-checkin-btn");
  if (!btn) return;
  var key = readerState.bookId + ":" + readerState.currentChapter;
  if (readerState.completedChapters[key]) {
    btn.classList.add("completed");
    btn.textContent = "✓ 已打卡";
    btn.disabled = true;
  } else {
    btn.classList.remove("completed");
    btn.textContent = "✓ 打卡";
    btn.disabled = false;
  }
}

function showCheckinToast() {
  // reuse capsule toast mechanism
  var el = document.getElementById("capsule-toast");
  var textEl = el.querySelector(".capsule-text");
  var oldText = textEl.textContent;
  textEl.textContent = "打卡成功 ✦";
  el.classList.add("show");
  setTimeout(function () {
    el.classList.remove("show");
    textEl.textContent = oldText;
  }, 3000);
}

// ==================== Chapter Reviews ====================

/**
 * Render the review section at the bottom of the chapter content.
 */
async function renderChapterReviews(bookId, chapterIndex) {
  var body = document.getElementById("reader-body");
  if (!body) return;

  // Create review section container
  var section = document.createElement("div");
  section.className = "chapter-review-section";
  section.innerHTML =
    '<div class="chapter-review-header">' +
      '<span class="chapter-review-title">✦ 书评区</span>' +
    '</div>' +
    '<div class="chapter-review-list" id="chapter-review-list"></div>' +
    '<div class="chapter-review-input">' +
      '<textarea class="chapter-review-textarea" id="chapter-review-textarea" placeholder="写下你对这一章的感想..." rows="3"></textarea>' +
      '<button class="chapter-review-submit" id="chapter-review-submit">发表书评</button>' +
    '</div>';

  body.appendChild(section);

  // Load existing reviews for this chapter
  var userNick = localStorage.getItem(USER_NICKNAME_KEY) || "我";
  var botNick = localStorage.getItem(BOT_NICKNAME_KEY) || "Bot";
  try {
    var res = await apiGet("data/" + bookId + "/reviews");
    if (res.success && res.reviews) {
      var chapterReviews = res.reviews.filter(function(r) {
        return r.chapter_index === chapterIndex;
      });
      var list = document.getElementById("chapter-review-list");
      if (chapterReviews.length > 0) {
        list.innerHTML = chapterReviews.map(function(r) {
          return buildReviewCardHtml(r, userNick, botNick);
        }).join("");
      }
    }
  } catch(e) {}

  // Bind submit button
  var submitBtn = document.getElementById("chapter-review-submit");
  var textarea = document.getElementById("chapter-review-textarea");
  if (submitBtn && textarea) {
    submitBtn.addEventListener("click", async function() {
      var content = textarea.value.trim();
      if (!content) return;

      submitBtn.disabled = true;
      submitBtn.textContent = "发送中...";
      textarea.value = "";

      // Immediately render user's review (before waiting for bot reply)
      var list = document.getElementById("chapter-review-list");
      var tempId = "review-pending-" + Date.now();
      var timeStr = new Date().toLocaleString();
      var pendingCard = '<div class="chapter-review-card" id="' + tempId + '">' +
        '<div class="review-card-user">' +
          '<span class="review-card-name">' + escapeHtml(userNick) + '</span>' +
          '<span class="review-card-time">' + timeStr + '</span>' +
          '<div class="review-card-text">' + escapeHtml(content) + '</div>' +
        '</div>' +
        '<div class="review-card-bot">' +
          '<span class="review-card-name">' + escapeHtml(botNick) + '</span>' +
          '<div class="review-card-text review-bot-pending">···</div>' +
        '</div>' +
      '</div>';
      list.innerHTML += pendingCard;

      // Send to server (bot reply comes back in response)
      try {
        var res = await apiPost("interact/review", {
          session_token: "local",
          book_id: bookId,
          chapter_index: chapterIndex,
          content: content
        });

        // Update the pending card with bot's actual reply
        var cardEl = document.getElementById(tempId);
        if (cardEl) {
          var botTextEl = cardEl.querySelector(".review-bot-pending");
          if (botTextEl && res.success && res.bot_reply) {
            botTextEl.textContent = res.bot_reply;
            botTextEl.classList.remove("review-bot-pending");
          } else if (botTextEl) {
            botTextEl.textContent = "（暂无回复）";
            botTextEl.classList.remove("review-bot-pending");
          }
        }
      } catch(e) {
        var cardEl = document.getElementById(tempId);
        if (cardEl) {
          var botTextEl = cardEl.querySelector(".review-bot-pending");
          if (botTextEl) {
            botTextEl.textContent = "（回复失败）";
            botTextEl.classList.remove("review-bot-pending");
          }
        }
      }

      submitBtn.disabled = false;
      submitBtn.textContent = "发表书评";
    });
  }
}

/**
 * Build HTML for a single review card (user + bot side by side).
 */
function buildReviewCardHtml(review, userNick, botNick) {
  var timeStr = review.created_at ? new Date(review.created_at * 1000).toLocaleString() : "";
  var botReplyHtml = review.bot_reply
    ? '<div class="review-card-text">' + escapeHtml(review.bot_reply) + '</div>'
    : '<div class="review-card-text review-card-empty">（暂无回复）</div>';

  return '<div class="chapter-review-card">' +
    '<div class="review-card-user">' +
      '<span class="review-card-name">' + escapeHtml(userNick) + '</span>' +
      '<span class="review-card-time">' + timeStr + '</span>' +
      '<div class="review-card-text">' + escapeHtml(review.content) + '</div>' +
    '</div>' +
    '<div class="review-card-bot">' +
      '<span class="review-card-name">' + escapeHtml(botNick) + '</span>' +
      botReplyHtml +
    '</div>' +
  '</div>';
}

// ==================== Reading Progress ====================

function saveReadingProgress() {
  if (!readerState.bookId) return;
  var data = { chapter: readerState.currentChapter, totalChapters: readerState.chapters.length };
  localStorage.setItem(PROGRESS_PREFIX + readerState.bookId, JSON.stringify(data));
  updateHomeProgress();
}

function getReadingProgress(bookId) {
  var raw = localStorage.getItem(PROGRESS_PREFIX + bookId);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (e) { return null; }
}

function updateHomeProgress() {
  var select = document.getElementById("progress-book-select");
  var bookId = select ? select.value : "";
  if (!bookId) {
    document.getElementById("user-progress").style.width = "0%";
    document.getElementById("bot-progress").style.width = "0%";
    return;
  }
  // fetch user progress from backend
  apiGet("user-progress/" + bookId).then(function (res) {
    if (res && res.success) {
      document.getElementById("user-progress").style.width = res.percent + "%";
    } else {
      document.getElementById("user-progress").style.width = "0%";
    }
  }).catch(function () {
    // fallback to localStorage if backend unavailable
    var progress = getReadingProgress(bookId);
    if (progress && progress.totalChapters > 0) {
      var pct = Math.round(((progress.chapter + 1) / progress.totalChapters) * 100);
      document.getElementById("user-progress").style.width = pct + "%";
    } else {
      document.getElementById("user-progress").style.width = "0%";
    }
  });
  apiGet("bot-progress/" + bookId).then(function (res) {
    if (res && res.success) {
      document.getElementById("bot-progress").style.width = res.percent + "%";
    }
  }).catch(function () {
    document.getElementById("bot-progress").style.width = "0%";
  });
}

function populateProgressBookSelect() {
  var select = document.getElementById("progress-book-select");
  var books = state.books;
  select.innerHTML = '<option value="">选择书籍</option>' +
    books.map(function (b) {
      return '<option value="' + b.id + '">' + escapeHtml(b.title) + '</option>';
    }).join("");

  var lastBook = localStorage.getItem("shared_read_progress_selected_book");
  if (lastBook && books.find(function (b) { return b.id === lastBook; })) {
    select.value = lastBook;
  }
  updateHomeProgress();

  select.addEventListener("change", function () {
    localStorage.setItem("shared_read_progress_selected_book", select.value);
    if (select.value) {
      state.currentBookId = select.value;
      var book = books.find(function (b) { return b.id === select.value; });
      state.currentBookTitle = book ? book.title : "";
      startSession(select.value);
    }
    updateHomeProgress();
  });
}

// ==================== Highlights ====================

function getAllHighlights() {
  var raw = localStorage.getItem(HIGHLIGHTS_KEY);
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { return {}; }
}

function saveHighlight(bookId, chapterIndex, text, chapterTitle) {
  var all = getAllHighlights();
  if (!all[bookId]) all[bookId] = [];

  var context = "";
  var sel = window.getSelection();
  if (sel && sel.rangeCount > 0) {
    var range = sel.getRangeAt(0);
    var container = range.commonAncestorContainer;
    var parentEl = container.nodeType === 3 ? container.parentElement : container;
    if (parentEl) context = (parentEl.textContent || "").trim().substring(0, 200);
  }

  all[bookId].push({
    chapter: chapterIndex,
    chapterTitle: chapterTitle || ("第" + (chapterIndex + 1) + "章"),
    text: text,
    context: context,
    time: Date.now(),
  });
  localStorage.setItem(HIGHLIGHTS_KEY, JSON.stringify(all));
}

function doHighlight() {
  // On iOS, clicking a button clears the selection. Use cached selection if available.
  var text = "";
  var sel = window.getSelection();
  if (sel && !sel.isCollapsed) {
    text = sel.toString().trim();
  }
  // fallback to cached selection (set by selectionchange/mouseup)
  if (!text && window._cachedSelectionText) {
    text = window._cachedSelectionText;
  }
  if (!text) return;

  // clear cache after use
  window._cachedSelectionText = "";

  var range = sel && !sel.isCollapsed ? sel.getRangeAt(0) : null;
  var container = range ? range.commonAncestorContainer : null;
  var markEl = container ? (container.nodeType === 3 ? container.parentElement : container) : null;

  while (markEl && markEl !== document.getElementById("reader-body")) {
    if (markEl.tagName === "MARK" && markEl.classList.contains("user-highlight")) {
      var highlightText = markEl.textContent;
      var parent = markEl.parentNode;
      while (markEl.firstChild) parent.insertBefore(markEl.firstChild, markEl);
      parent.removeChild(markEl);
      parent.normalize();

      removeHighlight(readerState.bookId, readerState.currentChapter, highlightText);
      sel.removeAllRanges();
      return;
    }
    markEl = markEl.parentElement;
  }

  var chTitle = readerState.chapters[readerState.currentChapter]
    ? readerState.chapters[readerState.currentChapter].title
    : "";
  saveHighlight(readerState.bookId, readerState.currentChapter, text, chTitle);

  var all = getAllHighlights();
  var bookH = all[readerState.bookId] || [];
  var dupeCount = bookH.filter(function (h) {
    return h.chapter === readerState.currentChapter && h.text === text;
  }).length;
  if (dupeCount > 1) {
    removeHighlight(readerState.bookId, readerState.currentChapter, text);
    sel.removeAllRanges();
    return;
  }

  try {
    var newRange = sel.getRangeAt(0);
    var mark = document.createElement("mark");
    mark.className = "user-highlight";
    newRange.surroundContents(mark);
  } catch (e) {
    // complex selections may fail
  }

  // notify backend about the highlight so bot knows
  var contextText = "";
  try {
    var parentEl = mark && mark.parentElement;
    if (parentEl) contextText = (parentEl.textContent || "").trim().substring(0, 200);
  } catch (e) {}
  apiPost("interact/highlight", {
    session_token: "local",
    book_id: readerState.bookId,
    chapter_index: readerState.currentChapter,
    text: text,
    context: contextText,
    chapter_title: readerState.chapters[readerState.currentChapter]
      ? readerState.chapters[readerState.currentChapter].title
      : "",
  }).catch(function () {});

  sel.removeAllRanges();
}

function removeHighlight(bookId, chapterIndex, text) {
  var all = getAllHighlights();
  if (!all[bookId]) return;

  var idx = -1;
  for (var i = 0; i < all[bookId].length; i++) {
    if (all[bookId][i].chapter === chapterIndex && all[bookId][i].text === text) {
      idx = i;
      break;
    }
  }
  if (idx >= 0) {
    all[bookId].splice(idx, 1);
    if (all[bookId].length === 0) delete all[bookId];
    localStorage.setItem(HIGHLIGHTS_KEY, JSON.stringify(all));
    // sync deletion to server
    apiDelete("data/" + bookId + "/highlights", {text: text, chapter_index: chapterIndex});
  }
}

function removeHighlightByIndex(bookId, highlightIndex) {
  var all = getAllHighlights();
  if (!all[bookId] || !all[bookId][highlightIndex]) return;
  var removed = all[bookId].splice(highlightIndex, 1)[0];
  if (all[bookId].length === 0) delete all[bookId];
  localStorage.setItem(HIGHLIGHTS_KEY, JSON.stringify(all));

  if (readerState.bookId === bookId && readerState.currentChapter === removed.chapter) {
    applyHighlights();
    loadChapterContent(readerState.currentChapter);
  }
  return removed;
}

function applyHighlights() {
  var all = getAllHighlights();
  var bookHighlights = all[readerState.bookId] || [];
  var chapterHighlights = bookHighlights.filter(function (h) {
    return h.chapter === readerState.currentChapter;
  });

  if (chapterHighlights.length === 0) return;

  var body = document.getElementById("reader-body");
  var html = body.innerHTML;

  chapterHighlights.forEach(function (h) {
    var escaped = h.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex = new RegExp("(" + escaped + ")", "");
    if (regex.test(html) && html.indexOf('<mark class="user-highlight">' + h.text) === -1) {
      html = html.replace(regex, '<mark class="user-highlight">$1</mark>');
    }
  });

  body.innerHTML = html;
}

function scrollToHighlight(text) {
  var body = document.getElementById("reader-body");
  if (!body) return;
  var marks = body.querySelectorAll("mark.user-highlight");
  for (var i = 0; i < marks.length; i++) {
    if (marks[i].textContent === text) {
      marks[i].scrollIntoView({ behavior: "smooth", block: "center" });
      // brief flash effect to draw attention
      marks[i].style.outline = "2px solid var(--primary)";
      setTimeout(function () { marks[i].style.outline = ""; }, 2000);
      return;
    }
  }
}

// ==================== Notes Tab ====================

function renderNotesTab() {
  var container = document.getElementById("notes-content");
  var all = getAllHighlights();
  var bookIds = Object.keys(all).filter(function (id) {
    return all[id] && all[id].length > 0;
  });

  if (bookIds.length === 0) {
    container.innerHTML = '<div class="tab-placeholder"><span class="tab-placeholder-icon">✦</span><p>还没有划线</p><p class="tab-placeholder-hint">阅读时选中文字点击划线按钮</p></div>';
    return;
  }

  var currentNoteBook = container.dataset.currentBook || bookIds[0];
  if (!all[currentNoteBook] || all[currentNoteBook].length === 0) currentNoteBook = bookIds[0];
  container.dataset.currentBook = currentNoteBook;

  var bookTitle = function (id) {
    var b = state.books.find(function (x) { return x.id === id; });
    return b ? b.title : "未知书籍";
  };

  var selectorHtml = '<div class="notes-book-selector"><select id="notes-book-dropdown">' +
    bookIds.map(function (id) {
      return '<option value="' + id + '"' + (id === currentNoteBook ? ' selected' : '') + '>' + escapeHtml(bookTitle(id)) + '</option>';
    }).join("") + '</select></div>';

  var highlights = all[currentNoteBook] || [];
  var byChapter = {};
  highlights.forEach(function (h) {
    if (!byChapter[h.chapter]) byChapter[h.chapter] = { title: h.chapterTitle, items: [] };
    byChapter[h.chapter].items.push(h);
  });

  var cardsHtml = Object.keys(byChapter).sort(function (a, b) { return a - b; }).map(function (chIdx) {
    var group = byChapter[chIdx];
    var itemsHtml = group.items.map(function (h) {
      return '<div class="note-item" data-jump-book="' + currentNoteBook + '" data-jump-chapter="' + h.chapter + '" data-jump-text="' + escapeAttr(h.text) + '" title="点击跳转到原文">' +
        '<div class="note-item-content">' + formatHighlightWithContext(h) + '</div>' +
        '<button class="note-delete-btn" data-book-id="' + currentNoteBook + '" data-text="' + escapeAttr(h.text) + '" data-chapter="' + h.chapter + '" title="删除划线">×</button>' +
        '</div>';
    }).join("");
    return '<div class="note-card"><div class="note-card-header">第' + (parseInt(chIdx) + 1) + '章 · ' + escapeHtml(group.title) + '</div>' + itemsHtml + '</div>';
  }).join("");

  if (!cardsHtml) cardsHtml = '<div class="notes-empty">这本书还没有划线</div>';

  container.innerHTML = selectorHtml + '<div class="notes-cards">' + cardsHtml + '</div>';

  document.getElementById("notes-book-dropdown").addEventListener("change", function () {
    container.dataset.currentBook = this.value;
    renderNotesTab();
  });

  container.querySelectorAll(".note-item[data-jump-book]").forEach(function (item) {
    item.addEventListener("click", function (e) {
      // don't jump if clicking the delete button
      if (e.target.closest(".note-delete-btn")) return;
      var bookId = item.dataset.jumpBook;
      var chapter = parseInt(item.dataset.jumpChapter);
      var jumpText = item.dataset.jumpText || "";
      if (bookId && !isNaN(chapter)) {
        openReader(bookId);
        // wait for chapters to load, then jump to the specific chapter and scroll to highlight
        setTimeout(function () {
          loadChapterContent(chapter);
          if (jumpText) {
            // wait for content to render and highlights to apply, then scroll to the mark
            setTimeout(function () { scrollToHighlight(jumpText); }, 400);
          }
        }, 500);
      }
    });
  });

  container.querySelectorAll(".note-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var bookId = btn.dataset.bookId;
      var text = btn.dataset.text;
      var chapter = parseInt(btn.dataset.chapter);
      removeHighlight(bookId, chapter, text);
      if (readerState.bookId === bookId && readerState.currentChapter === chapter &&
          document.getElementById("reader-view").classList.contains("open")) {
        loadChapterContent(chapter);
      }
      renderNotesTab();
    });
  });
}

function formatHighlightWithContext(h) {
  var text = h.text;
  var context = h.context || "";
  var maxContextLen = 30;

  if (!context || context === text) {
    return '<span class="note-highlight-text">' + escapeHtml(text) + '</span>';
  }

  var idx = context.indexOf(text);
  if (idx === -1) {
    return '<span class="note-highlight-text">' + escapeHtml(text) + '</span>';
  }

  var before = context.substring(0, idx);
  var after = context.substring(idx + text.length);

  var result = "";

  if (before.length > 0) {
    if (before.length > maxContextLen) {
      before = "…" + before.substring(before.length - maxContextLen);
    }
    result += '<span class="note-text-context">' + escapeHtml(before) + '</span>';
  }

  result += '<span class="note-highlight-text">' + escapeHtml(text) + '</span>';

  if (after.length > 0) {
    if (after.length > maxContextLen) {
      after = after.substring(0, maxContextLen) + "…";
    }
    result += '<span class="note-text-context">' + escapeHtml(after) + '</span>';
  }

  return result;
}



// ==================== Footprints Board ====================

var fpPhotosState = {
  photos: [],
  currentPage: 0,
  perPage: 6
};

async function loadFootprints() {
  var board = document.getElementById("footprints-board");
  if (!board) return;

  try {
    var res = await apiGet("footprints?type=photo");
    if (!res.success) return;

    var items = (res.items || []).filter(function(i) { return i.type === "photo"; });
    if (items.length === 0) {
      // Show built-in example photo if not dismissed
      if (!localStorage.getItem("fp_example_dismissed")) {
        renderExamplePhoto(board);
      } else {
        board.innerHTML = '<div class="footprints-empty">还没有照片，贴一张开始吧 ✦</div>';
      }
      renderFootprintsPagination();
      return;
    }

    fpPhotosState.photos = items;
    var totalPages = Math.ceil(items.length / fpPhotosState.perPage);
    if (fpPhotosState.currentPage >= totalPages) {
      fpPhotosState.currentPage = Math.max(0, totalPages - 1);
    }
    renderFootprintsPage();
  } catch(e) {
    if (!localStorage.getItem("fp_example_dismissed")) {
      renderExamplePhoto(board);
    } else {
      board.innerHTML = '<div class="footprints-empty">还没有照片，贴一张开始吧 ✦</div>';
    }
    renderFootprintsPagination();
  }
}

/**
 * Render a built-in example photo on the photo wall.
 * Users can delete it (dismisses permanently via localStorage).
 * Supports drag to reposition (saved to localStorage).
 */
function renderExamplePhoto(board) {
  board.innerHTML = "";
  var div = document.createElement("div");
  div.className = "footprint-item footprint-example";
  div.dataset.itemId = "__example__";
  div.dataset.rotation = "-2";

  // Restore saved position or use default
  var savedPos = localStorage.getItem("fp_example_position");
  var posX = 30, posY = 25;
  if (savedPos) {
    try {
      var pos = JSON.parse(savedPos);
      posX = pos.x;
      posY = pos.y;
    } catch(e) {}
  }
  div.style.left = posX + "%";
  div.style.top = posY + "%";
  div.style.transform = "rotate(-2deg)";

  div.innerHTML = '<div class="footprint-pin"></div>' +
    '<div class="footprint-photo">' +
    '<img src="/static/icons/example-photo.jpg" alt="示例照片" />' +
    '<div class="footprint-caption">贴上你们的第一张照片吧 ✦</div>' +
    '</div>' +
    '<button class="footprint-delete" title="删除示例">×</button>';

  div.querySelector(".footprint-delete").addEventListener("click", function(e) {
    e.stopPropagation();
    e.preventDefault();
    localStorage.setItem("fp_example_dismissed", "1");
    localStorage.removeItem("fp_example_position");
    board.innerHTML = '<div class="footprints-empty">还没有照片，贴一张开始吧 ✦</div>';
  });

  // Enable drag for example photo (save position to localStorage instead of server)
  initExamplePhotoDrag(div);

  board.appendChild(div);
}

/**
 * Drag handler for the example photo. Same logic as initPhotoDrag but saves to localStorage.
 */
function initExamplePhotoDrag(div) {
  var startX, startY, origLeft, origTop, hasMoved, pointerId;
  var DRAG_THRESHOLD = 5;

  function onPointerDown(e) {
    if (e.target.closest(".footprint-delete")) return;
    e.preventDefault();
    startX = e.clientX;
    startY = e.clientY;
    hasMoved = false;
    pointerId = e.pointerId;

    var board = document.getElementById("footprints-board");
    var rect = board.getBoundingClientRect();
    var divRect = div.getBoundingClientRect();
    origLeft = divRect.left - rect.left;
    origTop = divRect.top - rect.top;

    div.setPointerCapture(e.pointerId);
    div.addEventListener("pointermove", onPointerMove);
    div.addEventListener("pointerup", onPointerUp);
    div.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;
    if (!hasMoved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;

    if (!hasMoved) {
      hasMoved = true;
      div.classList.add("dragging");
      div.style.transform = "rotate(0deg)";
    }

    var board = document.getElementById("footprints-board");
    var rect = board.getBoundingClientRect();
    var newLeft = Math.max(0, Math.min(rect.width - 40, origLeft + dx));
    var newTop = Math.max(0, Math.min(rect.height - 40, origTop + dy));

    div.style.left = (newLeft / rect.width) * 100 + "%";
    div.style.top = (newTop / rect.height) * 100 + "%";
  }

  function onPointerUp(e) {
    div.removeEventListener("pointermove", onPointerMove);
    div.removeEventListener("pointerup", onPointerUp);
    div.removeEventListener("pointercancel", onPointerUp);
    try { div.releasePointerCapture(pointerId); } catch(ex) {}

    if (hasMoved) {
      div.classList.remove("dragging");
      div.style.transform = "rotate(" + (div.dataset.rotation || "0") + "deg)";

      // Save position to localStorage
      var board = document.getElementById("footprints-board");
      var rect = board.getBoundingClientRect();
      var divRect = div.getBoundingClientRect();
      var pctX = Math.max(0, Math.min(95, ((divRect.left - rect.left) / rect.width) * 100));
      var pctY = Math.max(0, Math.min(95, ((divRect.top - rect.top) / rect.height) * 100));
      localStorage.setItem("fp_example_position", JSON.stringify({
        x: Math.round(pctX * 10) / 10,
        y: Math.round(pctY * 10) / 10
      }));
    }
  }

  div.addEventListener("pointerdown", onPointerDown);
}

function renderFootprintsPage() {
  var board = document.getElementById("footprints-board");
  if (!board) return;

  var photos = fpPhotosState.photos;
  var start = fpPhotosState.currentPage * fpPhotosState.perPage;
  var pagePhotos = photos.slice(start, start + fpPhotosState.perPage);

  board.innerHTML = "";

  pagePhotos.forEach(function(item) {
    var div = document.createElement("div");
    div.className = "footprint-item";
    div.dataset.itemId = item.id;

    // Position from server data
    var posX = item.pos_x !== undefined ? item.pos_x : Math.random() * 70 + 5;
    var posY = item.pos_y !== undefined ? item.pos_y : Math.random() * 70 + 5;
    div.style.left = posX + "%";
    div.style.top = posY + "%";

    // Random rotation
    var rotation = item.rotation || 0;
    div.style.transform = "rotate(" + rotation + "deg)";
    div.dataset.rotation = rotation;

    div.innerHTML = '<div class="footprint-pin"></div>' +
      '<div class="footprint-photo">' +
      '<img src="/assets/footprints/thumbs/' + item.filename + '" alt="" loading="lazy" />' +
      (item.caption ? '<div class="footprint-caption">' + escapeHtml(item.caption) + '</div>' : '') +
      '</div>' +
      '<button class="footprint-delete" data-id="' + item.id + '">×</button>';

    // Delete button handler
    div.querySelector(".footprint-delete").addEventListener("click", function(e) {
      e.stopPropagation();
      e.preventDefault();
      if (confirm("删除这张照片？")) { deleteFootprint(item.id); }
    });

    // Drag handling (same pattern as sticky notes)
    initPhotoDrag(div, item);

    board.appendChild(div);
  });

  renderFootprintsPagination();
}

function renderFootprintsPagination() {
  var totalPages = Math.ceil(fpPhotosState.photos.length / fpPhotosState.perPage);
  var container = document.getElementById("footprints-pagination");

  // Create pagination container if it doesn't exist
  if (!container) {
    container = document.createElement("div");
    container.id = "footprints-pagination";
    container.className = "footprints-pagination";
    var board = document.getElementById("footprints-board");
    if (board) board.parentNode.insertBefore(container, board.nextSibling);
  }

  if (totalPages <= 1) {
    container.style.display = "none";
    return;
  }

  container.style.display = "flex";
  container.innerHTML = "";

  // prev button
  var prevBtn = document.createElement("button");
  prevBtn.className = "shelf-nav-btn" + (fpPhotosState.currentPage === 0 ? " hidden" : "");
  prevBtn.textContent = "\u2039";
  prevBtn.addEventListener("click", function() {
    if (fpPhotosState.currentPage > 0) {
      fpPhotosState.currentPage--;
      renderFootprintsPage();
    }
  });
  container.appendChild(prevBtn);

  // dots
  var dotsDiv = document.createElement("div");
  dotsDiv.className = "shelf-pagination";
  for (var i = 0; i < totalPages; i++) {
    var dot = document.createElement("button");
    dot.className = "page-dot" + (i === fpPhotosState.currentPage ? " active" : "");
    dot.dataset.page = i;
    dot.addEventListener("click", function() {
      fpPhotosState.currentPage = parseInt(this.dataset.page);
      renderFootprintsPage();
    });
    dotsDiv.appendChild(dot);
  }
  container.appendChild(dotsDiv);

  // next button
  var nextBtn = document.createElement("button");
  nextBtn.className = "shelf-nav-btn" + (fpPhotosState.currentPage >= totalPages - 1 ? " hidden" : "");
  nextBtn.textContent = "\u203a";
  nextBtn.addEventListener("click", function() {
    if (fpPhotosState.currentPage < totalPages - 1) {
      fpPhotosState.currentPage++;
      renderFootprintsPage();
    }
  });
  container.appendChild(nextBtn);
}

function initPhotoDrag(div, item) {
  var startX, startY, origLeft, origTop, hasMoved, pointerId;
  var DRAG_THRESHOLD = 5;

  function onPointerDown(e) {
    // Don't start drag on delete button
    if (e.target.closest(".footprint-delete")) return;

    e.preventDefault();

    startX = e.clientX;
    startY = e.clientY;
    hasMoved = false;
    pointerId = e.pointerId;

    var board = document.getElementById("footprints-board");
    var rect = board.getBoundingClientRect();
    var divRect = div.getBoundingClientRect();
    origLeft = divRect.left - rect.left;
    origTop = divRect.top - rect.top;

    div.setPointerCapture(e.pointerId);
    div.addEventListener("pointermove", onPointerMove);
    div.addEventListener("pointerup", onPointerUp);
    div.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;

    if (!hasMoved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) {
      return;
    }

    if (!hasMoved) {
      hasMoved = true;
      div.classList.add("dragging");
      // Remove rotation during drag for cleaner movement
      div.style.transform = "rotate(0deg)";
    }

    var board = document.getElementById("footprints-board");
    var rect = board.getBoundingClientRect();
    var newLeft = origLeft + dx;
    var newTop = origTop + dy;

    // Clamp within container
    newLeft = Math.max(0, Math.min(rect.width - 40, newLeft));
    newTop = Math.max(0, Math.min(rect.height - 40, newTop));

    // Convert to percentage
    var pctX = (newLeft / rect.width) * 100;
    var pctY = (newTop / rect.height) * 100;

    div.style.left = pctX + "%";
    div.style.top = pctY + "%";
  }

  function onPointerUp(e) {
    div.removeEventListener("pointermove", onPointerMove);
    div.removeEventListener("pointerup", onPointerUp);
    div.removeEventListener("pointercancel", onPointerUp);

    try { div.releasePointerCapture(pointerId); } catch(ex) {}

    if (hasMoved) {
      div.classList.remove("dragging");
      // Restore rotation
      var rotation = div.dataset.rotation || "0";
      div.style.transform = "rotate(" + rotation + "deg)";

      // Save new position to server
      var itemId = div.dataset.itemId;
      var board = document.getElementById("footprints-board");
      var rect = board.getBoundingClientRect();
      var divRect = div.getBoundingClientRect();
      var pctX = ((divRect.left - rect.left) / rect.width) * 100;
      var pctY = ((divRect.top - rect.top) / rect.height) * 100;

      // Clamp
      pctX = Math.max(0, Math.min(95, pctX));
      pctY = Math.max(0, Math.min(95, pctY));

      apiPost("footprints/" + itemId + "/position", {
        pos_x: Math.round(pctX * 10) / 10,
        pos_y: Math.round(pctY * 10) / 10
      }).catch(function() {});
    } else {
      // No drag happened — treat as click → open lightbox
      openLightbox("/assets/footprints/originals/" + item.filename);
    }
  }

  div.addEventListener("pointerdown", onPointerDown);
}

// === Sticky Notes (便签板) ===

var fpNotesState = {
  notes: [],
  currentPage: 0,
  perPage: 6,
  dragState: null
};

async function loadFpNotes() {
  var list = document.getElementById("fp-notes-list");
  if (!list) return;

  try {
    var res = await apiGet("footprints/notes");
    if (!res.success) { renderFpNotesEmpty(list); return; }

    var notes = res.notes || [];
    if (notes.length === 0) {
      renderFpNotesEmpty(list);
      return;
    }

    fpNotesState.notes = notes;
    var totalPages = Math.ceil(notes.length / fpNotesState.perPage);
    if (fpNotesState.currentPage >= totalPages) {
      fpNotesState.currentPage = Math.max(0, totalPages - 1);
    }
    renderFpNotesPage();
  } catch(e) {
    renderFpNotesEmpty(document.getElementById("fp-notes-list"));
  }
}

function renderFpNotesEmpty(list) {
  list.innerHTML = '<div class="fp-notes-empty">写一张便签给他吧 ✦</div>';
  // remove pagination if present
  var pag = document.getElementById("fp-notes-pagination");
  if (pag) pag.style.display = "none";
}

function renderFpNotesPage() {
  var list = document.getElementById("fp-notes-list");
  if (!list) return;

  var notes = fpNotesState.notes;
  var start = fpNotesState.currentPage * fpNotesState.perPage;
  var pageNotes = notes.slice(start, start + fpNotesState.perPage);

  list.innerHTML = "";

  pageNotes.forEach(function(pair) {
    var group = document.createElement("div");
    group.className = "fp-note-group";
    group.dataset.noteId = pair.id;

    // Position from server data
    var posX = pair.pos_x !== undefined ? pair.pos_x : Math.random() * 70 + 5;
    var posY = pair.pos_y !== undefined ? pair.pos_y : Math.random() * 70 + 5;
    group.style.left = posX + "%";
    group.style.top = posY + "%";

    // random rotation for the group
    var rotation = (Math.random() * 10 - 5).toFixed(1);
    group.style.transform = "rotate(" + rotation + "deg)";
    group.dataset.rotation = rotation;

    // pin
    var pin = document.createElement("div");
    pin.className = "fp-note-pin";
    group.appendChild(pin);

    // user note
    var userDiv = document.createElement("div");
    userDiv.className = "fp-note-item user";
    var userTime = pair.created_at ? new Date(pair.created_at * 1000).toLocaleDateString() : "";
    userDiv.innerHTML = '<div class="fp-note-content">' + escapeHtml(pair.content) + '</div>' +
      '<div class="fp-note-time">' + userTime + '</div>';
    group.appendChild(userDiv);

    // delete button
    var delBtn = document.createElement("button");
    delBtn.className = "footprint-delete";
    delBtn.textContent = "\u00d7";
    delBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      e.preventDefault();
      if (confirm("删掉这张便签？")) { deleteFpNote(pair.id); }
    });
    group.appendChild(delBtn);

    // bot reply (overlapping, pinned on top of user note)
    if (pair.reply) {
      var botDiv = document.createElement("div");
      botDiv.className = "fp-note-item bot-reply";
      var botRotation = (Math.random() * 8 - 4).toFixed(1);
      botDiv.style.transform = "rotate(" + botRotation + "deg)";
      var botTime = pair.reply_at ? new Date(pair.reply_at * 1000).toLocaleDateString() : "";
      botDiv.innerHTML = '<div class="fp-note-content">' + escapeHtml(pair.reply) + '</div>' +
        '<div class="fp-note-time">' + botTime + '</div>';
      group.appendChild(botDiv);
    }

    // Drag handling
    initNoteDrag(group);

    list.appendChild(group);
  });

  renderFpNotesPagination();
}

function renderFpNotesPagination() {
  var totalPages = Math.ceil(fpNotesState.notes.length / fpNotesState.perPage);
  var container = document.getElementById("fp-notes-pagination");

  // Create pagination container if it doesn't exist
  if (!container) {
    container = document.createElement("div");
    container.id = "fp-notes-pagination";
    container.className = "fp-notes-pagination";
    var list = document.getElementById("fp-notes-list");
    list.parentNode.insertBefore(container, list.nextSibling);
  }

  if (totalPages <= 1) {
    container.style.display = "none";
    return;
  }

  container.style.display = "flex";
  container.innerHTML = "";

  // prev button
  var prevBtn = document.createElement("button");
  prevBtn.className = "shelf-nav-btn" + (fpNotesState.currentPage === 0 ? " hidden" : "");
  prevBtn.textContent = "\u2039";
  prevBtn.addEventListener("click", function() {
    if (fpNotesState.currentPage > 0) {
      fpNotesState.currentPage--;
      renderFpNotesPage();
    }
  });
  container.appendChild(prevBtn);

  // dots
  var dotsDiv = document.createElement("div");
  dotsDiv.className = "shelf-pagination";
  for (var i = 0; i < totalPages; i++) {
    var dot = document.createElement("button");
    dot.className = "page-dot" + (i === fpNotesState.currentPage ? " active" : "");
    dot.dataset.page = i;
    dot.addEventListener("click", function() {
      fpNotesState.currentPage = parseInt(this.dataset.page);
      renderFpNotesPage();
    });
    dotsDiv.appendChild(dot);
  }
  container.appendChild(dotsDiv);

  // next button
  var nextBtn = document.createElement("button");
  nextBtn.className = "shelf-nav-btn" + (fpNotesState.currentPage >= totalPages - 1 ? " hidden" : "");
  nextBtn.textContent = "\u203a";
  nextBtn.addEventListener("click", function() {
    if (fpNotesState.currentPage < totalPages - 1) {
      fpNotesState.currentPage++;
      renderFpNotesPage();
    }
  });
  container.appendChild(nextBtn);
}

function initNoteDrag(group) {
  var startX, startY, origLeft, origTop, hasMoved, pointerId;
  var DRAG_THRESHOLD = 5;

  function onPointerDown(e) {
    // Don't start drag on delete button
    if (e.target.closest(".footprint-delete")) return;

    e.preventDefault(); // prevent text selection from interfering with drag

    startX = e.clientX;
    startY = e.clientY;
    hasMoved = false;
    pointerId = e.pointerId;

    var list = document.getElementById("fp-notes-list");
    var rect = list.getBoundingClientRect();
    var groupRect = group.getBoundingClientRect();
    origLeft = groupRect.left - rect.left;
    origTop = groupRect.top - rect.top;

    group.setPointerCapture(e.pointerId);
    group.addEventListener("pointermove", onPointerMove);
    group.addEventListener("pointerup", onPointerUp);
    group.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;

    if (!hasMoved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) {
      return;
    }

    if (!hasMoved) {
      hasMoved = true;
      group.classList.add("dragging");
      // Remove rotation during drag for cleaner movement
      group.style.transform = "rotate(0deg)";
    }

    var list = document.getElementById("fp-notes-list");
    var rect = list.getBoundingClientRect();
    var newLeft = origLeft + dx;
    var newTop = origTop + dy;

    // Clamp within container
    newLeft = Math.max(0, Math.min(rect.width - 40, newLeft));
    newTop = Math.max(0, Math.min(rect.height - 40, newTop));

    // Convert to percentage
    var pctX = (newLeft / rect.width) * 100;
    var pctY = (newTop / rect.height) * 100;

    group.style.left = pctX + "%";
    group.style.top = pctY + "%";
  }

  function onPointerUp(e) {
    group.removeEventListener("pointermove", onPointerMove);
    group.removeEventListener("pointerup", onPointerUp);
    group.removeEventListener("pointercancel", onPointerUp);

    try { group.releasePointerCapture(pointerId); } catch(ex) {}

    if (hasMoved) {
      group.classList.remove("dragging");
      // Restore rotation
      var rotation = group.dataset.rotation || "0";
      group.style.transform = "rotate(" + rotation + "deg)";

      // Save new position to server
      var noteId = group.dataset.noteId;
      var list = document.getElementById("fp-notes-list");
      var rect = list.getBoundingClientRect();
      var groupRect = group.getBoundingClientRect();
      var pctX = ((groupRect.left - rect.left) / rect.width) * 100;
      var pctY = ((groupRect.top - rect.top) / rect.height) * 100;

      // Clamp
      pctX = Math.max(0, Math.min(95, pctX));
      pctY = Math.max(0, Math.min(95, pctY));

      apiPost("footprints/notes/" + noteId + "/position", {
        pos_x: Math.round(pctX * 10) / 10,
        pos_y: Math.round(pctY * 10) / 10
      }).catch(function() {});
    }
  }

  group.addEventListener("pointerdown", onPointerDown);
}

async function postFpNote(text) {
  await apiPost("footprints/note", { content: text });
  loadFpNotes();
  // Poll for bot reply (bot replies asynchronously after 3-8s + LLM time)
  pollForNoteReply();
}

/**
 * Poll for new bot replies on notes. Checks every 2s, stops after 30s or when reply appears.
 */
function pollForNoteReply() {
  var attempts = 0;
  var maxAttempts = 15; // 30 seconds max
  var timer = setInterval(async function() {
    attempts++;
    if (attempts >= maxAttempts) { clearInterval(timer); return; }
    try {
      var res = await apiGet("footprints/notes");
      if (!res.success) return;
      var notes = res.notes || [];
      // Check if any note got a new reply since we last rendered
      var hasNewReply = notes.some(function(n) {
        var old = fpNotesState.notes.find(function(o) { return o.id === n.id; });
        return n.reply && (!old || !old.reply);
      });
      if (hasNewReply) {
        clearInterval(timer);
        fpNotesState.notes = notes;
        renderFpNotesPage();
      }
    } catch(e) {}
  }, 2000);
}

async function deleteFpNote(id) {
  await apiDelete("footprints/notes/" + id);
  loadFpNotes();
}

// === Bot Moments (他的动态) ===

async function loadFpMoments() {
  var list = document.getElementById("fp-moments-list");
  if (!list) return;

  try {
    var res = await apiGet("footprints/moments");
    if (!res.success) { list.innerHTML = '<div class="fp-moments-empty">还没有动态 ✦</div>'; return; }

    var moments = res.moments || [];
    if (moments.length === 0) {
      list.innerHTML = '<div class="fp-moments-empty">还没有动态 ✦</div>';
      return;
    }

    list.innerHTML = "";
    moments.forEach(function(m) {
      var card = document.createElement("div");
      card.className = "fp-moment-card";

      var isUserMoment = m.type === "user_note";
      var timeStr = m.created_at ? new Date(m.created_at * 1000).toLocaleString() : "";
      // For the like button, show user's own like state
      var userLiked = m.user_liked || false;
      var liked = userLiked ? " liked" : "";
      var likeText = (m.liked || userLiked) ? "♥" : "♡";
      var likeCount = m.like_count || 0;
      var botNick = localStorage.getItem(BOT_NICKNAME_KEY) || "沈星回";
      var userNick = localStorage.getItem(USER_NICKNAME_KEY) || "我";

      // Determine avatar and name based on who posted
      var posterNick = isUserMoment ? userNick : botNick;
      var posterAvatarKey = isUserMoment ? USER_AVATAR_KEY : BOT_AVATAR_KEY;
      var posterAvatar = localStorage.getItem(posterAvatarKey);

      var repliesHtml = "";
      if (m.replies && m.replies.length > 0) {
        repliesHtml = '<div class="fp-moment-replies">';
        m.replies.forEach(function(r, idx) {
          var fromName = r.role === "bot" ? botNick : userNick;
          var replyToName = r.reply_to || "";
          var nameHtml;
          if (replyToName) {
            nameHtml = '<span class="reply-name">' + escapeHtml(fromName) + '</span>' +
              '<span class="reply-arrow"> 回复 </span>' +
              '<span class="reply-name">' + escapeHtml(replyToName) + '</span>';
          } else {
            nameHtml = '<span class="reply-name">' + escapeHtml(fromName) + '</span>';
          }
          repliesHtml += '<div class="fp-moment-reply" data-reply-idx="' + idx + '" data-reply-from="' + escapeAttr(fromName) + '">' +
            nameHtml + '：' + escapeHtml(r.content) + '</div>';
        });
        repliesHtml += '</div>';
      }

      var defaultAvatarUrl = isUserMoment ? '/static/avatars/default-user.png' : '/static/avatars/default-bot.png';
      var avatarUrl = posterAvatar || defaultAvatarUrl;
      var avatarStyle = ' style="background-image:url(' + avatarUrl + ');background-size:cover;background-position:center;"';

      card.innerHTML =
        '<div class="fp-moment-header">' +
          '<div class="fp-moment-avatar"' + avatarStyle + '></div>' +
          '<span class="fp-moment-name">' + escapeHtml(posterNick) + '</span>' +
          '<span class="fp-moment-time">' + timeStr + '</span>' +
        '</div>' +
        '<div class="fp-moment-content">' + escapeHtml(m.content) + '</div>' +
        repliesHtml +
        '<div class="fp-moment-actions">' +
          '<button class="fp-moment-action-btn' + liked + '" data-id="' + m.id + '" data-action="like">' + likeText + ' ' + (likeCount > 0 ? likeCount : '') + '</button>' +
          '<button class="fp-moment-action-btn" data-id="' + m.id + '" data-action="reply">💬 回复</button>' +
          '<button class="fp-moment-action-btn" data-id="' + m.id + '" data-action="delete" style="margin-left:auto;color:#c06060;">删除</button>' +
        '</div>';

      // bind actions
      card.querySelector('[data-action="like"]').addEventListener("click", function() {
        likeMoment(m.id);
      });
      card.querySelector('[data-action="reply"]').addEventListener("click", function() {
        var text = prompt("评论：");
        if (text && text.trim()) { replyMoment(m.id, text.trim(), null); }
      });
      card.querySelector('[data-action="delete"]').addEventListener("click", function() {
        if (confirm("删除这条动态？")) {
          apiDelete("footprints/moments/" + m.id).then(function() { loadFpMoments(); });
        }
      });

      // bind click-to-reply on individual comments
      card.querySelectorAll(".fp-moment-reply").forEach(function(replyEl) {
        replyEl.addEventListener("click", function() {
          var replyFrom = replyEl.dataset.replyFrom;
          var text = prompt("回复 " + replyFrom + "：");
          if (text && text.trim()) { replyMoment(m.id, text.trim(), replyFrom); }
        });
      });

      list.appendChild(card);
    });
  } catch(e) {
    list.innerHTML = '<div class="fp-moments-empty">还没有动态 ✦</div>';
  }
}

async function likeMoment(id) {
  await apiPost("footprints/moments/" + id + "/like", {});
  loadFpMoments();
}

async function replyMoment(id, text, replyTo) {
  var body = { content: text };
  if (replyTo) body.reply_to = replyTo;
  await apiPost("footprints/moments/" + id + "/reply", body);
  loadFpMoments();
  // Poll for bot reply on this moment
  pollForMomentReply(id);
}

async function postUserMoment(text) {
  await apiPost("footprints/moments", { content: text });
  loadFpMoments();
  // Poll for bot reaction (like + reply)
  pollForUserMomentReaction();
}

/**
 * Poll for bot reaction to user's latest moment.
 */
function pollForUserMomentReaction() {
  var attempts = 0;
  var maxAttempts = 15; // 30 seconds
  var timer = setInterval(async function() {
    attempts++;
    if (attempts >= maxAttempts) { clearInterval(timer); return; }
    try {
      var res = await apiGet("footprints/moments");
      if (!res.success) return;
      var moments = res.moments || [];
      // Find the latest user moment and check if bot replied
      var userMoment = moments.find(function(m) { return m.type === "user_note"; });
      if (userMoment && userMoment.replies && userMoment.replies.length > 0) {
        var lastReply = userMoment.replies[userMoment.replies.length - 1];
        if (lastReply.role === "bot") {
          clearInterval(timer);
          loadFpMoments();
        }
      } else if (userMoment && userMoment.liked) {
        // At least liked, refresh to show the like
        loadFpMoments();
      }
    } catch(e) {}
  }, 2000);
}

/**
 * Poll for bot reply on a specific moment. Checks every 2s, stops after 30s or when reply appears.
 */
function pollForMomentReply(momentId) {
  var attempts = 0;
  var maxAttempts = 15; // 30 seconds max
  var timer = setInterval(async function() {
    attempts++;
    if (attempts >= maxAttempts) { clearInterval(timer); return; }
    try {
      var res = await apiGet("footprints/moments");
      if (!res.success) return;
      var moments = res.moments || [];
      var moment = moments.find(function(m) { return m.id === momentId; });
      if (!moment) { clearInterval(timer); return; }
      // Check if there's a new bot reply (last reply is from bot)
      var replies = moment.replies || [];
      var lastReply = replies.length > 0 ? replies[replies.length - 1] : null;
      if (lastReply && lastReply.role === "bot") {
        clearInterval(timer);
        loadFpMoments();
      }
    } catch(e) {}
  }, 2000);
}

// === Photo helpers ===

function openLightbox(src) {
  var lb = document.getElementById("footprint-lightbox");
  if (!lb) {
    lb = document.createElement("div");
    lb.id = "footprint-lightbox";
    lb.className = "footprint-lightbox";
    lb.innerHTML = '<button class="footprint-lightbox-close">×</button><img src="" alt="" />';
    lb.addEventListener("click", function() { lb.classList.remove("open"); });
    document.body.appendChild(lb);
  }
  lb.querySelector("img").src = src;
  lb.classList.add("open");
}

async function deleteFootprint(id) {
  await apiDelete("footprints/" + id);
  loadFootprints();
}

async function uploadFootprint(file) {
  var formData = new FormData();
  formData.append("file", file);
  try {
    var resp = await fetch(API_BASE + "/api/footprints/upload", { method: "POST", body: formData });
    var res = await resp.json();
    if (res.success) loadFootprints();
  } catch(e) { console.error("Upload failed:", e); }
}

// ==================== Helpers ====================

async function apiPost(endpoint, body) {
  var resp = await fetch(API_BASE + "/api/" + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

async function apiGet(endpoint) {
  var resp = await fetch(API_BASE + "/api/" + endpoint);
  return resp.json();
}

async function apiDelete(endpoint, body) {
  var resp = await fetch(API_BASE + "/api/" + endpoint, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return resp.json();
}

async function apiPut(endpoint, body) {
  var resp = await fetch(API_BASE + "/api/" + endpoint, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

function escapeHtml(str) {
  var div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

// ==================== Stars Animation ====================

(function () {
  var canvas, ctx, stars, animId;
  var STAR_COUNT = 25;
  var MAX_SIZE = 4;

  // particle config (loaded from localStorage)
  var particleConfig = {
    enabled: true,
    shape: "heart",  // heart, star, circle, snow
    color: "theme",  // theme, white, gold, pink
  };

  function loadParticleConfig() {
    try {
      var saved = localStorage.getItem("shared_read_particles");
      if (saved) {
        var parsed = JSON.parse(saved);
        if (parsed.enabled !== undefined) particleConfig.enabled = parsed.enabled;
        if (parsed.shape) particleConfig.shape = parsed.shape;
        if (parsed.color) particleConfig.color = parsed.color;
      }
    } catch (e) {}
  }

  function saveParticleConfig() {
    localStorage.setItem("shared_read_particles", JSON.stringify(particleConfig));
    saveProfileToServer("particles", particleConfig);
  }

  function getParticleColor() {
    switch (particleConfig.color) {
      case "white": return "rgba(255, 255, 255, 0.8)";
      case "gold": return "rgba(232, 200, 122, 0.8)";
      case "pink": return "rgba(232, 160, 184, 0.8)";
      default: // "theme" - use CSS variable
        var style = getComputedStyle(document.documentElement);
        var primary = style.getPropertyValue("--heart-color").trim();
        return primary || "rgba(200, 160, 220, 0.8)";
    }
  }

  function drawParticle(s) {
    var alpha = s.opacity * (0.6 + 0.4 * Math.sin(s.pulse));
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(s.x, s.y);
    ctx.scale(s.size / 5, s.size / 5);
    ctx.fillStyle = getParticleColor();

    switch (particleConfig.shape) {
      case "star":
        // four-pointed star (✦)
        ctx.beginPath();
        ctx.moveTo(0, -6);
        ctx.quadraticCurveTo(1, -1, 6, 0);
        ctx.quadraticCurveTo(1, 1, 0, 6);
        ctx.quadraticCurveTo(-1, 1, -6, 0);
        ctx.quadraticCurveTo(-1, -1, 0, -6);
        ctx.closePath();
        ctx.fill();
        break;
      case "circle":
        ctx.beginPath();
        ctx.arc(0, 0, 4, 0, Math.PI * 2);
        ctx.fill();
        break;
      case "snow":
        ctx.font = "10px serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("❄", 0, 0);
        break;
      default: // heart
        ctx.beginPath();
        ctx.moveTo(0, -3);
        ctx.bezierCurveTo(-5, -8, -10, -2, 0, 5);
        ctx.bezierCurveTo(10, -2, 5, -8, 0, -3);
        ctx.closePath();
        ctx.fill();
        break;
    }

    ctx.restore();
  }

  function initStars() {
    loadParticleConfig();
    canvas = document.getElementById("stars-canvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");
    resize();
    stars = [];
    for (var i = 0; i < STAR_COUNT; i++) {
      stars.push(createStar());
    }
    window.addEventListener("resize", resize);
    if (particleConfig.enabled) animate();
  }

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  function createStar() {
    return {
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      size: Math.random() * MAX_SIZE + 0.5,
      speedX: (Math.random() - 0.5) * 0.3,
      speedY: (Math.random() - 0.5) * 0.2 - 0.1,
      opacity: Math.random() * 0.5 + 0.2,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: Math.random() * 0.02 + 0.01,
    };
  }

  function animate() {
    if (!particleConfig.enabled) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      s.x += s.speedX;
      s.y += s.speedY;
      s.pulse += s.pulseSpeed;
      if (s.x < -10) s.x = canvas.width + 10;
      if (s.x > canvas.width + 10) s.x = -10;
      if (s.y < -10) s.y = canvas.height + 10;
      if (s.y > canvas.height + 10) s.y = -10;
      drawParticle(s);
    }
    animId = requestAnimationFrame(animate);
  }

  // expose for settings panel
  window._particleControl = {
    setEnabled: function (enabled) {
      particleConfig.enabled = enabled;
      saveParticleConfig();
      if (enabled) {
        animate();
      } else {
        if (animId) cancelAnimationFrame(animId);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    },
    setShape: function (shape) {
      particleConfig.shape = shape;
      saveParticleConfig();
    },
    setColor: function (color) {
      particleConfig.color = color;
      saveParticleConfig();
    },
    getConfig: function () { return particleConfig; },
  };

  // start after DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initStars);
  } else {
    initStars();
  }
})();

// ==================== Theme Switching ====================

(function initTheme() {
  // apply saved theme immediately (before DOM renders fully)
  var saved = localStorage.getItem("shared_read_theme") || "";
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
  }
  // apply custom color if saved
  var customColor = localStorage.getItem("shared_read_custom_color");
  if (saved === "custom" && customColor) {
    applyCustomThemeColor(customColor);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var picker = document.getElementById("theme-picker");
    if (!picker) return;

    // mark active dot
    var dots = picker.querySelectorAll(".theme-dot");
    dots.forEach(function (dot) {
      if (dot.dataset.theme === saved) {
        dot.classList.add("active");
      } else if (!saved && dot.dataset.theme === "") {
        dot.classList.add("active");
      } else {
        dot.classList.remove("active");
      }
    });

    // Update custom dot preview color
    var customInput = document.getElementById("custom-theme-color-input");
    if (customColor && customInput) {
      customInput.value = customColor;
      var customInner = picker.querySelector(".theme-dot-custom-inner");
      if (customInner) customInner.style.background = customColor;
    }

    // bind click for preset dots
    dots.forEach(function (dot) {
      dot.addEventListener("click", function () {
        var theme = dot.dataset.theme;

        // For custom theme, open color picker
        if (theme === "custom") {
          if (customInput) customInput.click();
          return;
        }

        // Remove custom inline styles when switching to preset
        removeCustomThemeStyles();

        // apply
        if (theme) {
          document.documentElement.setAttribute("data-theme", theme);
        } else {
          document.documentElement.removeAttribute("data-theme");
        }
        // save
        localStorage.setItem("shared_read_theme", theme);
        saveProfileToServer("theme", theme);
        // update active state
        dots.forEach(function (d) { d.classList.remove("active"); });
        dot.classList.add("active");
      });
    });

    // Custom color input handler
    if (customInput) {
      customInput.addEventListener("input", function () {
        var color = customInput.value;
        applyCustomThemeColor(color);
        // Update the custom dot preview
        var customInner = picker.querySelector(".theme-dot-custom-inner");
        if (customInner) customInner.style.background = color;
        // Mark custom dot as active
        dots.forEach(function (d) { d.classList.remove("active"); });
        picker.querySelector('[data-theme="custom"]').classList.add("active");
        // Set data-theme to custom
        document.documentElement.setAttribute("data-theme", "custom");
        localStorage.setItem("shared_read_theme", "custom");
        localStorage.setItem("shared_read_custom_color", color);
        saveProfileToServer("theme", "custom");
        saveProfileToServer("custom_color", color);
      });
    }
  });
})();

/**
 * Generate and apply custom theme CSS variables from a single hex color.
 * Derives light/dark variants, backgrounds, and alpha values.
 */
function applyCustomThemeColor(hex) {
  var r = parseInt(hex.slice(1, 3), 16);
  var g = parseInt(hex.slice(3, 5), 16);
  var b = parseInt(hex.slice(5, 7), 16);

  // Derive darker variant (multiply by 0.75)
  var dr = Math.round(r * 0.75);
  var dg = Math.round(g * 0.75);
  var db = Math.round(b * 0.75);

  // Derive lighter background (mix with white at 90%)
  var lr = Math.round(r + (255 - r) * 0.85);
  var lg = Math.round(g + (255 - g) * 0.85);
  var lb = Math.round(b + (255 - b) * 0.85);

  var lr2 = Math.round(r + (255 - r) * 0.92);
  var lg2 = Math.round(g + (255 - g) * 0.92);
  var lb2 = Math.round(b + (255 - b) * 0.92);

  var lr3 = Math.round(r + (255 - r) * 0.96);
  var lg3 = Math.round(g + (255 - g) * 0.96);
  var lb3 = Math.round(b + (255 - b) * 0.96);

  // Determine if color is light or dark for text contrast
  var luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  var textPrimary = luminance > 0.5 ? "#3d3d4a" : "#4a3d5c";
  var textSecondary = luminance > 0.5 ? "#5c5c6a" : "#5c4d73";
  var textMuted = luminance > 0.5 ? "#8a8a9a" : "#9a8aad";

  var style = document.getElementById("custom-theme-style");
  if (!style) {
    style = document.createElement("style");
    style.id = "custom-theme-style";
    document.head.appendChild(style);
  }

  style.textContent = '[data-theme="custom"] {\n' +
    '  --bg-gradient-1: rgb(' + lr + ',' + lg + ',' + lb + ');\n' +
    '  --bg-gradient-2: rgb(' + lr2 + ',' + lg2 + ',' + lb2 + ');\n' +
    '  --bg-gradient-3: rgb(' + lr3 + ',' + lg3 + ',' + lb3 + ');\n' +
    '  --primary: ' + hex + ';\n' +
    '  --primary-dark: rgb(' + dr + ',' + dg + ',' + db + ');\n' +
    '  --primary-light: rgba(' + r + ',' + g + ',' + b + ', 0.15);\n' +
    '  --primary-border: rgba(' + r + ',' + g + ',' + b + ', 0.25);\n' +
    '  --primary-hover: rgba(' + r + ',' + g + ',' + b + ', 0.4);\n' +
    '  --primary-glow: rgba(' + dr + ',' + dg + ',' + db + ', 0.3);\n' +
    '  --text-primary: ' + textPrimary + ';\n' +
    '  --text-secondary: ' + textSecondary + ';\n' +
    '  --text-muted: ' + textMuted + ';\n' +
    '  --text-hint: ' + textMuted + ';\n' +
    '  --glass-bg: rgba(255, 255, 255, 0.28);\n' +
    '  --glass-border: rgba(255, 255, 255, 0.45);\n' +
    '  --glass-shadow: rgba(' + r + ',' + g + ',' + b + ', 0.1);\n' +
    '  --nav-bg: rgba(255, 255, 255, 0.6);\n' +
    '  --nav-border: rgba(' + r + ',' + g + ',' + b + ', 0.15);\n' +
    '  --divider: rgba(' + r + ',' + g + ',' + b + ', 0.15);\n' +
    '  --card-hover-shadow: rgba(' + r + ',' + g + ',' + b + ', 0.15);\n' +
    '  --progress-bot: linear-gradient(90deg, ' + hex + ', rgb(' + dr + ',' + dg + ',' + db + '));\n' +
    '  --progress-user: linear-gradient(90deg, rgba(' + r + ',' + g + ',' + b + ', 0.7), ' + hex + ');\n' +
    '  --heart-color: rgba(' + r + ',' + g + ',' + b + ', 0.8);\n' +
    '  --highlight-bg: rgba(' + r + ',' + g + ',' + b + ', 0.35);\n' +
    '}\n';
}

/**
 * Remove custom theme inline styles when switching back to a preset theme.
 */
function removeCustomThemeStyles() {
  var style = document.getElementById("custom-theme-style");
  if (style) style.remove();
}

// ==================== Particle Settings ====================

(function initParticleSettings() {
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("particle-toggle");
    var options = document.getElementById("particle-options");
    var shapePicker = document.getElementById("particle-shape-picker");
    var colorPicker = document.getElementById("particle-color-picker");

    if (!toggle || !window._particleControl) return;

    var config = window._particleControl.getConfig();

    // restore state
    toggle.checked = config.enabled;
    if (!config.enabled && options) options.classList.add("hidden");

    // restore active shape
    if (shapePicker) {
      shapePicker.querySelectorAll(".particle-shape-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.shape === config.shape);
      });
    }

    // restore active color
    if (colorPicker) {
      colorPicker.querySelectorAll(".particle-color-btn").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.color === config.color);
      });
    }

    // toggle binding
    toggle.addEventListener("change", function () {
      window._particleControl.setEnabled(toggle.checked);
      if (options) {
        if (toggle.checked) options.classList.remove("hidden");
        else options.classList.add("hidden");
      }
    });

    // shape binding
    if (shapePicker) {
      shapePicker.querySelectorAll(".particle-shape-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          shapePicker.querySelectorAll(".particle-shape-btn").forEach(function (b) { b.classList.remove("active"); });
          btn.classList.add("active");
          window._particleControl.setShape(btn.dataset.shape);
        });
      });
    }

    // color binding
    if (colorPicker) {
      colorPicker.querySelectorAll(".particle-color-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          colorPicker.querySelectorAll(".particle-color-btn").forEach(function (b) { b.classList.remove("active"); });
          btn.classList.add("active");
          window._particleControl.setColor(btn.dataset.color);
        });
      });
    }
  });
})();

// ==================== PWA Service Worker ====================

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(function () {});
}

// ==================== Start ====================
init();

// ==================== Accordion ====================

function initAccordions() {
  document.querySelectorAll(".accordion-card").forEach(function (card) {
    var header = card.querySelector(".accordion-header");
    if (!header) return;
    // remove old listeners by cloning
    var newHeader = header.cloneNode(true);
    header.parentNode.replaceChild(newHeader, header);
    newHeader.addEventListener("click", function () {
      // toggle this card
      var isOpen = card.classList.contains("open");
      // close all others (single-open mode)
      document.querySelectorAll(".accordion-card.open").forEach(function (c) {
        if (c !== card) c.classList.remove("open");
      });
      card.classList.toggle("open", !isOpen);
    });
  });
}

// ==================== Stats ====================

async function loadStats() {
  try {
    var res = await apiGet("stats");
    if (res.success && res.stats) {
      var s = res.stats;
      document.getElementById("stat-books").textContent = s.total_books + "本";
      document.getElementById("stat-user-chapters").textContent = s.user_chapters_read + "章";
      document.getElementById("stat-bot-chapters").textContent = s.bot_chapters_read + "章";
      document.getElementById("stat-days").textContent = s.reading_days + "天";
      document.getElementById("stat-highlights").textContent = s.highlights_count + "条";
      document.getElementById("stat-messages").textContent = s.total_messages + "条";
    }
  } catch (e) {}
}

// ==================== Bot Activity ====================

async function loadBotActivity() {
  var container = document.getElementById("bot-activity-list");
  if (!container) return;
  try {
    var res = await apiGet("books");
    if (!res.success) return;
    var books = res.books;
    var items = [];
    for (var i = 0; i < books.length; i++) {
      var book = books[i];
      var progRes = await apiGet("bot-progress/" + book.id);
      if (progRes.success && progRes.percent > 0) {
        items.push({
          title: book.title,
          percent: progRes.percent,
        });
      }
    }
    if (items.length === 0) {
      container.innerHTML = '<div class="memory-empty">他还没开始读书</div>';
      return;
    }
    container.innerHTML = items.map(function (item) {
      return '<div class="bot-activity-item">' +
        '<span class="activity-book">《' + escapeHtml(item.title) + '》</span>' +
        ' 已读 ' + item.percent + '%' +
        '</div>';
    }).join("");
  } catch (e) {
    container.innerHTML = '<div class="memory-empty">加载失败</div>';
  }
}

// ==================== Reading Progress (阅读进度) ====================

async function loadReadingProgress() {
  var container = document.getElementById("reading-progress-list");
  if (!container) return;
  try {
    var res = await apiGet("reading-progress");
    if (!res.success) {
      container.innerHTML = '<div class="memory-empty">加载失败</div>';
      return;
    }

    var botProgress = res.bot_progress || [];
    var userProgress = res.user_progress || [];

    if (botProgress.length === 0 && userProgress.length === 0) {
      container.innerHTML = '<div class="memory-empty">还没有阅读进度数据</div>';
      return;
    }

    var botNick = localStorage.getItem(BOT_NICKNAME_KEY) || "沈星回";
    var userNick = localStorage.getItem(USER_NICKNAME_KEY) || "你";

    var html = "";

    // Bot progress sub-section
    html += '<div class="rp-subsection">';
    html += '<div class="rp-subsection-title">✦ ' + escapeHtml(botNick) + '的进度</div>';
    if (botProgress.length === 0) {
      html += '<div class="rp-empty">还没有开始读书</div>';
    } else {
      html += botProgress.map(function (item) {
        var pct = Math.min(100, Math.max(0, item.percentage || 0));
        return '<div class="rp-book-item">' +
          '<div class="rp-book-title">《' + escapeHtml(item.book_title) + '》</div>' +
          '<div class="rp-book-detail">第' + item.current_chapter + '章 / 共' + item.total_chapters + '章</div>' +
          '<div class="rp-bar-wrapper">' +
            '<div class="rp-bar-track">' +
              '<div class="rp-bar-fill bot" style="width: ' + pct + '%"></div>' +
            '</div>' +
            '<span class="rp-bar-pct">' + pct.toFixed(1) + '%</span>' +
          '</div>' +
        '</div>';
      }).join("");
    }
    html += '</div>';

    // User progress sub-section
    html += '<div class="rp-subsection">';
    html += '<div class="rp-subsection-title">✦ ' + escapeHtml(userNick) + '的进度</div>';
    if (userProgress.length === 0) {
      html += '<div class="rp-empty">还没有开始读书</div>';
    } else {
      html += userProgress.map(function (item) {
        var pct = Math.min(100, Math.max(0, item.percentage || 0));
        return '<div class="rp-book-item">' +
          '<div class="rp-book-title">《' + escapeHtml(item.book_title) + '》</div>' +
          '<div class="rp-book-detail">第' + item.current_chapter + '章 / 共' + item.total_chapters + '章</div>' +
          '<div class="rp-bar-wrapper">' +
            '<div class="rp-bar-track">' +
              '<div class="rp-bar-fill user" style="width: ' + pct + '%"></div>' +
            '</div>' +
            '<span class="rp-bar-pct">' + pct.toFixed(1) + '%</span>' +
          '</div>' +
        '</div>';
      }).join("");
    }
    html += '</div>';

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="memory-empty">加载失败</div>';
  }
}

// ==================== Note Box ====================

async function loadNoteBox() {
  var container = document.getElementById("note-box-list");
  if (!container) return;
  try {
    var res = await apiGet("profile");
    var notes = (res.success && res.profile && res.profile.note_box) || [];
    if (notes.length === 0) {
      container.innerHTML = '<div class="memory-empty">还没有纸条</div>';
      return;
    }
    container.innerHTML = notes.slice().reverse().map(function (n) {
      var timeStr = n.time ? new Date(n.time).toLocaleString("zh-CN") : "";
      return '<div class="note-box-item">' +
        escapeHtml(n.content) +
        '<span class="note-box-time">' + timeStr + '</span>' +
        '</div>';
    }).join("");
  } catch (e) {
    container.innerHTML = '<div class="memory-empty">加载失败</div>';
  }
}

function sendNoteBox() {
  var textarea = document.getElementById("note-box-textarea");
  var content = textarea.value.trim();
  if (!content) return;
  textarea.value = "";

  apiGet("profile").then(function (res) {
    var profile = (res.success && res.profile) || {};
    var notes = profile.note_box || [];
    notes.push({ content: content, time: Date.now() });
    return apiPost("profile", { note_box: notes });
  }).then(function () {
    loadNoteBox();
  }).catch(function () {});
}

// ==================== Cache Management ====================

/**
 * Clear Service Worker caches and unregister SW, then reload.
 * Solves the mobile PWA issue where updated assets (favicon, css, js) don't refresh.
 */
async function clearCacheAndReload() {
  try {
    // Delete all caches
    if ('caches' in window) {
      var names = await caches.keys();
      await Promise.all(names.map(function(name) { return caches.delete(name); }));
    }
    // Unregister all service workers
    if ('serviceWorker' in navigator) {
      var registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map(function(reg) { return reg.unregister(); }));
    }
  } catch(e) {
    console.warn("Cache clear error:", e);
  }
  // Force reload bypassing cache
  window.location.reload(true);
}

/**
 * Clear localStorage (frontend state only). Server data is unaffected.
 * Preserves server-synced data (avatars, nicknames) since they'll be re-fetched anyway.
 */
function clearLocalStorage() {
  if (!confirm("确定要重置本地状态吗？\n\n这会清除浏览器中的主题、示例照片标记等前端缓存。\n服务端数据（书架、聊天记录、宠物等）不受影响。\n头像和昵称会从服务端重新加载。")) return;
  // Only clear plugin-specific keys, not everything
  var keysToRemove = [];
  for (var i = 0; i < localStorage.length; i++) {
    var key = localStorage.key(i);
    if (key && (
      key.startsWith("shared_read_") ||
      key.startsWith("fp_") ||
      key === "uluru_star_version"
    )) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach(function(k) { localStorage.removeItem(k); });
  window.location.reload(true);
}

// ==================== Connection Info ====================

function loadConnectionInfo() {
  var urlEl = document.getElementById("conn-url");
  var bookEl = document.getElementById("conn-book");
  var sessionEl = document.getElementById("conn-session");
  if (urlEl) urlEl.textContent = window.location.origin;
  if (bookEl) bookEl.textContent = state.currentBookTitle || "未选择";
  if (sessionEl) sessionEl.textContent = state.currentBookId ? "活跃" : "未连接";
}

// ==================== Bot Memories ====================

async function loadBotMemories() {
  var container = document.getElementById("memory-bot-list");
  var countEl = document.getElementById("bot-memory-count");
  if (!container) return;

  try {
    var res = await apiGet("bot-memories");
    if (!res.success) return;
    var memories = res.memories || [];
    if (countEl) countEl.textContent = memories.length;

    if (memories.length === 0) {
      container.innerHTML = '<div class="memory-empty">还没有阅读记忆</div>';
      return;
    }

    // group by book
    var byBook = {};
    memories.forEach(function (m) {
      if (!byBook[m.book_id]) byBook[m.book_id] = { title: m.book_title, chapters: [] };
      byBook[m.book_id].chapters.push(m);
    });

    container.innerHTML = Object.keys(byBook).map(function (bookId) {
      var book = byBook[bookId];
      var chaptersHtml = book.chapters.map(function (m) {
        return '<div class="bot-memory-chapter" data-book-id="' + escapeAttr(bookId) + '" data-chapter="' + m.chapter_index + '" data-full="' + escapeAttr(m.summary) + '">' +
          '<span class="bot-memory-ch-label">第' + (m.chapter_index + 1) + '章</span>' +
          '<span class="bot-memory-ch-preview">' + escapeHtml(m.summary.substring(0, 50)) + (m.summary.length > 50 ? '...' : '') + '</span>' +
          '<button class="memory-card-delete bot-memory-delete" title="删除此记忆">×</button>' +
          '</div>';
      }).join("");

      return '<div class="bot-memory-book">' +
        '<div class="bot-memory-book-header" onclick="this.parentElement.classList.toggle(\'open\')">' +
        '<span>📖 《' + escapeHtml(book.title) + '》</span>' +
        '<span class="bot-memory-book-count">' + book.chapters.length + '章</span>' +
        '</div>' +
        '<div class="bot-memory-book-body">' + chaptersHtml + '</div>' +
        '</div>';
    }).join("");

    // bind click and delete events
    container.querySelectorAll(".bot-memory-chapter").forEach(function (el) {
      el.addEventListener("click", function (e) {
        if (e.target.closest(".bot-memory-delete")) return;
        var bookTitle = el.closest(".bot-memory-book").querySelector(".bot-memory-book-header span").textContent.replace(/^📖 《/, "").replace(/》$/, "");
        var chapterIndex = parseInt(el.dataset.chapter) || 0;
        showBotMemoryDetail(bookTitle, chapterIndex, el);
      });
    });

    container.querySelectorAll(".bot-memory-delete").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var chapterEl = btn.closest(".bot-memory-chapter");
        var bookId = chapterEl.dataset.bookId;
        var chapter = chapterEl.dataset.chapter;
        if (confirm("删除这条阅读记忆？下次阅读时会重新生成。")) {
          apiDelete("bot-memories/" + bookId + "/" + chapter).then(function () {
            loadBotMemories();
          });
        }
      });
    });

  } catch (e) {
    container.innerHTML = '<div class="memory-empty">加载失败</div>';
  }
}

function showBotMemoryDetail(bookTitle, chapterIndex, el) {
  var modal = document.getElementById("memory-modal");
  var title = document.getElementById("memory-modal-title");
  var body = document.getElementById("memory-modal-body");
  var deleteAllBtn = document.getElementById("memory-modal-delete-all");

  var fullText = el.getAttribute("data-full") || "";

  title.textContent = "《" + bookTitle + "》第" + (chapterIndex + 1) + "章 · 阅读记忆";
  body.innerHTML = '<div class="memory-msg-item"><span class="memory-msg-content">' + escapeHtml(fullText) + '</span></div>';
  deleteAllBtn.style.display = "none";

  modal.classList.add("open");
}

// ==================== Memory Management ====================

var memoryState = {
  archives: [],
  activeSessions: [],
  currentArchiveId: null,
  currentArchiveType: null, // "archive" or "active"
};

async function loadMemoryData() {
  try {
    var [archiveRes, activeRes] = await Promise.all([
      apiGet("memory/archives"),
      apiGet("memory/active"),
    ]);

    memoryState.archives = archiveRes.archives || [];
    memoryState.activeSessions = activeRes.sessions || [];

    renderMemoryLists();
    loadBotMemories();
  } catch (e) {
    console.error("Failed to load memory data:", e);
  }
}

function renderMemoryLists() {
  // Active sessions
  var activeList = document.getElementById("memory-active-list");
  var activeCount = document.getElementById("active-count");
  activeCount.textContent = memoryState.activeSessions.length;

  if (memoryState.activeSessions.length === 0) {
    activeList.innerHTML = '<div class="memory-empty">暂无活跃会话</div>';
  } else {
    activeList.innerHTML = memoryState.activeSessions
      .map(function (s) {
        var timeStr = formatMemoryTime(s.last_active);
        var title = s.book_title || s.session_id;
        return (
          '<div class="memory-card" onclick="openMemoryDetail(\'' +
          escapeAttr(s.session_id) +
          "', 'active')\">" +
          '<div class="memory-card-info">' +
          '<div class="memory-card-preview">📖 ' +
          escapeHtml(title) +
          "</div>" +
          '<div class="memory-card-meta"><span>' +
          timeStr +
          "</span></div>" +
          "</div>" +
          '<span class="memory-card-count">' +
          s.message_count +
          "条</span>" +
          "</div>"
        );
      })
      .join("");
  }

  // Archives
  var archiveList = document.getElementById("memory-archive-list");
  var archiveCount = document.getElementById("archive-count");
  archiveCount.textContent = memoryState.archives.length;

  if (memoryState.archives.length === 0) {
    archiveList.innerHTML = '<div class="memory-empty">暂无归档记录</div>';
  } else {
    archiveList.innerHTML = memoryState.archives
      .map(function (a) {
        var timeStr = formatMemoryTime(a.ended_at);
        var title = a.book_title || a.preview || a.session_id;
        return (
          '<div class="memory-card" onclick="openMemoryDetail(\'' +
          escapeAttr(a.session_id) +
          "', 'archive')\">" +
          '<div class="memory-card-info">' +
          '<div class="memory-card-preview">📖 ' +
          escapeHtml(title) +
          "</div>" +
          '<div class="memory-card-meta"><span>' +
          timeStr +
          "</span></div>" +
          "</div>" +
          '<span class="memory-card-count">' +
          a.message_count +
          "条</span>" +
          '<button class="memory-card-delete" onclick="event.stopPropagation(); deleteArchive(\'' +
          escapeAttr(a.session_id) +
          "')\">×</button>" +
          "</div>"
        );
      })
      .join("");
  }
}

function formatMemoryTime(timestamp) {
  if (!timestamp) return "";
  var d = new Date(timestamp * 1000);
  var now = new Date();
  var diff = now - d;

  if (diff < 0) {
    return "刚刚";
  }

  if (diff < 3600000) {
    return Math.floor(diff / 60000) + "分钟前";
  } else if (diff < 86400000) {
    return Math.floor(diff / 3600000) + "小时前";
  } else if (diff < 604800000) {
    return Math.floor(diff / 86400000) + "天前";
  } else {
    return (
      d.getMonth() + 1 + "/" + d.getDate() + " " +
      d.getHours().toString().padStart(2, "0") + ":" +
      d.getMinutes().toString().padStart(2, "0")
    );
  }
}

async function openMemoryDetail(sessionId, type) {
  memoryState.currentArchiveId = sessionId;
  memoryState.currentArchiveType = type;

  var modal = document.getElementById("memory-modal");
  var title = document.getElementById("memory-modal-title");
  var body = document.getElementById("memory-modal-body");
  var deleteAllBtn = document.getElementById("memory-modal-delete-all");

  title.textContent = type === "active" ? "当前会话详情" : "归档对话详情";
  body.innerHTML = '<div class="memory-empty">加载中...</div>';

  // show/hide delete all button based on type
  deleteAllBtn.style.display = type === "archive" ? "block" : "none";

  modal.classList.add("open");

  try {
    var messages = [];
    if (type === "archive") {
      var res = await apiGet("memory/archives/" + sessionId);
      messages = (res.archive && res.archive.chat_history) || [];
    } else {
      // for active sessions, we get messages from the active endpoint
      var res = await apiGet("memory/active");
      var session = (res.sessions || []).find(function (s) {
        return s.session_id === sessionId;
      });
      // active sessions don't have full messages in the list endpoint,
      // so we show what we have
      if (session) {
        // re-fetch from chat history API using session token
        // For now, show a note that active sessions can be viewed in the chat
        body.innerHTML =
          '<div class="memory-empty">活跃会话的对话可在阅读器聊天面板中查看</div>';
        return;
      }
    }

    if (messages.length === 0) {
      body.innerHTML = '<div class="memory-empty">暂无对话内容</div>';
      return;
    }

    body.innerHTML = messages
      .map(function (msg) {
        var roleClass = msg.role === "user" ? "user" : "bot";
        var roleLabel = msg.role === "user"
          ? (localStorage.getItem(USER_NICKNAME_KEY) || "我")
          : (localStorage.getItem(BOT_NICKNAME_KEY) || "Bot");
        return (
          '<div class="memory-msg-item">' +
          '<span class="memory-msg-role ' + roleClass + '">' + escapeHtml(roleLabel) + "</span>" +
          '<span class="memory-msg-content">' + escapeHtml(msg.content || "") + "</span>" +
          '<button class="memory-msg-delete" onclick="deleteMemoryMessage(\'' +
          escapeAttr(sessionId) + "', '" + type + "', '" +
          escapeAttr(msg.id || "") +
          "')\">×</button>" +
          "</div>"
        );
      })
      .join("");
  } catch (e) {
    body.innerHTML = '<div class="memory-empty">加载失败</div>';
    console.error("Failed to load archive detail:", e);
  }
}

function closeMemoryModal() {
  document.getElementById("memory-modal").classList.remove("open");
  memoryState.currentArchiveId = null;
  memoryState.currentArchiveType = null;
}

async function deleteMemoryMessage(sessionId, type, messageId) {
  if (!messageId) return;

  var endpoint =
    type === "archive"
      ? API_BASE + "/api/memory/archives/" + sessionId + "/messages/" + messageId
      : API_BASE + "/api/memory/active/" + sessionId + "/messages/" + messageId;

  try {
    var res = await fetch(endpoint, { method: "DELETE" });
    var data = await res.json();
    if (data.success) {
      // remove from DOM - find the button that triggered this and get its parent
      var btns = document.querySelectorAll(".memory-msg-delete");
      var item = null;
      for (var i = 0; i < btns.length; i++) {
        if (btns[i].getAttribute("onclick") &&
            btns[i].getAttribute("onclick").indexOf(messageId) !== -1) {
          item = btns[i].closest(".memory-msg-item");
          break;
        }
      }
      if (item) {
        item.style.opacity = "0";
        item.style.transform = "translateX(20px)";
        item.style.transition = "all 0.2s";
        setTimeout(function () {
          item.remove();
        }, 200);
      }
    }
  } catch (e) {
    console.error("Failed to delete message:", e);
  }
}

async function deleteArchive(sessionId) {
  if (!confirm("确定删除这段归档记录？删除后无法恢复。")) return;

  try {
    var res = await fetch(API_BASE + "/api/memory/archives/" + sessionId, {
      method: "DELETE",
    });
    var data = await res.json();
    if (data.success) {
      // refresh list
      memoryState.archives = memoryState.archives.filter(function (a) {
        return a.session_id !== sessionId;
      });
      renderMemoryLists();

      // close modal if this archive was open
      if (memoryState.currentArchiveId === sessionId) {
        closeMemoryModal();
      }
    }
  } catch (e) {
    console.error("Failed to delete archive:", e);
  }
}

async function deleteCurrentArchive() {
  if (!memoryState.currentArchiveId || memoryState.currentArchiveType !== "archive") return;
  await deleteArchive(memoryState.currentArchiveId);
}

// Bind memory management events
(function bindMemoryEvents() {
  document.addEventListener("DOMContentLoaded", function () {
    var closeBtn = document.getElementById("memory-modal-close");
    if (closeBtn) closeBtn.addEventListener("click", closeMemoryModal);

    var deleteAllBtn = document.getElementById("memory-modal-delete-all");
    if (deleteAllBtn) deleteAllBtn.addEventListener("click", deleteCurrentArchive);

    var refreshBtn = document.getElementById("btn-refresh-memory");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        refreshBtn.classList.add("spinning");
        loadMemoryData().then(function () {
          setTimeout(function () {
            refreshBtn.classList.remove("spinning");
          }, 600);
        });
      });
    }

    // close modal on overlay click
    var modal = document.getElementById("memory-modal");
    if (modal) {
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeMemoryModal();
      });
    }

    // note box send button
    var noteBoxSend = document.getElementById("note-box-send");
    if (noteBoxSend) noteBoxSend.addEventListener("click", sendNoteBox);
    var noteBoxTextarea = document.getElementById("note-box-textarea");
    if (noteBoxTextarea) {
      noteBoxTextarea.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendNoteBox(); }
      });
    }
  });
})();


// ==================== Pet House ====================

var petHouseState = {
  pets: [],
  pendingDeleteId: null,
};

/**
 * Render a single pet card HTML string from pet data.
 * @param {object} pet - { id, name, species, hunger, mood, photo_filename, animation_state }
 * @returns {string} HTML string for the pet card
 */
function renderPetCardHtml(pet) {
  var animState = pet.animation_state || "idle";
  var hungerPct = Math.min(100, Math.max(0, pet.hunger || 0));
  var moodPct = Math.min(100, Math.max(0, pet.mood || 0));
  var speciesClass = "pet-sprite-" + (pet.species || "cat");

  var photoHtml = "";
  if (pet.photo_filename) {
    photoHtml = '<img class="pet-card-photo" src="' + API_BASE + '/api/pets/' + pet.id + '/photo" alt="" />';
  }

  return '<div class="pet-card" data-pet-id="' + escapeAttr(pet.id) + '">' +
    photoHtml +
    '<button class="pet-card-delete-btn" data-pet-id="' + escapeAttr(pet.id) + '" title="删除">×</button>' +
    '<div class="pet-sprite-area">' +
      '<div class="pet-sprite ' + speciesClass + ' pet-anim-' + animState + '"></div>' +
    '</div>' +
    '<div class="pet-card-name" title="' + escapeAttr(pet.name) + '">' + escapeHtml(pet.name) + '</div>' +
    '<div class="pet-status-bars">' +
      '<div class="pet-bar-row">' +
        '<span class="pet-bar-label">饱</span>' +
        '<div class="pet-bar-track"><div class="pet-bar-fill hunger" style="width: ' + hungerPct + '%"></div></div>' +
        '<span class="pet-bar-value">' + hungerPct + '</span>' +
      '</div>' +
      '<div class="pet-bar-row">' +
        '<span class="pet-bar-label">心</span>' +
        '<div class="pet-bar-track"><div class="pet-bar-fill mood" style="width: ' + moodPct + '%"></div></div>' +
        '<span class="pet-bar-value">' + moodPct + '</span>' +
      '</div>' +
    '</div>' +
    '<div class="pet-card-actions">' +
      '<button class="pet-action-btn pet-btn-feed" data-pet-id="' + escapeAttr(pet.id) + '">🍖 投喂</button>' +
      '<button class="pet-action-btn pet-btn-pet" data-pet-id="' + escapeAttr(pet.id) + '">🤚 摸摸</button>' +
      '<button class="pet-action-btn pet-btn-customize" data-pet-id="' + escapeAttr(pet.id) + '" data-species="' + escapeAttr(pet.species || 'cat') + '">✨ 捏一捏</button>' +
    '</div>' +
  '</div>';
}

/**
 * Render all pet cards into the grid.
 * @param {Array} pets - array of pet data objects
 */
function renderPetHouse(pets) {
  petHouseState.pets = pets || [];
  var grid = document.getElementById("pet-cards-grid");
  if (!grid) return;

  if (petHouseState.pets.length === 0) {
    grid.innerHTML = '<div class="pet-empty-hint">还没有宠物，添加一只吧 🐾</div>';
  } else {
    grid.innerHTML = petHouseState.pets.map(renderPetCardHtml).join("");
  }

  // Bind delete buttons
  grid.querySelectorAll(".pet-card-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      openPetDeleteConfirm(btn.dataset.petId);
    });
  });

  // Bind feed buttons
  grid.querySelectorAll(".pet-btn-feed").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      feedPet(btn.dataset.petId);
    });
  });

  // Bind pet (摸摸) buttons
  grid.querySelectorAll(".pet-btn-pet").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      petPet(btn.dataset.petId);
    });
  });

  // Bind customize (捏一捏) buttons
  grid.querySelectorAll(".pet-btn-customize").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var petId = btn.dataset.petId;
      var species = btn.dataset.species;
      // Find the pet's customization_data from the loaded pets array
      var pet = petHouseState.pets.find(function (p) { return p.id === petId; });
      var customizationData = pet ? pet.customization_data : null;
      openCustomizationUI(petId, species, customizationData);
    });
  });

  // Apply dynamic sprites to all pet cards using the rendering engine.
  // This replaces the old hardcoded CSS ::after box-shadow with JS-generated styles.
  // Legacy pets (null customization_data) will use DEFAULT_CUSTOMIZATION fallback.
  if (typeof applyAllPetSprites === "function") {
    applyAllPetSprites(petHouseState.pets);
  }
}

/**
 * Load pet house data from server and render.
 */
async function loadPetHouse() {
  // skip if pet house is disabled (section hidden)
  var section = document.getElementById("pet-house-section");
  if (section && section.style.display === "none") return;
  var grid = document.getElementById("pet-cards-grid");
  if (!grid) return;
  try {
    var res = await apiGet("pets");
    if (res.success) {
      renderPetHouse(res.pets || []);
    } else {
      grid.innerHTML = '<div class="memory-empty">加载失败</div>';
    }
  } catch (e) {
    grid.innerHTML = '<div class="memory-empty">加载失败</div>';
  }
}

// ==================== Pet Creation Modal ====================

function openPetCreateModal() {
  var modal = document.getElementById("pet-create-modal");
  if (modal) {
    // Reset form
    var nameInput = document.getElementById("pet-name-input");
    var speciesSelect = document.getElementById("pet-species-select");
    var photoName = document.getElementById("pet-photo-name");
    var photoInput = document.getElementById("pet-photo-input");
    if (nameInput) nameInput.value = "";
    if (speciesSelect) speciesSelect.selectedIndex = 0;
    if (photoName) photoName.textContent = "未选择";
    if (photoInput) photoInput.value = "";
    modal.classList.add("open");
  }
}

function closePetCreateModal() {
  var modal = document.getElementById("pet-create-modal");
  if (modal) modal.classList.remove("open");
}

async function confirmCreatePet() {
  var nameInput = document.getElementById("pet-name-input");
  var speciesSelect = document.getElementById("pet-species-select");
  var name = nameInput ? nameInput.value.trim() : "";
  var species = speciesSelect ? speciesSelect.value : "cat";

  if (!name) {
    nameInput.focus();
    return;
  }

  try {
    var res = await apiPost("pets", { name: name, species: species });
    if (res.success && res.pet) {
      var newPetId = res.pet.id;
      // Upload photo if selected
      var photoInput = document.getElementById("pet-photo-input");
      if (photoInput && photoInput.files && photoInput.files[0]) {
        await uploadPetPhoto(newPetId, photoInput.files[0]);
      }
      closePetCreateModal();
      // Open customization UI for the new pet before displaying in main view.
      // Pass null for existingData to initialize with default appearance
      // (first template of species, orange primary, cream secondary, solid pattern, no accessory).
      // On confirm: confirmCustomization() saves via PUT API then refreshes pet house.
      // On cancel: pet already exists with no customization_data (renders with defaults),
      // but we still need to refresh the view to show the new pet.
      window._onCustomizationCancel = async function () {
        await loadPetHouse();
      };
      openCustomizationUI(newPetId, species, null);
    }
  } catch (e) {
    console.error("Failed to create pet:", e);
  }
}

async function uploadPetPhoto(petId, file) {
  var formData = new FormData();
  formData.append("file", file);
  try {
    await fetch(API_BASE + "/api/pets/" + petId + "/photo", {
      method: "POST",
      body: formData,
    });
  } catch (e) {
    console.error("Failed to upload pet photo:", e);
  }
}

// ==================== Pet Delete Confirmation ====================

function openPetDeleteConfirm(petId) {
  petHouseState.pendingDeleteId = petId;
  var overlay = document.getElementById("pet-delete-confirm");
  if (overlay) overlay.classList.add("open");
}

function closePetDeleteConfirm() {
  petHouseState.pendingDeleteId = null;
  var overlay = document.getElementById("pet-delete-confirm");
  if (overlay) overlay.classList.remove("open");
}

async function confirmDeletePet() {
  var petId = petHouseState.pendingDeleteId;
  if (!petId) return;
  try {
    var res = await apiDelete("pets/" + petId);
    if (res.success) {
      closePetDeleteConfirm();
      await loadPetHouse();
    }
  } catch (e) {
    console.error("Failed to delete pet:", e);
  }
  closePetDeleteConfirm();
}

// ==================== Pet Actions (feed/pet) ====================

async function feedPet(petId) {
  try {
    var res = await apiPost("pets/" + petId + "/feed", {});
    if (res.success) {
      // Play eating animation temporarily
      var card = document.querySelector('.pet-card[data-pet-id="' + petId + '"]');
      if (card) {
        var sprite = card.querySelector(".pet-sprite");
        if (sprite) {
          // Apply dynamic sprite with eating animation state
          var pet = petHouseState.pets.find(function (p) { return p.id === petId; });
          if (pet && typeof applyPetSprite === "function") {
            var customData = pet.customization_data;
            if (!customData && typeof DEFAULT_CUSTOMIZATION !== "undefined") {
              customData = DEFAULT_CUSTOMIZATION[pet.species] || DEFAULT_CUSTOMIZATION.cat;
            }
            applyPetSprite(sprite, customData, pet.species, "eating");
          } else {
            // Fallback: just swap the animation class
            sprite.className = sprite.className.replace(/pet-anim-\w+/g, "").trim();
            sprite.classList.add("pet-anim-eating");
          }
          // Revert after 2s
          setTimeout(function () {
            loadPetHouse();
          }, 2000);
        }
        // Update bars immediately (optimistic)
        if (res.pet) {
          var hungerFill = card.querySelector(".pet-bar-fill.hunger");
          var hungerVal = card.querySelector(".pet-bar-row:first-child .pet-bar-value");
          if (hungerFill) hungerFill.style.width = res.pet.hunger + "%";
          if (hungerVal) hungerVal.textContent = res.pet.hunger;
        }
      }
      // Show easter egg comment as toast
      if (res.comment) {
        showPetToast(res.comment);
      }
    }
  } catch (e) {
    console.error("Failed to feed pet:", e);
  }
}

async function petPet(petId) {
  try {
    var res = await apiPost("pets/" + petId + "/pet", {});
    if (res.success) {
      // Play being_petted animation temporarily
      var card = document.querySelector('.pet-card[data-pet-id="' + petId + '"]');
      if (card) {
        var sprite = card.querySelector(".pet-sprite");
        if (sprite) {
          // Apply dynamic sprite with being_petted animation state
          var pet = petHouseState.pets.find(function (p) { return p.id === petId; });
          if (pet && typeof applyPetSprite === "function") {
            var customData = pet.customization_data;
            if (!customData && typeof DEFAULT_CUSTOMIZATION !== "undefined") {
              customData = DEFAULT_CUSTOMIZATION[pet.species] || DEFAULT_CUSTOMIZATION.cat;
            }
            applyPetSprite(sprite, customData, pet.species, "being_petted");
          } else {
            // Fallback: just swap the animation class
            sprite.className = sprite.className.replace(/pet-anim-\w+/g, "").trim();
            sprite.classList.add("pet-anim-petted");
          }
          setTimeout(function () {
            loadPetHouse();
          }, 2000);
        }
        // Update bars immediately (optimistic)
        if (res.pet) {
          var moodFill = card.querySelector(".pet-bar-fill.mood");
          var moodVal = card.querySelector(".pet-bar-row:last-child .pet-bar-value");
          if (moodFill) moodFill.style.width = res.pet.mood + "%";
          if (moodVal) moodVal.textContent = res.pet.mood;
        }
      }
      // Show easter egg comment as toast
      if (res.comment) {
        showPetToast(res.comment);
      }
    }
  } catch (e) {
    console.error("Failed to pet:", e);
  }
}

// ==================== Pet Toast (Easter Egg Comments) ====================

function showPetToast(message) {
  // Reuse or create a toast element
  var toast = document.getElementById("pet-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "pet-toast";
    toast.className = "pet-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = "「" + message + "」";
  toast.classList.add("show");
  // Auto-dismiss after 3 seconds
  clearTimeout(toast._timer);
  toast._timer = setTimeout(function () {
    toast.classList.remove("show");
  }, 3000);
}

// ==================== Pet House Event Bindings ====================

(function () {
  document.addEventListener("DOMContentLoaded", function () {
    // Add pet button
    var addBtn = document.getElementById("pet-add-btn");
    if (addBtn) {
      addBtn.addEventListener("click", openPetCreateModal);
    }

    // Modal close button
    var modalClose = document.getElementById("pet-modal-close");
    if (modalClose) modalClose.addEventListener("click", closePetCreateModal);

    // Modal cancel button
    var modalCancel = document.getElementById("pet-modal-cancel");
    if (modalCancel) modalCancel.addEventListener("click", closePetCreateModal);

    // Modal confirm button
    var modalConfirm = document.getElementById("pet-modal-confirm");
    if (modalConfirm) modalConfirm.addEventListener("click", confirmCreatePet);

    // Modal overlay click to close
    var modalOverlay = document.getElementById("pet-create-modal");
    if (modalOverlay) {
      modalOverlay.addEventListener("click", function (e) {
        if (e.target === modalOverlay) closePetCreateModal();
      });
    }

    // Photo upload button
    var photoBtn = document.getElementById("pet-photo-btn");
    var photoInput = document.getElementById("pet-photo-input");
    if (photoBtn && photoInput) {
      photoBtn.addEventListener("click", function () { photoInput.click(); });
      photoInput.addEventListener("change", function () {
        var fileName = photoInput.files && photoInput.files[0] ? photoInput.files[0].name : "未选择";
        var photoName = document.getElementById("pet-photo-name");
        if (photoName) photoName.textContent = fileName;
      });
    }

    // Delete confirmation buttons
    var deleteCancel = document.getElementById("pet-delete-cancel");
    if (deleteCancel) deleteCancel.addEventListener("click", closePetDeleteConfirm);

    var deleteOk = document.getElementById("pet-delete-ok");
    if (deleteOk) deleteOk.addEventListener("click", confirmDeletePet);

    // Delete confirmation overlay click to close
    var deleteOverlay = document.getElementById("pet-delete-confirm");
    if (deleteOverlay) {
      deleteOverlay.addEventListener("click", function (e) {
        if (e.target === deleteOverlay) closePetDeleteConfirm();
      });
    }
  });
})();

