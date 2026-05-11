/**
 * 星夜书屋 - 首页 + 书架（分页）
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
};

// ==================== Init ====================

async function init() {
  restoreProfile();
  await loadBooks();
  // restore last selected book (after books are loaded so we can get the title)
  var lastBook = localStorage.getItem("shared_read_progress_selected_book");
  if (lastBook && state.books.find(function (b) { return b.id === lastBook; })) {
    state.currentBookId = lastBook;
    var book = state.books.find(function (b) { return b.id === lastBook; });
    state.currentBookTitle = book ? book.title : "";
    await startSession(lastBook);
  }
  bindEvents();
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

// ==================== Session ====================

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
      showCapsule();
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
  if (!file || !file.name.endsWith(".epub")) return;
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
  // load memory data when switching to tools tab
  if (tabName === "tools") loadMemoryData();
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
      container.innerHTML = "";
      res.messages.forEach(function (msg) {
        appendChatMsg(msg.role === "user" ? "user" : "bot", msg.content);
      });
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
      applyHighlights();
    }
  } catch (e) {
    document.getElementById("reader-body").innerHTML = '<p class="reader-placeholder">加载失败</p>';
  }

  saveReadingProgress();
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

  var thinkingId = appendChatMsg("bot", "思考中...", "thinking");

  try {
    var bookId = readerState.bookId || state.currentBookId;
    var result = await apiPost("chat/send", {
      book_id: bookId,
      content: content,
    });
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

  initDraggableFab();
  initDraggableChatPanel();
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
  var progress = getReadingProgress(bookId);
  if (progress && progress.totalChapters > 0) {
    var pct = Math.round(((progress.chapter + 1) / progress.totalChapters) * 100);
    document.getElementById("user-progress").style.width = pct + "%";
  } else {
    document.getElementById("user-progress").style.width = "0%";
  }
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
  var sel = window.getSelection();
  if (!sel || sel.isCollapsed) return;
  var text = sel.toString().trim();
  if (!text) return;

  var range = sel.getRangeAt(0);
  var container = range.commonAncestorContainer;
  var markEl = container.nodeType === 3 ? container.parentElement : container;

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
      return '<div class="note-item">' +
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

  container.querySelectorAll(".note-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
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

function formatHighlightText(text) {
  // kept for backward compat but not used anymore
  return '<span class="note-highlight-text">' + escapeHtml(text) + '</span>';
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

  function initStars() {
    canvas = document.getElementById("stars-canvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");
    resize();
    stars = [];
    for (var i = 0; i < STAR_COUNT; i++) {
      stars.push(createStar());
    }
    window.addEventListener("resize", resize);
    animate();
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
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];

      // move
      s.x += s.speedX;
      s.y += s.speedY;
      s.pulse += s.pulseSpeed;

      // wrap around
      if (s.x < -10) s.x = canvas.width + 10;
      if (s.x > canvas.width + 10) s.x = -10;
      if (s.y < -10) s.y = canvas.height + 10;
      if (s.y > canvas.height + 10) s.y = -10;

      // pulsing opacity
      var alpha = s.opacity * (0.6 + 0.4 * Math.sin(s.pulse));

      // draw heart
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(s.x, s.y);
      ctx.scale(s.size / 5, s.size / 5);
      ctx.fillStyle = "rgba(200, 160, 220, 0.8)";
      ctx.beginPath();
      ctx.moveTo(0, -3);
      ctx.bezierCurveTo(-5, -8, -10, -2, 0, 5);
      ctx.bezierCurveTo(10, -2, 5, -8, 0, -3);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }

    animId = requestAnimationFrame(animate);
  }

  // start after DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initStars);
  } else {
    initStars();
  }
})();

// ==================== Start ====================
init();

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
        var roleLabel = msg.role === "user" ? "她" : "bot";
        return (
          '<div class="memory-msg-item">' +
          '<span class="memory-msg-role ' + roleClass + '">' + roleLabel + "</span>" +
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
      // remove from DOM
      var item = event.target.closest(".memory-msg-item");
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
  });
})();
