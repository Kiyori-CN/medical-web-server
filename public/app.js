const API = "/api";
const PREFERENCE_KEY = "medical-exam-preferences";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  user: null,
  setupRequired: false,
  authMode: "login",
  settings: null,
  stats: null,
  todaySummary: null,
  libraryPath: { library: "", favorites: "", wrong: "" },
  libraryViewMode: "folders",
  libraryGuideExam: "",
  libraryGuideKind: "",
  libraryGuideTagsExpanded: { exam: true, kind: true },
  libraryGuideExpanded: new Set(),
  libraryGuideCache: {},
  libraryGuideLoading: new Set(),
  libraryRenderToken: 0,
  outline: null,
  outlineMode: "practice",
  session: null,
  sessionIndex: 0,
  sessionReturnTarget: null,
  adminReturnHash: "#library",
  accountReturnHash: "#library",
  studyReturnHash: "#library",
  profileReturnHash: "#profile",
  drafts: {},
  timerHandle: null,
  selectionTimer: null,
  gestureStart: null,
  suppressClickUntil: 0,
  navigationDirection: "",
  searchMode: "keyword",
  searchResults: [],
  searchMetadata: {},
  searchFields: { stem: true, answer: true, point: true, explanation: true },
  searchQuestionTypes: [],
  searchSolutionVisibility: { stem: true, answer: true, point: true, explanation: true },
  searchSettingsExpanded: true,
  lastSearchQuery: "",
  adminTab: "overview",
  adminData: null,
  profileAvatarDraft: null,
  profileEditing: false,
  publicProfile: null,
  comments: {},
  notes: {},
  myComments: null,
  myNotes: null,
  myLikes: null,
  messageTab: "system",
  messageData: null,
  theme: window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  avatarCrop: null,
  providerModels: {
    embedding: { status: "idle", items: [], open: false, error: "" },
    llm: { status: "idle", items: [], open: false, error: "" },
  },
  providerDiscoveryTimers: {},
  vectorPollHandle: null,
};

document.addEventListener("DOMContentLoaded", boot);
window.addEventListener("hashchange", route);
document.addEventListener("click", handleClick);
document.addEventListener("submit", handleSubmit);
document.addEventListener("pointerdown", handlePracticePointerDown, { passive: true });
document.addEventListener("pointerup", handlePracticePointerUp, { passive: true });
document.addEventListener("pointercancel", clearPracticeGesture, { passive: true });
document.addEventListener("keydown", handlePracticeKeydown);
window.addEventListener("resize", handlePracticeResize);

async function boot() {
  applyTheme();
  try {
    const [setup, me] = await Promise.all([
      api("/auth/setup", { authRedirect: false }),
      api("/auth/me", { authRedirect: false }),
    ]);
    state.setupRequired = Boolean(setup.setup_required);
    if (me.authenticated && me.user) {
      await enterApp(me.user);
      return;
    }
    showAuth(state.setupRequired ? "setup" : "login");
  } catch (error) {
    showAuth("login");
    setAuthError(error.message || "服务暂时不可用，请稍后重试");
  }
}

async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = getCookie("medical_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`${API}${path}`, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    body:
      options.body === undefined || options.body instanceof FormData
        ? options.body
        : JSON.stringify(options.body),
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : { error: await response.text() };
  if (!response.ok) {
    if (response.status === 401 && options.authRedirect !== false) {
      state.user = null;
      showAuth("login");
    }
    const error = new Error(payload.error || `请求失败（${response.status}）`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function getCookie(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

function showAuth(mode) {
  stopTimer();
  document.body.classList.remove("in-session", "outline-open");
  state.authMode = mode;
  $("#app-shell").hidden = true;
  $("#auth-page").hidden = false;
  const setup = mode === "setup";
  const register = mode === "register";
  $("#auth-heading").innerHTML = `
    <h2>${setup ? "初始化管理账号" : register ? "创建账号" : "欢迎回来"}</h2>
    <p class="section-copy">${
      setup
        ? "首次部署仅执行一次。完成后，该账号拥有系统管理权限。"
        : register
          ? "注册后即可同步保存练习进度、收藏与错题。"
          : "登录后继续你的检索与训练记录。"
    }</p>`;
  $("#auth-tabs").innerHTML = setup
    ? ""
    : `<button type="button" role="tab" class="${mode === "login" ? "active" : ""}" data-action="auth-mode" data-mode="login">登录</button>
       <button type="button" role="tab" class="${mode === "register" ? "active" : ""}" data-action="auth-mode" data-mode="register">注册</button>`;
  $("#auth-submit").textContent = setup ? "创建管理员并进入系统" : register ? "注册并登录" : "登录";
  $("#auth-password").autocomplete = register || setup ? "new-password" : "current-password";
  $("#auth-password-tip").textContent = register || setup
    ? "至少 10 位，并包含大小写字母、数字或符号中的三类。"
    : "";
  $("#auth-error").textContent = "";
  $("#auth-form").reset();
  setTimeout(() => $("#auth-username")?.focus(), 0);
}

function setAuthError(message) {
  const element = $("#auth-error");
  if (element) element.textContent = message || "";
}

async function enterApp(user) {
  state.user = user;
  state.comments = {};
  state.notes = {};
  state.myComments = null;
  state.myNotes = null;
  state.myLikes = null;
  state.messageData = null;
  $("#auth-page").hidden = true;
  $("#app-shell").hidden = false;
  syncUserChrome(user);
  try {
    [state.settings, state.stats] = await Promise.all([api("/settings"), api("/stats")]);
    $("#site-brand-name").textContent = state.settings.site_name || "医考知库";
    state.searchMode = state.settings.default_search_mode || "keyword";
  } catch (error) {
    showToast(error.message, "error");
  }
  if (!location.hash) {
    await navigateHash("#library", { replace: true });
    return;
  }
  await route();
}

async function route() {
  if (!state.user) return;
  const descriptor = parseRouteHash();
  const target = descriptor.target;
  if (target !== "admin") stopVectorPoll();
  if (target.startsWith("session/")) {
    document.body.classList.add("in-session");
    showPage("practice");
    setActiveNav("");
    const sessionId = target.slice("session/".length);
    if (!state.session || state.session.session_id !== sessionId) {
      await loadSession(sessionId, { returnHash: descriptor.returnHash });
    } else {
      renderPractice();
      startTimer();
    }
    return;
  }
  document.body.classList.remove("in-session", "outline-open");
  const allowed = ["library", "search", "favorites", "wrong", "profile", "profile-detail", "settings", "messages", "my-comments", "notes", "my-likes", "ai", "forum", "admin"];
  if (!allowed.includes(target)) {
    await navigateHash("#library", { replace: true });
    return;
  }
  const page = target;
  if (page === "admin" && state.user.role !== "admin") {
    await navigateHash("#library", { replace: true });
    return;
  }
  stopTimer();
  showPage(page);
  const studyPages = ["my-comments", "notes", "my-likes", "favorites", "wrong"];
  const accountPages = ["profile-detail", "settings", "messages"];
  if (studyPages.includes(page)) {
    state.studyReturnHash = descriptor.returnHash || "#library";
  }
  const studyNav = state.studyReturnHash.startsWith("#library") ? "library" : "profile";
  setActiveNav(studyPages.includes(page) ? studyNav : accountPages.includes(page) ? "profile" : page);
  if (page === "library" || page === "favorites" || page === "wrong") {
    state.sessionReturnTarget = null;
    state.libraryPath[page] = descriptor.path;
    if (descriptor.collectionId) {
      renderLibraryShell(page);
      await openCollection(page, descriptor.collectionId, descriptor.path);
    } else {
      if (page === "library") {
        try { state.todaySummary = await api("/study/today-summary"); } catch (error) { state.todaySummary = null; }
      }
      await renderLibrary(page);
    }
  } else if (page === "search") {
    try {
      state.settings = await api("/settings");
    } catch (error) {
      showToast(error.message, "error");
    }
    renderSearchPage();
  } else if (page === "profile") {
    await renderMyHomePage();
  } else if (page === "profile-detail") {
    await renderPublicProfilePage(descriptor.userId || state.user.user_id);
  } else if (page === "settings") {
    renderSettingsPage();
  } else if (page === "messages") {
    await renderMessagesPage();
  } else if (page === "my-comments") {
    await renderMyCommentsPage();
  } else if (page === "notes") {
    await renderMyNotesPage();
  } else if (page === "my-likes") {
    await renderMyLikesPage();
  } else if (page === "ai" || page === "forum") {
    renderPlaceholderPage(page);
  } else if (page === "admin") {
    await renderAdminPage();
  }
}

function showPage(name) {
  $$(".page").forEach((page) => {
    page.hidden = page.id !== `page-${name}`;
  });
  $("#workspace")?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
}

function setActiveNav(name) {
  $$('[data-nav]').forEach((item) => item.classList.toggle("active", item.dataset.nav === name));
}

function syncUserChrome(user) {
  document.body.dataset.userRole = user.role === "admin" ? "admin" : "user";
}

function applyTheme() {
  const dark = state.theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  const color = $("meta[name='theme-color']");
  if (color) color.content = dark ? "#0f1218" : "#f5f6fa";
  const button = $("#theme-toggle");
  if (button) {
    const label = dark ? "切换日间模式" : "切换夜间模式";
    button.setAttribute("aria-label", label);
    button.title = label;
  }
}

function toggleTheme() {
  state.theme = state.theme === "dark" ? "light" : "dark";
  applyTheme();
  savePreferences();
}

function avatarVisual(entity, fallbackName = "用户") {
  if (entity?.avatar_url) {
    return `<img src="${escapeAttr(entity.avatar_url)}" alt="" loading="lazy" decoding="async">`;
  }
  return `<span>${escapeHtml(entity?.avatar || [...String(fallbackName || "用户")][0]?.toUpperCase() || "用")}</span>`;
}

function parseRouteHash() {
  const raw = location.hash.replace(/^#/, "") || "library";
  const separator = raw.indexOf("?");
  const target = separator >= 0 ? raw.slice(0, separator) : raw;
  const params = new URLSearchParams(separator >= 0 ? raw.slice(separator + 1) : "");
  return {
    target,
    path: params.get("path") || "",
    collectionId: params.get("collection") || "",
    userId: params.get("user") || "",
    returnHash: normalizeStudyReturnHash(params.get("return") || ""),
  };
}

function normalizeStudyReturnHash(value, fallback = "") {
  const candidate = String(value || "").trim();
  if (!candidate.startsWith("#")) return fallback;
  const target = candidate.slice(1).split("?", 1)[0];
  const allowed = new Set(["library", "profile", "favorites", "wrong", "my-comments", "notes", "my-likes"]);
  return allowed.has(target) ? candidate : fallback;
}

function studyReturnLabel(hash = state.studyReturnHash) {
  const target = String(hash || "").replace(/^#/, "").split("?", 1)[0];
  return ({
    library: "题库",
    profile: "我的",
    favorites: "收藏",
    wrong: "错题",
    "my-comments": "我的评论",
    notes: "我的笔记",
    "my-likes": "我的点赞",
  })[target] || "上一页";
}

function internalReturnLabel(hash) {
  const target = String(hash || "").replace(/^#/, "").split("?", 1)[0];
  if (target.startsWith("session/")) return "题目";
  return ({ search: "搜题", "profile-detail": "个人资料", messages: "消息" })[target] || studyReturnLabel(hash);
}

function libraryRouteHash(kind, path = "", collectionId = "") {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (collectionId) params.set("collection", collectionId);
  const query = params.toString();
  return `#${kind}${query ? `?${query}` : ""}`;
}

async function navigateHash(hash, { replace = false } = {}) {
  const normalized = hash.startsWith("#") ? hash : `#${hash}`;
  if (location.hash === normalized) {
    await route();
    return;
  }
  if (replace) {
    history.replaceState(null, "", normalized);
    await route();
    return;
  }
  location.hash = normalized;
}

function collectionParentPath(relativePath) {
  const normalized = String(relativePath || "").replace(/\\/g, "/");
  const separator = normalized.lastIndexOf("/");
  return separator >= 0 ? normalized.slice(0, separator) : "";
}

function loadingMarkup(label = "正在加载") {
  return `<div class="card loading-state"><span class="loading-spinner" aria-hidden="true"></span><span>${escapeHtml(label)}</span></div>`;
}

function emptyMarkup(title, copy, icon = "·") {
  return `<div class="card empty-state"><span class="empty-icon" aria-hidden="true">${icon}</span><h3>${escapeHtml(title)}</h3><p>${escapeHtml(copy)}</p></div>`;
}

async function renderLibrary(kind, { preserveContent = false, preserveScroll = false } = {}) {
  state.outline = null;
  const scrollTop = preserveScroll ? window.scrollY : null;
  const requestToken = kind === "library" ? ++state.libraryRenderToken : null;
  if (preserveContent) syncLibraryViewToolbar();
  else renderLibraryShell(kind);
  if (kind === "library" && state.libraryViewMode === "guided") {
    await renderGuidedLibrary({ preserveScroll, requestToken });
    return;
  }
  const parent = state.libraryPath[kind] || "";
  try {
    const path = kind === "library"
      ? `/library?parent=${encodeURIComponent(parent)}`
      : `/study/${kind}/library?parent=${encodeURIComponent(parent)}`;
    const payload = await api(path);
    if (kind === "library" && (requestToken !== state.libraryRenderToken || state.libraryViewMode !== "folders")) return;
    renderLibraryPayload(kind, payload);
    restoreLibraryScroll(scrollTop);
  } catch (error) {
    if (kind === "library" && (requestToken !== state.libraryRenderToken || state.libraryViewMode !== "folders")) return;
    $(`#${kind}-content`).innerHTML = emptyMarkup("目录读取失败", error.message, "!");
    restoreLibraryScroll(scrollTop);
  }
}

function renderLibraryShell(kind) {
  const container = $(`#page-${kind}`);
  const meta = libraryMeta(kind);
  const heading = kind === "library" ? "" : secondaryPageHeader(meta.eyebrow);
  const viewToggle = kind === "library" ? `<div class="library-view-toolbar"><span>浏览方式</span><div class="library-view-switch" role="group" aria-label="题库浏览方式"><button class="${state.libraryViewMode === "folders" ? "active" : ""}" type="button" data-action="library-view-mode" data-mode="folders"><span aria-hidden="true">▦</span>目录</button><button class="${state.libraryViewMode === "guided" ? "active" : ""}" type="button" data-action="library-view-mode" data-mode="guided"><span aria-hidden="true">☷</span>分类</button></div></div>` : "";
  container.innerHTML = `${heading}${viewToggle}<div id="${kind}-content">${loadingMarkup("正在读取题库目录")}</div>`;
}

function syncLibraryViewToolbar() {
  $$('[data-action="library-view-mode"]').forEach((button) => {
    const active = button.dataset.mode === state.libraryViewMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function libraryMeta(kind) {
  if (kind === "favorites") return { eyebrow: "收藏" };
  if (kind === "wrong") return { eyebrow: "错题" };
  return { eyebrow: "题库" };
}

function renderLibraryPayload(kind, payload) {
  const content = $(`#${kind}-content`);
  const stats = kind === "library" ? renderTodaySummary() : "";
  const meta = libraryMeta(kind);
  const breadcrumbs = (payload.breadcrumbs || []).map((item, index) => `
    ${index ? "<span>/</span>" : ""}
    <button type="button" data-action="library-breadcrumb" data-kind="${kind}" data-path="${escapeAttr(item.path)}">${escapeHtml(index === 0 && kind !== "library" ? `全部${meta.eyebrow}` : item.name)}</button>`).join("");
  const items = [
    ...(payload.directories || []).map((item) => libraryItem(item, kind, "directory")),
    ...(payload.collections || []).map((item) => libraryItem(item, kind, "collection")),
  ].join("");
  const currentSaved = kind === "library" ? 0 : [
    ...(payload.directories || []).map((item) => Number(item.question_count || 0)),
    ...(payload.collections || []).map((item) => Number(item.saved_count ?? item.question_count ?? 0)),
  ].reduce((sum, value) => sum + value, 0);
  const total = kind === "library" ? "" : `<span>${payload.parent ? `当前 ${formatNumber(currentSaved)} · ` : ""}共 ${formatNumber(payload.total_saved || 0)} 道</span>`;
  content.innerHTML = `${stats}
    <div class="breadcrumbs">${breadcrumbs}${total}</div>
    ${items ? `<div class="library-grid">${items}</div>` : emptyMarkup(
      kind === "favorites" ? "暂无收藏" : kind === "wrong" ? "暂无错题" : "目录为空",
      kind === "favorites" ? "在搜题或练习页面点击收藏后，会按原文件路径显示在这里。" : kind === "wrong" ? "答错的客观题会自动归档到这里。" : "当前目录没有可用题库文件。",
      kind === "favorites" ? "☆" : kind === "wrong" ? "!" : "▦"
    )}`;
}

function restoreLibraryScroll(scrollTop) {
  if (!Number.isFinite(scrollTop)) return;
  requestAnimationFrame(() => {
    const maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    window.scrollTo({ top: Math.min(scrollTop, maximum), behavior: "auto" });
  });
}

async function renderGuidedLibrary({ preserveScroll = false, requestToken = null } = {}) {
  if (state.libraryViewMode !== "guided") return;
  const activeToken = requestToken ?? ++state.libraryRenderToken;
  const isCurrent = () => activeToken === state.libraryRenderToken && state.libraryViewMode === "guided";
  const content = $("#library-content");
  if (!content) return;
  const scrollTop = preserveScroll ? window.scrollY : null;
  if (!preserveScroll || !content.firstElementChild) {
    content.innerHTML = loadingMarkup("正在整理分类导航");
  }
  try {
    const currentParts = String(state.libraryPath.library || "").split("/").filter(Boolean);
    const root = await api("/library?parent=");
    if (!isCurrent()) return;
    const exams = root.directories || [];
    const requestedExam = state.libraryGuideExam || currentParts[0] || exams[0]?.path || "";
    const exam = exams.find((item) => item.path === requestedExam) || exams[0];
    if (!exam) {
      content.innerHTML = emptyMarkup("暂无题库分类", "当前题库没有可用目录。", "▦");
      restoreLibraryScroll(scrollTop);
      return;
    }
    state.libraryGuideExam = exam.path;
    const examPayload = await api(`/library?parent=${encodeURIComponent(exam.path)}`);
    if (!isCurrent()) return;
    const kinds = examPayload.directories || [];
    const requestedKind = state.libraryGuideKind || (currentParts[0] === exam.name && currentParts[1] ? `${exam.path}/${currentParts[1]}` : "") || kinds[0]?.path || "";
    const collectionKind = kinds.find((item) => item.path === requestedKind) || kinds[0];
    state.libraryGuideKind = collectionKind?.path || "";
    const branchPayload = collectionKind
      ? await api(`/library?parent=${encodeURIComponent(collectionKind.path)}`)
      : examPayload;
    if (!isCurrent()) return;
    const lead = renderTodaySummary();
    content.innerHTML = `${lead}<section class="card guided-library"><div class="guided-library-head"><h2>分类导航</h2><span>${formatNumber(branchPayload.directories?.length || branchPayload.collections?.length || 0)} 个分类</span></div>${renderGuideRail("考试", exams, exam.path, "exam")}${renderGuideRail("题库", kinds, collectionKind?.path || "", "kind")}<div class="guided-library-body">${renderGuideBranches(branchPayload)}</div></section>`;
    restoreLibraryScroll(scrollTop);
  } catch (error) {
    if (!isCurrent()) return;
    content.innerHTML = emptyMarkup("分类导航读取失败", error.message, "!");
    restoreLibraryScroll(scrollTop);
  }
}

function renderGuideRail(label, items, activePath, level) {
  if (!items.length) return "";
  const expanded = state.libraryGuideTagsExpanded[level] !== false;
  return `<div class="guide-tag-row"><div class="guide-tag-head"><strong>${label}</strong><button type="button" data-action="toggle-guide-tags" data-level="${level}" aria-expanded="${expanded}" aria-label="${expanded ? "收起" : "展开"}${label}标签">${expanded ? "收起" : "展开"}<span aria-hidden="true">${expanded ? "⌃" : "⌄"}</span></button></div><div class="guide-tags ${expanded ? "expanded" : "collapsed"}" role="tablist" aria-label="${label}">${items.map((item) => `<button class="guide-tag ${item.path === activePath ? "active" : ""}" type="button" role="tab" aria-selected="${item.path === activePath}" data-action="guide-select" data-level="${level}" data-path="${escapeAttr(item.path)}" title="${escapeAttr(item.name)}"><span class="guide-tag-check" aria-hidden="true"></span><span class="guide-tag-label">${escapeHtml(item.name)}</span><small>${formatNumber(item.collection_count || 0)}</small></button>`).join("")}</div></div>`;
}

function renderGuideBranches(payload) {
  const directories = payload.directories || [];
  const collections = payload.collections || [];
  const directFiles = collections.length ? `<section class="guide-direct-files"><h3>题库文件</h3>${renderGuideFiles(collections)}</section>` : "";
  const branches = directories.map((directory) => {
    const expanded = state.libraryGuideExpanded.has(directory.path);
    const loading = state.libraryGuideLoading.has(directory.path);
    const cached = state.libraryGuideCache[directory.path];
    const body = loading
      ? `<div class="guide-branch-loading"><span class="loading-spinner"></span><span>正在读取目录</span></div>`
      : cached ? renderGuideTreeNode(cached) : "";
    return `<article class="guide-branch ${expanded ? "open" : ""}"><button class="guide-branch-trigger" type="button" data-action="toggle-guide-branch" data-path="${escapeAttr(directory.path)}" aria-expanded="${expanded}"><span class="guide-branch-icon" aria-hidden="true">${expanded ? "−" : "+"}</span><span class="guide-branch-copy"><strong>${escapeHtml(directory.name)}</strong><small>${formatNumber(directory.collection_count || 0)} 个文件 · ${formatNumber(directory.question_count || 0)} 道题</small></span><span class="guide-branch-arrow" aria-hidden="true">⌄</span></button><div class="guide-branch-body" ${expanded ? "" : "hidden"}>${body}</div></article>`;
  }).join("");
  return `${directFiles}${branches || (!directFiles ? emptyMarkup("暂无可展开目录", "当前分类没有题库文件。", "▦") : "")}`;
}

function renderGuideTreeNode(node, depth = 0) {
  const files = renderGuideFiles(node.collections || []);
  const directories = (node.directories || []).map((directory) => `<details class="guide-nested-folder" ${depth === 0 ? "open" : ""}><summary><span>${escapeHtml(directory.name)}</span><small>${formatNumber(directory.collection_count || 0)} 个文件</small></summary><div class="guide-nested-content">${renderGuideTreeNode(directory, depth + 1)}</div></details>`).join("");
  return `${files}${directories}` || `<p class="guide-empty">该目录暂无题库文件</p>`;
}

function renderGuideFiles(collections) {
  if (!collections.length) return "";
  return `<div class="guide-file-list">${collections.map((item) => `<button type="button" data-action="open-collection" data-kind="library" data-parent-path="${escapeAttr(collectionParentPath(item.relative_path || ""))}" data-id="${escapeAttr(item.collection_id || "")}"><span class="guide-file-copy"><strong>${escapeHtml(String(item.name || "").replace(/\.xlsx$/i, ""))}</strong><small>${formatNumber(item.question_count || 0)} 题</small></span><b aria-hidden="true">›</b></button>`).join("")}</div>`;
}

async function selectGuideLevel(level, path) {
  if (!path) return;
  if (level === "exam") {
    state.libraryGuideExam = path;
    state.libraryGuideKind = "";
    state.libraryGuideExpanded.clear();
  } else {
    state.libraryGuideKind = path;
    state.libraryGuideExpanded.clear();
  }
  await renderGuidedLibrary({ preserveScroll: true });
}

async function toggleGuideBranch(path) {
  if (!path) return;
  if (state.libraryGuideExpanded.has(path)) {
    state.libraryGuideExpanded.delete(path);
    await renderGuidedLibrary({ preserveScroll: true });
    return;
  }
  state.libraryGuideExpanded.add(path);
  if (!state.libraryGuideCache[path]) {
    state.libraryGuideLoading.add(path);
    await renderGuidedLibrary({ preserveScroll: true });
    try {
      state.libraryGuideCache[path] = await api(`/library/tree?parent=${encodeURIComponent(path)}`);
    } catch (error) {
      state.libraryGuideExpanded.delete(path);
      showToast(error.message, "error");
    } finally {
      state.libraryGuideLoading.delete(path);
    }
  }
  await renderGuidedLibrary({ preserveScroll: true });
}

function renderTodaySummary() {
  const summary = state.todaySummary || {};
  const updated = summary.updated_at ? new Date(summary.updated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "--:--:--";
  const metrics = [
    ["score", summary.score || 0, "预估分"],
    ["duration_minutes", `${summary.duration_minutes || 0}m`, "答题时长"],
    ["questions", summary.questions || 0, "做题数量"],
    ["accuracy", `${summary.accuracy || 0}%`, "总正确率"],
    ["rank_label", summary.rank_label || "未上榜", "刷题榜单"],
  ];
  const shortcuts = [
    ["wrong", "错题", "!", "wrong", "wrong"],
    ["favorites", "收藏", "♡", "favorites", "favorites"],
    ["notes", "笔记", "▢", "notes", "notes"],
    ["my-comments", "评论", "▱", "comments", "comments"],
    ["my-likes", "点赞", "♧", "likes", "likes"],
  ];
  return `<section class="today-summary-card card" aria-label="今日统计">
    <div class="today-summary-head"><h2>今日统计</h2><time>更新时间 ${updated}</time></div>
    <div class="today-summary-metrics">${metrics.map(([key, value, label], index) => index === 0
      ? `<div class="today-score"><span>${escapeHtml(String(value))}</span><small>${label}</small></div>`
      : `<div class="today-metric"><strong>${escapeHtml(String(value))}</strong><small>${label}</small></div>`).join("")}</div>
  </section>
  <nav class="today-shortcuts card" aria-label="学习入口">${shortcuts.map(([page, label, icon, palette, metricKey]) => `<a class="today-shortcut" href="${escapeAttr(studyPageHash(page, "#library"))}" data-action="open-personal-page" data-page="${page}" data-return-hash="#library"><span class="today-shortcut-icon shortcut-${palette}">${icon}</span><strong>${label}</strong><small>${formatNumber(summary.totals?.[metricKey] || 0)}</small></a>`).join("")}</nav>`;
}

function secondaryPageHeader(label, copy = "") {
  return `<div class="secondary-page-header"><div><p class="eyebrow">${escapeHtml(label)}</p>${copy ? `<span>${escapeHtml(copy)}</span>` : ""}</div><button class="button button-secondary button-small" type="button" data-action="return-from-study">‹ 返回${escapeHtml(studyReturnLabel())}</button></div>`;
}

function studyPageHash(page, returnHash = "#library") {
  return `#${page}?return=${encodeURIComponent(normalizeStudyReturnHash(returnHash, "#library"))}`;
}

function statCard(label, value, copy) {
  return `<div class="stat-card"><small>${label}</small><strong>${formatNumber(value || 0)}</strong>${copy ? `<span>${copy}</span>` : ""}</div>`;
}

function invalidateStudySummary() {
  state.todaySummary = null;
}

function libraryItem(item, kind, type) {
  const isDirectory = type === "directory";
  const action = isDirectory ? "open-directory" : "open-collection";
  const count = item.saved_count ?? item.question_count ?? 0;
  const extension = String(item.extension || "").replace(/^\./, "").toLowerCase();
  const displayName = String(item.name || "").replace(/\.xlsx$/i, "");
  const copy = isDirectory
    ? `${formatNumber(item.collection_count || 0)} 个文件 · ${formatNumber(item.question_count || 0)} 道题`
    : `${formatNumber(count)} 道题${extension && extension !== "xlsx" ? ` · ${escapeHtml(extension.toUpperCase())}` : ""}`;
  return `<button class="library-item ${isDirectory ? "" : "collection"}" type="button" data-action="${action}" data-kind="${kind}" data-path="${escapeAttr(item.path || "")}" data-id="${escapeAttr(item.collection_id || "")}">
    ${isDirectory ? `<span class="library-icon" aria-hidden="true">▦</span>` : ""}
    <span class="library-main"><strong>${escapeHtml(displayName)}</strong><small>${copy}</small></span>
    <span class="library-arrow" aria-hidden="true">›</span>
  </button>`;
}

async function openCollection(kind, collectionId, parentPath = state.libraryPath[kind] || "") {
  state.outline = null;
  const content = $(`#${kind}-content`);
  content.innerHTML = loadingMarkup("正在读取题号目录");
  try {
    const path = kind === "library"
      ? `/practice/collections/${encodeURIComponent(collectionId)}/outline`
      : `/study/${kind}/collections/${encodeURIComponent(collectionId)}/outline`;
    const payload = await api(path);
    state.libraryPath[kind] = parentPath || collectionParentPath(payload.collection?.relative_path);
    state.outline = { kind, payload };
    state.outlineMode = "practice";
    renderOutline();
  } catch (error) {
    content.innerHTML = emptyMarkup("题号目录读取失败", error.message, "!");
  }
}

function renderOutline() {
  if (!state.outline) return;
  const { kind, payload } = state.outline;
  const content = $(`#${kind}-content`);
  const collection = payload.collection;
  const outlineItems = payload.items || [];
  const hasItems = outlineItems.length > 0;
  const groups = groupOutlineItems(outlineItems);
  const groupMarkup = groups.map(([label, items]) => `
    <section class="outline-group">
      <h3 class="outline-group-title"><span class="type-dot ${typeClass(label)}"></span><span>${escapeHtml(label)}</span><span>${items.length} 题</span></h3>
      ${kind === "library" ? `<div class="number-grid">${items.map((item) => `
        <button class="number-button ${escapeAttr(item.status || "")}" type="button" title="${escapeAttr(item.source_code || `第 ${item.collection_position} 题`)} · ${escapeAttr(label)}" data-action="start-session" data-kind="${kind}" data-id="${escapeAttr(collection.collection_id)}" data-position="${item.collection_position}">${item.collection_position}</button>`).join("")}</div>` : `<div class="saved-outline-list">${items.map((item) => `
        <div class="saved-outline-item">
          <button class="saved-outline-open" type="button" data-action="start-session" data-kind="${kind}" data-id="${escapeAttr(collection.collection_id)}" data-position="${item.collection_position}"><span>${item.collection_position}</span><strong>${escapeHtml(item.source_code || `第 ${item.collection_position} 题`)}</strong></button>
          <button class="button button-danger button-small saved-outline-remove" type="button" data-action="remove-saved-outline" data-kind="${kind}" data-key="${escapeAttr(item.saved?.item_key || "")}">${kind === "wrong" ? "删除错题" : "取消收藏"}</button>
        </div>`).join("")}</div>`}
    </section>`).join("");
  const summary = payload.summary || {};
  content.innerHTML = `
    <div class="breadcrumbs"><button type="button" data-action="close-outline" data-kind="${kind}">‹ 返回当前目录</button></div>
    <div class="page-header">
      <div class="page-title"><h1>${escapeHtml(fileTitle(collection.relative_path))}</h1><p class="section-copy">${escapeHtml(displaySourcePath(collection.relative_path))} · ${formatNumber((payload.items || []).length)} 题</p></div>
      ${hasItems ? `<div class="header-actions"><button class="button button-primary" type="button" data-action="start-session" data-kind="${kind}" data-id="${escapeAttr(collection.collection_id)}" data-position="${outlineItems[0].collection_position}">${modeMeta(state.outlineMode).cta}</button></div>` : ""}
    </div>
    ${hasItems ? `<div class="card outline-card">
      <div class="outline-toolbar">
        <div class="outline-mode-row">
          <div class="segmented" aria-label="学习模式">
            ${["practice", "speed", "test", "memorize"].map((mode) => `<button class="${state.outlineMode === mode ? "active" : ""}" type="button" data-action="outline-mode" data-mode="${mode}">${modeMeta(mode).label}</button>`).join("")}
          </div>
          <button class="button button-secondary button-small outline-reset" type="button" data-action="reset-collection-progress" data-id="${escapeAttr(collection.collection_id)}">重置</button>
        </div>
        <p class="mode-description">${modeMeta(state.outlineMode).description}</p>
        <span class="outline-summary">已答 ${summary.answered || 0} · 正确 ${summary.correct || 0} · 错误 ${summary.wrong || 0} · 未答 ${Math.max(0, (summary.total || payload.items.length) - (summary.answered || 0))}</span>
      </div>
      <div class="outline-groups">${groupMarkup}</div>
    </div>` : emptyMarkup(kind === "wrong" ? "该文件已没有错题" : kind === "favorites" ? "该文件已没有收藏" : "没有可练习题目", kind === "library" ? "该文件当前没有可练习题目。" : "返回当前目录继续查看其他文件。", kind === "wrong" ? "!" : kind === "favorites" ? "☆" : "▦")}`;
}

async function resetCollectionProgress(button) {
  const outline = state.outline;
  const collectionId = button.dataset.id;
  if (!outline || !collectionId) return;
  const confirmed = await confirmModal(
    "重置刷题进度",
    "确认清空当前题库的已答、正确和错误状态？收藏、错题、评论与历史试卷不会被删除。",
    "确认重置"
  );
  if (!confirmed) return;
  setButtonBusy(button, true, "重置中");
  try {
    const result = await api(`/practice/collections/${encodeURIComponent(collectionId)}/progress/reset`, {
      method: "POST",
      body: {},
    });
    if (state.outline === outline) {
      outline.payload.items = (outline.payload.items || []).map((item) => ({
        ...item,
        status: "unanswered",
        progress: null,
      }));
      outline.payload.summary = {
        ...(outline.payload.summary || {}),
        total: outline.kind === "library" ? result.summary.total : outline.payload.items.length,
        answered: 0,
        correct: 0,
        wrong: 0,
        ungraded: 0,
      };
      outline.payload.active_session = null;
      renderOutline();
    }
    showToast(result.reset_count ? `已重置 ${result.reset_count} 道题的刷题状态` : "当前题库暂无需要重置的进度");
  } catch (error) {
    showToast(error.message, "error");
    setButtonBusy(button, false);
  }
}

function groupOutlineItems(items) {
  const groups = new Map();
  items.forEach((item) => {
    const label = item.type_label || item.raw_type || item.question_type || "其他题型";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(item);
  });
  return [...groups.entries()];
}

function typeClass(label) {
  const value = String(label || "").toUpperCase();
  if (value.startsWith("B")) return "type-b";
  if (value.startsWith("X") || value.includes("多选")) return "type-x";
  if (["简答", "论述", "名词", "填空"].some((part) => value.includes(part))) return "type-subjective";
  return "";
}

function modeMeta(mode) {
  const modes = {
    practice: {
      label: "练习模式",
      short: "练习",
      cta: "开始练习",
      description: "逐题作答，提交后立即查看正确答案与解析。",
    },
    speed: {
      label: "快刷模式",
      short: "快刷",
      cta: "开始快刷",
      description: "选对即进入下一题，选错停留并展开解析。",
    },
    test: {
      label: "测试模式",
      short: "测试",
      cta: "开始测试",
      description: "作答后自动进入下一题，交卷前不显示对错。",
    },
    memorize: {
      label: "背题模式",
      short: "背题",
      cta: "开始背题",
      description: "直接查看答案、考点与解析，不需要提交作答。",
    },
  };
  return modes[mode] || modes.practice;
}

function renderSearchPage() {
  const vector = state.settings?.vector_index || {};
  const semanticReady = vector.status === "ready" && vector.semantic_enabled && state.searchFields.stem;
  const container = $("#page-search");
  container.innerHTML = `
    <div class="search-hero">
      <form id="search-form">
        <div class="search-box"><input id="search-input" name="query" type="search" maxlength="200" value="${escapeAttr(state.lastSearchQuery)}" placeholder="输入关键词或原题号" required><button class="button button-primary" type="submit">搜题</button></div>
        <div class="search-preference-panel">
          <div class="search-preference-toolbar">
            <strong>搜索设置</strong>
            <div class="search-preference-toolbar-actions">
              <button class="search-preference-reset" type="button" data-action="reset-search-settings">重置</button>
              <button class="search-preference-toggle" type="button" data-action="toggle-search-settings" aria-expanded="${state.searchSettingsExpanded}" aria-controls="search-preference-body"><span aria-hidden="true">${state.searchSettingsExpanded ? "⌃" : "⌄"}</span>${state.searchSettingsExpanded ? "收起" : "展开"}</button>
            </div>
          </div>
          <div id="search-preference-body" class="search-preference-body" ${state.searchSettingsExpanded ? "" : "hidden"}>
          <div class="search-control-row">
            <span class="search-control-label">搜索模式</span>
            <div class="search-mode-list" role="radiogroup" aria-label="搜索模式">
              ${searchModeButton("keyword", "关键词", true)}
              ${searchModeButton("semantic", "语义", semanticReady)}
              ${searchModeButton("hybrid", "混合", semanticReady)}
            </div>
          </div>
          <div class="search-control-row">
            <span class="search-control-label">搜索题型</span>
            <div class="search-type-list ${isAllSearchQuestionTypes() ? "all-selected" : ""}" role="group" aria-label="搜索题型">
              ${searchAllButton("question-types", isAllSearchQuestionTypes())}
              ${renderSearchQuestionTypes()}
            </div>
          </div>
          <div class="search-control-row">
            <span class="search-control-label">搜索范围</span>
            <div class="search-toggle-grid columns-4" role="group" aria-label="搜索范围">
              ${searchAllButton("search-fields", allSearchFieldsSelected())}
              ${searchFieldButton("stem", "题干", 1)}
              ${searchFieldButton("answer", "答案", 2)}
              ${searchFieldButton("point", "考点还原", 3)}
              ${searchFieldButton("explanation", "标准解析", 4)}
            </div>
          </div>
          <div class="search-control-row">
            <span class="search-control-label">显示内容</span>
            <div class="search-toggle-grid columns-4" role="group" aria-label="结果显示内容">
              ${searchAllButton("search-content", allSearchSolutionSelected())}
              ${searchSolutionButton("stem", "题干", 1)}
              ${searchSolutionButton("answer", "答案", 2)}
              ${searchSolutionButton("point", "考点还原", 3)}
              ${searchSolutionButton("explanation", "标准解析", 4)}
            </div>
          </div>
          </div>
        </div>
      </form>
    </div>
    <div id="search-results">${state.searchResults.length ? renderSearchResults(state.searchResults, state.searchMetadata) : ""}</div>`;
  refreshFavoriteButtons();
}

function searchModeButton(mode, label, enabled) {
  const active = state.searchMode === mode || (!enabled && state.searchMode === mode && mode === "keyword");
  const palette = { keyword: 0, semantic: 1, hybrid: 2 }[mode] ?? 0;
  return `<button class="search-filter-toggle search-mode-toggle palette-${palette} ${active ? "active" : ""}" type="button" role="radio" data-action="search-mode" data-mode="${mode}" aria-checked="${active}" ${enabled ? "" : "disabled"}><span aria-hidden="true"></span>${label}</button>`;
}

function searchAllButton(group, active) {
  return `<button class="search-filter-toggle search-all-toggle palette-0 ${active ? "active" : ""}" type="button" role="checkbox" data-action="search-all-toggle" data-group="${group}" aria-checked="${active}"><span aria-hidden="true"></span>全部</button>`;
}

function searchSolutionButton(field, label, palette = searchFieldPalette(field)) {
  const active = !allSearchSolutionSelected() && state.searchSolutionVisibility[field] !== false;
  return `<button class="search-filter-toggle palette-${palette} ${active ? "active" : ""}" type="button" data-action="search-solution-toggle" data-field="${field}" aria-pressed="${active}"><span aria-hidden="true"></span>${label}</button>`;
}

function searchFieldButton(field, label, palette = searchFieldPalette(field)) {
  const active = !allSearchFieldsSelected() && state.searchFields[field] !== false;
  return `<button class="search-filter-toggle palette-${palette} ${active ? "active" : ""}" type="button" data-action="search-field-toggle" data-field="${field}" aria-pressed="${active}"><span aria-hidden="true"></span>${label}</button>`;
}

function searchFieldPalette(field) {
  return ({ stem: 0, answer: 1, point: 2, explanation: 3 })[field] ?? 0;
}

function searchQuestionTypeOptions() {
  const counts = state.stats?.by_question_type || state.stats?.by_type || {};
  const preferred = [
    "A1型题", "A2型题", "A3/A4型题", "B型题", "B1型题", "C型题", "单选题",
    "X型题", "多选题", "判断题", "填空题", "名词解释", "简答题", "论述题",
    "知识点", "未知题型",
  ];
  const available = Object.keys(counts).filter(Boolean);
  const ordered = [
    ...preferred.filter((type) => available.includes(type)),
    ...available.filter((type) => !preferred.includes(type)).sort((left, right) => left.localeCompare(right, "zh-CN")),
  ];
  return { counts, ordered };
}

function isAllSearchQuestionTypes() {
  const { ordered } = searchQuestionTypeOptions();
  return state.searchQuestionTypes.length === 0
    || (ordered.length > 0 && ordered.every((type) => state.searchQuestionTypes.includes(type)));
}

function allSearchFieldsSelected() {
  return Object.values(state.searchFields).every(Boolean);
}

function allSearchSolutionSelected() {
  return Object.values(state.searchSolutionVisibility).every(Boolean);
}

function renderSearchQuestionTypes() {
  const { counts, ordered } = searchQuestionTypeOptions();
  const allActive = isAllSearchQuestionTypes();
  return ordered.map((type, index) => {
    const active = !allActive && state.searchQuestionTypes.includes(type);
    const label = type;
    return `<button class="search-filter-toggle search-type-toggle palette-${(index + 1) % 12} ${active ? "active" : ""}" type="button" role="checkbox" data-action="search-question-type" data-type="${escapeAttr(type)}" aria-checked="${active}" title="${escapeAttr(`${label} · ${counts[type]} 题`)}"><span aria-hidden="true"></span>${escapeHtml(label)}</button>`;
  }).join("");
}

async function toggleSearchAll(group) {
  if (group === "question-types") state.searchQuestionTypes = [];
  if (group === "search-fields") state.searchFields = { stem: true, answer: true, point: true, explanation: true };
  if (group === "search-content") state.searchSolutionVisibility = { stem: true, answer: true, point: true, explanation: true };
  savePreferences();
  renderSearchPage();
  if (state.lastSearchQuery) await runSearch(state.lastSearchQuery);
}

async function toggleSearchQuestionType(type) {
  const normalized = String(type || "");
  if (!normalized) {
    return toggleSearchAll("question-types");
  } else {
    const { ordered } = searchQuestionTypeOptions();
    const selected = new Set(isAllSearchQuestionTypes() ? [] : state.searchQuestionTypes);
    selected.has(normalized) ? selected.delete(normalized) : selected.add(normalized);
    state.searchQuestionTypes = ordered.length && ordered.every((type) => selected.has(type))
      ? []
      : [...selected];
  }
  savePreferences();
  renderSearchPage();
  if (state.lastSearchQuery) await runSearch(state.lastSearchQuery);
}

async function toggleSearchField(field) {
  if (!Object.prototype.hasOwnProperty.call(state.searchFields, field)) return;
  if (allSearchFieldsSelected()) {
    state.searchFields = { stem: false, answer: false, point: false, explanation: false };
    state.searchFields[field] = true;
  } else {
    const activeCount = Object.values(state.searchFields).filter(Boolean).length;
    if (state.searchFields[field] && activeCount === 1) {
      showToast("至少保留一个搜索范围", "error");
      return;
    }
    state.searchFields[field] = !state.searchFields[field];
  }
  if (!state.searchFields.stem && state.searchMode !== "keyword") {
    state.searchMode = "keyword";
    showToast("语义与混合搜索需要题干范围，已切换为关键词搜索");
  }
  savePreferences();
  renderSearchPage();
  if (state.lastSearchQuery) await runSearch(state.lastSearchQuery);
}

async function toggleSearchSolution(field) {
  if (!Object.prototype.hasOwnProperty.call(state.searchSolutionVisibility, field)) return;
  if (allSearchSolutionSelected()) {
    state.searchSolutionVisibility = { stem: false, answer: false, point: false, explanation: false };
    state.searchSolutionVisibility[field] = true;
  } else {
    state.searchSolutionVisibility[field] = !state.searchSolutionVisibility[field];
  }
  savePreferences();
  renderSearchPage();
  if (field !== "stem" && state.lastSearchQuery) await runSearch(state.lastSearchQuery);
}

function toggleSearchSettings() {
  state.searchSettingsExpanded = !state.searchSettingsExpanded;
  savePreferences();
  renderSearchPage();
}

async function resetSearchSettings() {
  state.searchMode = "keyword";
  state.searchFields = { stem: true, answer: true, point: true, explanation: true };
  state.searchQuestionTypes = [];
  state.searchSolutionVisibility = { stem: true, answer: true, point: true, explanation: true };
  state.searchSettingsExpanded = true;
  savePreferences();
  renderSearchPage();
  showToast("搜索标签已重置");
  if (state.lastSearchQuery) await runSearch(state.lastSearchQuery);
}

function renderSearchResults(results, metadata = {}) {
  if (!results.length) return emptyMarkup("未找到匹配题目", "可缩短题干、去掉标点或切换搜索方式后重试。", "⌕");
  return `<div class="results-header"><h2>检索结果</h2><span>${results.length} 条 · ${escapeHtml(metadata.mode || state.searchMode)}</span></div>
    <div class="result-list">${results.map(searchResultCard).join("")}</div>`;
}

function searchResultCard(item) {
  const source = (item.sources || [])[0] || {};
  const showStem = state.searchSolutionVisibility.stem !== false;
  return `<article class="card question-card">
    <div class="question-meta"><span class="badge">${escapeHtml(item.type_label || item.raw_type || item.question_type)}</span>${item.source_code ? `<span class="badge badge-neutral">${escapeHtml(item.source_code)}</span>` : ""}<span class="badge badge-neutral">${item.source_count || 0} 个来源</span></div>
    ${showStem ? `<p class="question-stem">${searchHighlightText(item.stem)}</p>${renderOptions(item.options || [], state.searchSolutionVisibility.answer ? item.answer : "", searchHighlightText)}` : ""}
    ${renderSolution(item, state.searchSolutionVisibility, searchHighlightText)}
    <p class="source-path">${escapeHtml(source.relative_path ? displaySourcePath(source.relative_path) : "来源路径未记录")}</p>
    <div class="question-actions">
      <button class="button button-secondary button-small favorite-button" type="button" data-action="toggle-favorite" data-occurrence="${source.occurrence_id || ""}" data-question="${escapeAttr(item.question_id)}" data-favorited="false" aria-pressed="false"><span class="favorite-icon" aria-hidden="true">☆</span><span class="favorite-label">收藏</span></button>
      ${source.collection_id ? `<button class="button button-quiet button-small" type="button" data-action="practice-source" data-id="${escapeAttr(source.collection_id)}" data-path="${escapeAttr(collectionParentPath(source.relative_path))}" data-position="${source.position || 1}">练习此文件</button>` : ""}
    </div>
  </article>`;
}

function renderOptions(options, correctAnswer = "", textRenderer = escapeHtml) {
  if (!options.length) return "";
  const correct = new Set(String(correctAnswer || "").toUpperCase().match(/[A-Z]/g) || []);
  return `<ul class="option-list">${options.map((option) => {
    const isCorrect = correct.has(String(option.label || "").toUpperCase());
    return `<li class="option-row ${isCorrect ? "correct" : ""}"${isCorrect ? ' aria-label="正确答案"' : ""}><span class="option-label">${escapeHtml(option.label)}</span><span>${textRenderer(option.text)}</span>${isCorrect ? `<span class="option-state" aria-hidden="true"></span>` : ""}</li>`;
  }).join("")}</ul>`;
}

function renderSolution(question, visibility = null, textRenderer = escapeHtml) {
  const showAnswer = visibility?.answer !== false;
  const showPoint = visibility?.point !== false;
  const showExplanation = visibility?.explanation !== false;
  const answer = showAnswer ? question.answer ?? "" : "";
  const userAnswer = question.user_answer;
  const isCorrect = question.is_correct;
  const point = showPoint ? question.point ?? "" : "";
  const explanation = showExplanation ? question.explanation ?? "" : "";
  if (!answer && !point && !explanation) return "";
  return `<div class="solution-stack">
    ${answer ? renderAnswerSolution(answer, userAnswer, isCorrect, textRenderer) : ""}
    ${point ? renderSolutionSection("point", "考点还原", point, textRenderer) : ""}
    ${explanation ? renderSolutionSection("explanation", "标准解析", explanation, textRenderer) : ""}
  </div>`;
}

function renderAnswerSolution(answer, userAnswer, isCorrect, textRenderer = escapeHtml) {
  const hasUserAnswer = userAnswer !== undefined && userAnswer !== null && String(userAnswer) !== "";
  const userClass = isCorrect === true ? "correct" : isCorrect === false ? "incorrect" : "neutral";
  return `<section class="solution-section solution-answer">
    <div class="solution-heading"><strong>答案</strong></div>
    <div class="solution-content answer-summary">标准答案：<strong class="standard-answer-value">${textRenderer(answer)}</strong>${hasUserAnswer ? `，你的答案：<strong class="user-answer-value ${userClass}">${textRenderer(userAnswer)}</strong>` : ""}</div>
  </section>`;
}

function renderSolutionSection(kind, label, content, textRenderer = escapeHtml) {
  return `<section class="solution-section solution-${kind}">
    <div class="solution-heading"><strong>${label}</strong></div>
    <div class="solution-content">${textRenderer(content)}</div>
  </section>`;
}

async function startSession({ collectionId, position = 1, kind = "library", mode = state.outlineMode, path = "", returnHash = "" }) {
  const returnPath = path
    || state.libraryPath[kind]
    || collectionParentPath(state.outline?.payload?.collection?.relative_path);
  const body = {
    collection_id: collectionId,
    start_position: Number(position) || 1,
    order_mode: "sequential",
    mode,
  };
  if (kind === "favorites") body.saved_kind = "favorite";
  if (kind === "wrong") body.saved_kind = "wrong";
  try {
    const session = await api("/practice/sessions", { method: "POST", body });
    const normalizedReturnHash = normalizeStudyReturnHash(returnHash);
    state.session = session;
    state.sessionIndex = 0;
    state.drafts = {};
    state.sessionReturnTarget = {
      kind: normalizedReturnHash ? "personal" : kind,
      collectionId,
      path: returnPath,
      hash: normalizedReturnHash || libraryRouteHash(kind, returnPath, collectionId),
    };
    const sessionParams = new URLSearchParams();
    if (normalizedReturnHash) sessionParams.set("return", normalizedReturnHash);
    const sessionQuery = sessionParams.toString();
    await navigateHash(`#session/${session.session_id}${sessionQuery ? `?${sessionQuery}` : ""}`);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function loadSession(sessionId, { returnHash = "" } = {}) {
  const container = $("#page-practice");
  container.innerHTML = loadingMarkup("正在恢复练习进度");
  try {
    state.session = await api(`/practice/sessions/${encodeURIComponent(sessionId)}`);
    state.sessionIndex = Math.max(
      0,
      state.session.items.findIndex((item) => !item.submission)
    );
    state.drafts = {};
    const restoredReturnHash = normalizeStudyReturnHash(returnHash);
    if (restoredReturnHash) {
      state.sessionReturnTarget = {
        kind: "personal",
        collectionId: state.session.collection_id,
        path: "",
        hash: restoredReturnHash,
      };
    } else if (!state.sessionReturnTarget) {
      const kind = state.session.saved_kind === "favorite"
        ? "favorites"
        : state.session.saved_kind === "wrong" ? "wrong" : "library";
      const source = state.session.items[0]?.question?.sources?.[0] || {};
      const path = collectionParentPath(source.relative_path);
      state.sessionReturnTarget = {
        kind,
        collectionId: state.session.collection_id,
        path,
        hash: libraryRouteHash(kind, path, state.session.collection_id),
      };
    }
    renderPractice();
    startTimer();
  } catch (error) {
    container.innerHTML = emptyMarkup("练习恢复失败", error.message, "!");
  }
}

function renderPractice() {
  const session = state.session;
  if (!session?.items?.length) return;
  document.body.classList.remove("outline-open");
  const index = Math.min(Math.max(0, state.sessionIndex), session.items.length - 1);
  state.sessionIndex = index;
  const item = session.items[index];
  const question = item.question;
  const submitted = item.submission;
  const isComplete = session.status === "complete";
  const mode = session.mode || "practice";
  const meta = modeMeta(mode);
  const reveal = session.solution_visible || (Boolean(submitted) && mode !== "test");
  const draft = state.drafts[item.position] || "";
  const source = (question.sources || [])[0] || {};
  const occurrenceId = Number(question.occurrence_id || item.occurrence_id || source.occurrence_id) || null;
  const title = fileTitle(source.relative_path || session.collection_id || "练习");
  const answerMarkup = renderAnswerArea(item, draft, reveal, isComplete);
  const summary = session.summary || {};
  const canPrev = index > 0;
  const isLast = index === session.items.length - 1;
  const showSubmit = mode === "practice" && !submitted && !isComplete;
  const finishButton = ["practice", "test"].includes(mode)
    ? `<button class="button button-danger button-small" type="button" data-action="finish-session" ${isComplete ? "disabled" : ""}>${isComplete ? "已交卷" : "交卷"}</button>`
    : "";
  const returnKind = state.sessionReturnTarget?.kind === "personal"
    ? studyReturnLabel(state.sessionReturnTarget.hash)
    : session.saved_kind === "favorite"
      ? "收藏"
      : session.saved_kind === "wrong" ? "错题" : "题库";
  const statusNote = mode === "speed" && submitted && submitted.is_correct === false
    ? `<p class="practice-feedback-note wrong">未选中正确答案，答案与解析已展开；确认后可继续下一题。</p>`
    : mode === "test" && !isComplete
      ? `<p class="practice-feedback-note">测试进行中，交卷前不会显示对错。</p>`
      : "";
  const resultPanel = isComplete && mode !== "memorize"
    ? `<section class="session-result" aria-label="本次成绩">
        <span><small>得分</small><strong>${summary.correct || 0}<em> / ${summary.total || session.items.length}</em></strong></span>
        <span><small>正确率</small><strong>${summary.total ? Math.round(((summary.correct || 0) / summary.total) * 100) : 0}<em>%</em></strong></span>
        <span><small>未作答</small><strong>${Math.max(0, (summary.total || 0) - (summary.answered || 0))}</strong></span>
      </section>`
    : "";
  const outlineStats = mode === "test" && !isComplete
    ? `${summary.answered || 0} 已答 · ${Math.max(0, (summary.total || 0) - (summary.answered || 0))} 未答`
    : `${summary.correct || 0} 对 · ${summary.wrong || 0} 错 · ${Math.max(0, (summary.total || 0) - (summary.answered || 0))} 未答`;
  $("#page-practice").innerHTML = `
    <div class="practice-top practice-top-immersive ${finishButton ? "has-finish" : ""}">
      <button class="button button-secondary" type="button" data-action="leave-session" aria-label="返回${returnKind}">‹ <span>返回${returnKind}</span></button>
      <div class="practice-top-main"><h1>${escapeHtml(title)}</h1><p><span class="mode-chip mode-${mode}">${meta.label}</span> · ${summary.answered || 0}/${summary.total || session.items.length} 已完成</p></div>
      <span class="timer-pill" id="practice-timer" aria-label="本次练习用时">◷ ${sessionDuration()}</span>
      ${finishButton}
    </div>
    <div class="practice-layout">
      <article class="card practice-question ${state.navigationDirection ? `slide-${state.navigationDirection}` : ""}" data-mode="${mode}">
        <div class="practice-progress"><strong>第 ${item.position} 题 / 共 ${session.items.length} 题</strong><span>${escapeHtml(question.type_label || question.raw_type || question.question_type)}${question.source_code ? ` · ${escapeHtml(question.source_code)}` : ""}</span></div>
        ${resultPanel}
        <div class="question-meta">
          <span class="badge">${escapeHtml(question.type_label || question.raw_type || question.question_type)}</span>
          ${question.source_code ? `<span class="badge badge-neutral">${escapeHtml(question.source_code)}</span>` : ""}
          ${!submitted && isComplete && session.mode !== "memorize" ? `<span class="badge badge-warn">未作答</span>` : ""}
        </div>
        <p class="question-stem">${escapeHtml(question.stem)}</p>
        ${answerMarkup}
        ${statusNote}
        ${reveal ? `<div class="practice-solution">${renderSolution(submitted ? {
          answer: submitted.correct_answer,
          user_answer: submitted.answer,
          is_correct: submitted.is_correct,
          point: submitted.point,
          explanation: submitted.explanation,
        } : question)}</div>` : ""}
        ${reveal ? `<div class="question-note-slot" data-note-occurrence="${occurrenceId || ""}">${renderQuestionNote(occurrenceId)}</div>` : ""}
        ${reveal ? `<div class="question-comments-slot" data-comments-occurrence="${occurrenceId || ""}">${renderQuestionComments(occurrenceId)}</div>` : ""}
        <div class="practice-footer" data-action-count="${4 + Number(showSubmit) + Number(session.saved_kind === "wrong")}">
          <button class="button button-secondary practice-footer-button practice-footer-prev" type="button" data-action="session-prev" aria-label="上一题" ${canPrev ? "" : "disabled"}><span class="practice-footer-icon" aria-hidden="true">←</span><span class="practice-footer-label">上一题</span></button>
          <button class="button button-secondary practice-footer-button mobile-outline-trigger" type="button" data-action="toggle-outline" aria-controls="practice-outline" aria-expanded="false"><span class="practice-footer-label">题号</span></button>
          ${showSubmit ? `<button class="button button-primary practice-footer-button practice-footer-main practice-footer-submit" type="button" data-action="submit-answer" ${draft ? "" : "disabled"}><span class="practice-footer-label">提交答案</span></button>` : ""}
          <button class="button button-secondary practice-footer-button practice-footer-favorite favorite-button" type="button" data-action="toggle-favorite" aria-label="收藏本题" data-occurrence="${occurrenceId || ""}" data-question="${escapeAttr(question.question_id)}" data-favorited="false" aria-pressed="false"><span class="practice-footer-icon favorite-icon" aria-hidden="true">☆</span><span class="practice-footer-label favorite-label">收藏</span></button>
          ${session.saved_kind === "wrong" ? `<button class="button button-danger practice-footer-button practice-remove-button" type="button" data-action="remove-current-wrong" data-key="occurrence:${occurrenceId || ""}"><span class="practice-footer-label">删除错题</span></button>` : ""}
          <button class="button ${showSubmit || isLast ? "button-secondary" : "button-primary"} practice-footer-button practice-footer-next" type="button" data-action="session-next" aria-label="下一题"><span class="practice-footer-label">下一题</span><span class="practice-footer-icon" aria-hidden="true">→</span></button>
        </div>
      </article>
      <button id="practice-outline-backdrop" class="practice-outline-backdrop" type="button" data-action="close-practice-outline" aria-label="关闭题号目录"></button>
      <aside id="practice-outline" class="card practice-outline">
        <div class="practice-outline-grabber" aria-hidden="true"></div>
        <div class="practice-outline-head"><strong>题号目录</strong><span>${outlineStats}</span><button class="practice-outline-close" type="button" data-action="close-practice-outline" aria-label="关闭题号目录">×</button></div>
        <div class="practice-outline-body">${renderSessionOutline(session, index)}</div>
      </aside>
    </div>`;
  updateTimerText();
  state.navigationDirection = "";
  refreshFavoriteButtons();
  if (reveal) {
    ensureQuestionNote(occurrenceId);
    ensureQuestionComments(occurrenceId);
  }
}

function renderAnswerArea(item, draft, reveal, isComplete) {
  const question = item.question;
  const submitted = item.submission;
  const mode = state.session.mode || "practice";
  if (mode === "memorize") {
    return renderReviewOptions(question.options || [], question.answer || "");
  }
  let options = question.options || [];
  if (!options.length && question.question_type === "判断题") {
    options = [
      { label: "A", text: "正确" },
      { label: "B", text: "错误" },
    ];
  }
  if (options.length) {
    const selected = new Set(String(submitted?.answer || draft).split(""));
    const correctAnswer = submitted?.correct_answer || (reveal ? question.answer || "" : "");
    const correct = new Set(String(correctAnswer).split(""));
    const canSpeedRetry = mode === "speed" && submitted && submitted.is_correct === false && !isComplete;
    const disabled = isComplete || (submitted && !canSpeedRetry);
    return `<div class="answer-options">${options.map((option) => {
      const classes = ["answer-option"];
      if (selected.has(option.label)) classes.push("selected");
      if (reveal && correct.has(option.label)) classes.push("correct");
      if (reveal && selected.has(option.label) && !correct.has(option.label)) classes.push("incorrect");
      const stateMark = classes.includes("correct") ? "正确答案" : classes.includes("incorrect") ? "回答错误" : "";
      return `<button class="${classes.join(" ")}" type="button" data-action="select-option" data-label="${escapeAttr(option.label)}" ${disabled ? "disabled" : ""}><span class="option-label">${escapeHtml(option.label)}</span><span class="option-text">${escapeHtml(option.text)}</span>${stateMark ? `<span class="option-state" aria-label="${stateMark}"></span>` : ""}</button>`;
    }).join("")}</div>`;
  }
  return `<div class="subjective-answer"><label class="field"><span>作答内容</span><textarea id="subjective-draft" data-action="subjective-draft" placeholder="输入你的答案" ${(submitted || isComplete) ? "disabled" : ""}>${escapeHtml(submitted?.answer || draft)}</textarea></label></div>`;
}

function renderReviewOptions(options, answer) {
  if (!options.length) return "";
  const correct = new Set(String(answer || "").split(""));
  return `<div class="answer-options answer-options-review" role="list">${options.map((option) => {
    const isCorrect = correct.has(option.label);
    return `<div class="answer-option ${isCorrect ? "correct" : ""}" role="listitem"><span class="option-label">${escapeHtml(option.label)}</span><span class="option-text">${escapeHtml(option.text)}</span>${isCorrect ? `<span class="option-state" aria-label="正确答案"></span>` : ""}</div>`;
  }).join("")}</div>`;
}

function renderSessionOutline(session, currentIndex) {
  const groups = new Map();
  session.items.forEach((item, index) => {
    const label = item.question.type_label || item.question.raw_type || item.question.question_type;
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push({ item, index });
  });
  return [...groups.entries()].map(([label, entries]) => `
    <section class="outline-group"><h3 class="outline-group-title"><span class="type-dot ${typeClass(label)}"></span><span>${escapeHtml(label)}</span></h3>
      <div class="number-grid">${entries.map(({ item, index }) => {
        const result = item.submission?.is_correct;
        const hiddenResult = session.mode === "test" && session.status !== "complete";
        const status = hiddenResult && item.submission ? "answered" : result === true ? "correct" : result === false ? "wrong" : item.submission ? "ungraded" : "";
        return `<button class="number-button ${status} ${index === currentIndex ? "current" : ""}" type="button" data-action="session-jump" data-index="${index}">${item.position}</button>`;
      }).join("")}</div>
     </section>`).join("");
}

function renderQuestionNote(occurrenceId) {
  if (!occurrenceId) return "";
  const record = state.notes[String(occurrenceId)];
  if (!record || record.loading) {
    return `<section class="question-note"><div class="study-section-heading"><div><h2>笔记</h2><span>仅自己可见</span></div></div><div class="comments-loading"><span class="loading-spinner" aria-hidden="true"></span><span>正在加载笔记</span></div></section>`;
  }
  if (record.error) {
    return `<section class="question-note"><div class="study-section-heading"><div><h2>笔记</h2><span>仅自己可见</span></div><button class="text-button" type="button" data-action="reload-note" data-occurrence="${occurrenceId}">重新加载</button></div><p class="comments-error">${escapeHtml(record.error)}</p></section>`;
  }
  const item = record.item || { body: "", exists: false };
  return `<section class="question-note" aria-label="我的本题笔记">
    <div class="study-section-heading"><div><h2>笔记</h2><span class="private-label">仅自己可见</span></div>${item.updated_at ? `<time datetime="${escapeAttr(item.updated_at)}">更新于 ${escapeHtml(formatDate(item.updated_at))}</time>` : ""}</div>
    <form class="note-form" data-form="question-note" data-occurrence="${occurrenceId}">
      <textarea name="body" maxlength="10000" rows="5" required placeholder="记录易错点、鉴别思路或复习提示">${escapeHtml(item.body || "")}</textarea>
      <div class="note-form-actions"><small>笔记不会进入搜题索引 · 最多 10000 字</small><div>${item.exists ? `<button class="button button-quiet button-small" type="button" data-action="delete-note" data-occurrence="${occurrenceId}">删除</button>` : ""}<button class="button button-primary button-small" type="submit">保存笔记</button></div></div>
    </form>
  </section>`;
}

async function ensureQuestionNote(occurrenceId, { force = false } = {}) {
  if (!occurrenceId) return;
  const key = String(occurrenceId);
  if (state.notes[key] && !force) return;
  state.notes[key] = { loading: true, item: state.notes[key]?.item || null, error: "" };
  updateQuestionNote(occurrenceId);
  try {
    const item = await api(`/study/notes/${encodeURIComponent(occurrenceId)}`);
    state.notes[key] = { loading: false, item, error: "" };
  } catch (error) {
    state.notes[key] = { loading: false, item: null, error: error.message || "笔记加载失败" };
  }
  updateQuestionNote(occurrenceId);
}

function updateQuestionNote(occurrenceId) {
  const slot = $(`[data-note-occurrence="${Number(occurrenceId)}"]`);
  if (slot) slot.innerHTML = renderQuestionNote(Number(occurrenceId));
}

async function submitQuestionNote(form) {
  const occurrenceId = Number(form.dataset.occurrence);
  const body = String(new FormData(form).get("body") || "").trim();
  if (!occurrenceId || !body) return;
  const button = $('button[type="submit"]', form);
  setButtonBusy(button, true, "保存中");
  try {
    const item = await api(`/study/notes/${encodeURIComponent(occurrenceId)}`, {
      method: "PUT",
      body: { body },
    });
    state.notes[String(occurrenceId)] = { loading: false, item, error: "" };
    state.myNotes = null;
    invalidateStudySummary();
    updateQuestionNote(occurrenceId);
    showToast("笔记已保存");
  } catch (error) {
    showToast(error.message, "error");
    setButtonBusy(button, false);
  }
}

async function deleteQuestionNote(occurrenceId, { fromList = false } = {}) {
  const confirmed = await confirmModal("删除笔记", "确认删除这道题的个人笔记？删除后无法恢复。", "确认删除");
  if (!confirmed) return;
  try {
    await api(`/study/notes/${encodeURIComponent(occurrenceId)}`, { method: "DELETE", body: {} });
    state.notes[String(occurrenceId)] = { loading: false, item: { occurrence_id: occurrenceId, body: "", exists: false }, error: "" };
    state.myNotes = null;
    invalidateStudySummary();
    if (fromList) await renderMyNotesPage({ force: true });
    else updateQuestionNote(occurrenceId);
    showToast("笔记已删除");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function editPersonalNote(occurrenceId) {
  const item = state.myNotes?.items?.find((entry) => Number(entry.occurrence_id) === Number(occurrenceId));
  if (!item) return;
  const body = await textEditorModal("编辑笔记", item.body, 10000, "保存笔记");
  if (!body || body === item.body) return;
  try {
    await api(`/study/notes/${encodeURIComponent(occurrenceId)}`, { method: "PUT", body: { body } });
    state.notes[String(occurrenceId)] = null;
    state.myNotes = null;
    invalidateStudySummary();
    await renderMyNotesPage({ force: true });
    showToast("笔记已更新");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderQuestionComments(occurrenceId) {
  if (!occurrenceId) {
    return `<section class="question-comments"><div class="comments-heading"><h2>评论</h2></div><p class="comments-empty">当前题目没有可用的原题来源。</p></section>`;
  }
  const record = state.comments[String(occurrenceId)];
  const items = record?.items || [];
  const list = !record || record.loading
    ? `<div class="comments-loading"><span class="loading-spinner" aria-hidden="true"></span><span>正在加载评论</span></div>`
    : record.error
      ? `<div class="comments-error"><span>${escapeHtml(record.error)}</span><button class="text-button" type="button" data-action="reload-comments" data-occurrence="${occurrenceId}">重新加载</button></div>`
      : items.length
        ? `<div class="comment-list">${items.map((comment) => `
            <article class="comment-item">
              <button class="comment-avatar comment-avatar-button" type="button" data-action="open-profile" data-user-id="${escapeAttr(comment.author_id || "")}" aria-label="查看${escapeAttr(comment.author_name || "用户")}的个人资料">${avatarVisual(comment, comment.author_name)}</button>
              <div class="comment-main ${comment.parent_comment_id ? "comment-reply" : ""}">
                <div class="comment-meta"><button class="comment-author" type="button" data-action="open-profile" data-user-id="${escapeAttr(comment.author_id || "")}">${escapeHtml(comment.author_name || "用户")}</button><time datetime="${escapeAttr(comment.created_at || "")}">${escapeHtml(formatDate(comment.created_at))}${comment.edited ? " · 已编辑" : ""}</time><span class="comment-actions">${comment.can_edit ? `<button class="text-button" type="button" data-action="edit-comment" data-comment-id="${escapeAttr(comment.comment_id)}" data-occurrence="${occurrenceId}">编辑</button>` : ""}${comment.can_delete ? `<button class="text-button comment-delete" type="button" data-action="delete-comment" data-comment-id="${escapeAttr(comment.comment_id)}" data-occurrence="${occurrenceId}">删除</button>` : ""}</span></div>
                <p>${escapeHtml(comment.body)}</p>
                <div class="comment-toolbar"><button class="comment-reaction ${comment.my_reaction === "agree" ? "active" : ""}" type="button" data-action="react-comment" data-comment-id="${escapeAttr(comment.comment_id)}" data-reaction="agree" data-occurrence="${occurrenceId}">赞同 <span>${comment.agree_count || 0}</span></button><button class="comment-reaction ${comment.my_reaction === "disagree" ? "active" : ""}" type="button" data-action="react-comment" data-comment-id="${escapeAttr(comment.comment_id)}" data-reaction="disagree" data-occurrence="${occurrenceId}">反对 <span>${comment.disagree_count || 0}</span></button><button class="comment-reaction" type="button" data-action="reply-comment" data-comment-id="${escapeAttr(comment.comment_id)}" data-occurrence="${occurrenceId}">回复 <span>${comment.reply_count || 0}</span></button></div>
              </div>
            </article>`).join("")}</div>`
        : `<p class="comments-empty">暂无评论</p>`;
  return `<section class="question-comments" aria-label="本题评论">
    <div class="comments-heading"><h2>评论</h2><span>${items.length} 条</span></div>
    ${list}
    <form class="comment-form" data-form="question-comment" data-occurrence="${occurrenceId}">
      <textarea name="body" maxlength="2000" rows="3" required placeholder="分享解题思路，或提出与本题相关的疑问"></textarea>
      <div class="comment-form-actions"><small>最多 2000 字</small><button class="button button-primary button-small" type="submit">发表评论</button></div>
    </form>
  </section>`;
}

async function ensureQuestionComments(occurrenceId, { force = false } = {}) {
  if (!occurrenceId) return;
  const key = String(occurrenceId);
  if (state.comments[key] && !force) return;
  const existing = state.comments[key];
  state.comments[key] = { loading: true, items: existing?.items || [], error: "" };
  updateQuestionComments(occurrenceId);
  try {
    const payload = await api(`/study/comments?occurrence_id=${encodeURIComponent(occurrenceId)}`);
    state.comments[key] = { loading: false, items: payload.items || [], error: "" };
  } catch (error) {
    state.comments[key] = { loading: false, items: existing?.items || [], error: error.message || "评论加载失败" };
  }
  updateQuestionComments(occurrenceId);
}

function updateQuestionComments(occurrenceId) {
  const slot = $(`[data-comments-occurrence="${Number(occurrenceId)}"]`);
  if (slot) slot.innerHTML = renderQuestionComments(Number(occurrenceId));
}

async function submitQuestionComment(form) {
  const occurrenceId = Number(form.dataset.occurrence);
  const body = String(new FormData(form).get("body") || "").trim();
  if (!occurrenceId || !body) return;
  const button = $('button[type="submit"]', form);
  setButtonBusy(button, true, "发表中");
  try {
    const item = await api("/study/comments", {
      method: "POST",
      body: { occurrence_id: occurrenceId, body },
    });
    const key = String(occurrenceId);
    const items = state.comments[key]?.items || [];
    state.comments[key] = { loading: false, items: [...items, item], error: "" };
    state.myComments = null;
    invalidateStudySummary();
    updateQuestionComments(occurrenceId);
    showToast("评论已发表");
  } catch (error) {
    showToast(error.message, "error");
    setButtonBusy(button, false);
  }
}

async function deleteQuestionComment(commentId, occurrenceId) {
  const confirmed = await confirmModal("删除评论", "确认删除这条评论？", "确认删除");
  if (!confirmed) return;
  try {
    await api(`/study/comments/${encodeURIComponent(commentId)}`, { method: "DELETE", body: {} });
    const key = String(occurrenceId);
    const record = state.comments[key] || { loading: false, items: [], error: "" };
    state.comments[key] = {
      ...record,
      items: (record.items || []).filter((item) => item.comment_id !== commentId),
    };
    if (state.myComments) {
      state.myComments.items = (state.myComments.items || []).filter((item) => item.comment_id !== commentId);
      state.myComments.total = Math.max(0, Number(state.myComments.total || 0) - 1);
    }
    state.myLikes = null;
    invalidateStudySummary();
    updateQuestionComments(occurrenceId);
    if (location.hash.startsWith("#my-comments")) await renderMyCommentsPage({ force: true });
    showToast("评论已删除");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function editQuestionComment(commentId, occurrenceId) {
  const cached = state.comments[String(occurrenceId)]?.items?.find((item) => item.comment_id === commentId)
    || state.myComments?.items?.find((item) => item.comment_id === commentId);
  if (!cached) return;
  const body = await textEditorModal("编辑评论", cached.body, 2000, "保存修改");
  if (!body || body === cached.body) return;
  try {
    const updated = await api(`/study/comments/${encodeURIComponent(commentId)}`, {
      method: "PATCH",
      body: { body },
    });
    const record = state.comments[String(occurrenceId)];
    if (record) record.items = record.items.map((item) => item.comment_id === commentId ? updated : item);
    if (state.myComments) state.myComments.items = state.myComments.items.map((item) => item.comment_id === commentId ? { ...item, ...updated } : item);
    state.myLikes = null;
    updateQuestionComments(occurrenceId);
    if (location.hash.startsWith("#my-comments")) await renderMyCommentsPage({ force: true });
    showToast("评论已更新");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function reactComment(commentId, reaction, occurrenceId) {
  try {
    const result = await api(`/study/comments/${encodeURIComponent(commentId)}/reaction`, { method: "POST", body: { reaction } });
    const record = state.comments[String(occurrenceId)];
    if (record) record.items = record.items.map((item) => item.comment_id === commentId ? { ...item, ...result } : item);
    state.myLikes = null;
    invalidateStudySummary();
    updateQuestionComments(occurrenceId);
    if (location.hash.startsWith("#my-likes")) {
      await renderMyLikesPage({ force: true });
      showToast(result.my_reaction === "agree" ? "已赞同" : "已取消赞同");
    }
  } catch (error) { showToast(error.message, "error"); }
}

async function replyToComment(commentId, occurrenceId) {
  const body = await textEditorModal("回复评论", "", 2000, "发表回复");
  if (!body) return;
  try {
    await api("/study/comments", { method: "POST", body: { occurrence_id: occurrenceId, parent_comment_id: commentId, body } });
    state.myComments = null;
    invalidateStudySummary();
    await ensureQuestionComments(occurrenceId, { force: true });
    showToast("回复已发表");
  } catch (error) { showToast(error.message, "error"); }
}

async function submitCurrentAnswer({ autoAdvance = false } = {}) {
  const session = state.session;
  const index = state.sessionIndex;
  const item = session.items[index];
  const answer = state.drafts[item.position] || "";
  if (!answer) return;
  try {
    const result = await api(`/practice/sessions/${encodeURIComponent(session.session_id)}/answers`, {
      method: "POST",
      body: { position: item.position, answer, elapsed_ms: sessionElapsedMs() },
    });
    invalidateStudySummary();
    state.session = await api(`/practice/sessions/${encodeURIComponent(session.session_id)}`);
    state.sessionIndex = Math.min(index, state.session.items.length - 1);
    const canAdvance = autoAdvance && index < state.session.items.length - 1 && state.session.status !== "complete";
    if (canAdvance && session.mode === "test") {
      state.sessionIndex = index + 1;
      renderPractice();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else if (canAdvance && session.mode === "speed" && result.is_correct === true) {
      renderPractice();
      state.selectionTimer = window.setTimeout(() => {
        state.selectionTimer = null;
        const currentItem = state.session?.items?.[index];
        if (
          state.session?.session_id === session.session_id &&
          state.sessionIndex === index &&
          currentItem?.submission?.is_correct === true
        ) {
          state.navigationDirection = "forward";
          state.sessionIndex = index + 1;
          renderPractice();
          window.scrollTo({ top: 0, behavior: "smooth" });
        }
      }, 360);
    } else {
      renderPractice();
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function finishCurrentSession() {
  const session = state.session;
  const unanswered = Math.max(0, session.summary.total - session.summary.answered);
  const confirmed = await confirmModal(
    "确认交卷",
    unanswered ? `还有 ${unanswered} 道题未作答。交卷后仍会保留这些题为未答，并进入整卷复盘。` : "所有题目均已作答，确认提交本次练习？",
    "确认交卷"
  );
  if (!confirmed) return;
  try {
    state.session = await api(`/practice/sessions/${encodeURIComponent(session.session_id)}/finish`, { method: "POST", body: {} });
    renderPractice();
    showToast("交卷完成，可以逐题复盘答案与解析");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function startTimer() {
  stopTimer();
  state.timerHandle = window.setInterval(updateTimerText, 1000);
  updateTimerText();
}

function stopTimer() {
  if (state.timerHandle) window.clearInterval(state.timerHandle);
  state.timerHandle = null;
}

function sessionElapsedMs() {
  if (!state.session) return 0;
  if (state.session.duration_ms != null) return Number(state.session.duration_ms);
  const start = Date.parse(state.session.created_at);
  const end = state.session.completed_at ? Date.parse(state.session.completed_at) : Date.now();
  return Number.isFinite(start) ? Math.max(0, end - start) : 0;
}

function sessionDuration() {
  const total = Math.floor(sessionElapsedMs() / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function updateTimerText() {
  const timer = $("#practice-timer");
  if (timer) timer.textContent = `◷ ${sessionDuration()}`;
}

const PROFILE_AVATARS = [
  { id: "preset-01", label: "珊瑚白袍" },
  { id: "preset-02", label: "靛蓝听诊" },
  { id: "preset-03", label: "青绿研究" },
  { id: "preset-04", label: "暖金书卷" },
  { id: "preset-05", label: "雾紫脑科学" },
  { id: "preset-06", label: "天蓝临床" },
];

async function renderMyHomePage() {
  const user = state.user || {};
  const container = $("#page-profile");
  container.innerHTML = loadingMarkup("正在读取个人空间");
  try {
    const profile = await api(`/profiles/${encodeURIComponent(user.user_id)}`);
    state.publicProfile = profile;
    const stats = profile.stats || {};
    const commonActions = `${user.role === "admin" ? homeUtility("系统管理", "admin", "admin", "open-admin") : ""}${homeUtility("下载", "download", "download")}`;
    container.innerHTML = `<div class="my-home">
      <section class="my-home-hero" data-action="open-own-profile" role="button" tabindex="0"><span class="profile-avatar profile-avatar-xl">${avatarVisual(profile, profile.username)}</span><div><h1>${escapeHtml(profile.username || "我的")}</h1><p>IP属地 ${escapeHtml(profile.ip_location || "本地")} · ${escapeHtml(profile.school || "学校未设置")}</p></div><span class="my-home-chevron">›</span></section>
      <nav class="my-home-stats card" aria-label="个人数据">${profileStatLink("dynamics", "动态", stats.dynamics || 0, "profile-detail")}${profileStatLink("following", "关注", stats.following || 0, "profile-detail")}${profileStatLink("followers", "粉丝", stats.followers || 0, "profile-detail")}</nav>
      <section class="my-home-section"><h2>常用功能</h2><div class="my-home-actions">${commonActions}</div></section>
      <section class="my-home-section"><h2>其他功能</h2><div class="my-home-actions">${homeUtility("帮助中心", "help", "help")}${homeUtility("联系客服", "contact", "contact")}${homeUtility("意见反馈", "feedback", "feedback")}${homeUtility("设置", "settings", "settings")}</div></section>
      <button class="button button-danger logout-wide" type="button" data-action="logout">退出登录</button>
    </div>`;
  } catch (error) {
    container.innerHTML = emptyMarkup("个人空间读取失败", error.message, "!");
  }
}

function profileStatLink(key, label, value, page, userId = "") {
  const targetId = userId || state.publicProfile?.user_id || state.user.user_id;
  return `<a class="my-home-stat" href="#${page}?user=${encodeURIComponent(targetId)}&tab=${key}"><strong>${formatNumber(value)}</strong><small>${label}</small></a>`;
}

function utilityIcon(name) {
  const paths = {
    admin: '<rect x="4" y="3" width="16" height="18" rx="3"></rect><path d="M8 8h8M8 12h8M8 16h5"></path>',
    download: '<path d="M12 3v12M7.5 10.5 12 15l4.5-4.5"></path><path d="M5 20h14"></path>',
    help: '<circle cx="12" cy="12" r="9"></circle><path d="M9.8 9a2.4 2.4 0 1 1 3.1 2.3c-.9.4-.9 1.2-.9 1.7M12 17h.01"></path>',
    contact: '<path d="M5 15v-3a7 7 0 0 1 14 0v3"></path><path d="M5 14H4a2 2 0 0 0-2 2v1a2 2 0 0 0 2 2h2v-5H5ZM19 14h1a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-2v-5h1ZM18 19c-1 1.3-2.7 2-5 2"></path>',
    feedback: '<path d="M4 5h16v11H9l-5 4V5Z"></path><path d="M8 9h8M8 12h5"></path>',
    settings: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"></path>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.help}</svg>`;
}

function homeUtility(label, icon, utility, action = "home-utility") {
  return `<button class="my-home-action" type="button" data-action="${action}" data-utility="${utility}"><span class="my-home-action-icon utility-icon">${utilityIcon(icon)}</span><strong>${label}</strong></button>`;
}

async function renderPublicProfilePage(userId) {
  const container = $("#page-profile-detail");
  container.innerHTML = loadingMarkup("正在读取个人资料");
  try {
    const profile = await api(`/profiles/${encodeURIComponent(userId)}`);
    state.publicProfile = profile;
    if (state.profileEditing && profile.is_self) return renderProfileEditorPage(profile);
    const stats = profile.stats || {};
    container.innerHTML = `<div class="public-profile"><button class="profile-back-button" type="button" data-action="return-from-profile">‹ 返回${escapeHtml(internalReturnLabel(state.profileReturnHash))}</button><section class="public-profile-card card"><div class="public-profile-head"><span class="profile-avatar profile-avatar-xl">${avatarVisual(profile, profile.username)}</span><div class="public-profile-identity"><h1>${escapeHtml(profile.username || "用户")}</h1><p>IP属地 ${escapeHtml(profile.ip_location || "本地")} · ${escapeHtml(profile.school || "学校未设置")}</p></div>${profile.is_self ? `<button class="button button-secondary button-small" type="button" data-action="edit-profile">编辑</button>` : ""}</div><nav class="public-profile-stats">${profileStatLink("followers", "粉丝", stats.followers || 0, "profile-detail")}${profileStatLink("following", "关注", stats.following || 0, "profile-detail")}${profileStatLink("likes", "获赞", stats.likes || 0, "profile-detail")}</nav><div class="public-profile-actions">${profile.is_self ? "" : `<button class="button ${stats.is_following ? "button-secondary" : "button-primary"} button-small" type="button" data-action="toggle-follow" data-user-id="${escapeAttr(profile.user_id)}" data-following="${stats.is_following ? "true" : "false"}">${stats.is_following ? "已关注" : "关注"}</button><button class="button button-secondary button-small" type="button" data-action="message-profile" data-user-id="${escapeAttr(profile.user_id)}" data-name="${escapeAttr(profile.username)}">私信</button>`}</div></section><section class="public-profile-dynamics"><div class="section-heading"><div><h2>动态</h2><p>评论与学习讨论</p></div></div>${(profile.dynamics || []).length ? profile.dynamics.map(profileDynamicCard).join("") : emptyMarkup("暂无动态", "发表评论后会显示在这里。", "▤")}</section></div>`;
  } catch (error) {
    container.innerHTML = `<button class="profile-back-button" type="button" data-action="return-from-profile">‹ 返回${escapeHtml(internalReturnLabel(state.profileReturnHash))}</button>${emptyMarkup("个人资料读取失败", error.message, "!")}`;
  }
}

function profileDynamicCard(item) {
  const question = item.question || {};
  return `<article class="card profile-dynamic-card"><div class="profile-dynamic-meta"><span>评论</span><time>${escapeHtml(formatDate(item.created_at))}</time></div><p>${escapeHtml(item.body || "")}</p>${question.stem ? `<small>${escapeHtml(question.stem)}</small>` : ""}<div class="profile-dynamic-actions"><span>赞同 ${item.agree_count || 0}</span><span>回复 ${item.reply_count || 0}</span></div></article>`;
}

function renderProfileEditorPage(user) {
  const selectedAvatar = (state.profileAvatarDraft ?? user.avatar) || "preset-01";
  const preview = selectedAvatar === user.avatar ? user : { avatar: selectedAvatar, avatar_url: selectedAvatar.startsWith("preset-") ? `/avatars/${selectedAvatar}.svg` : "" };
  $("#page-profile-detail").innerHTML = `<div class="profile-editor-page"><button class="profile-back-button" type="button" data-action="cancel-profile-edit">‹ 个人资料</button><section class="card profile-card profile-editor-card"><div class="profile-card-head"><div><h1>编辑个人资料</h1><p>头像、昵称和公开资料。</p></div></div><form id="profile-form" class="profile-form"><div class="profile-avatar-editor"><span class="profile-avatar profile-avatar-large">${avatarVisual(preview, user.username)}</span><div class="avatar-editor-main"><div class="avatar-editor-copy"><strong>个人头像</strong><small>选择统一风格头像，或上传并裁剪自己的图片</small></div><div class="avatar-picker" role="radiogroup" aria-label="预设头像">${PROFILE_AVATARS.map((avatar) => `<button class="avatar-choice ${avatar.id === selectedAvatar ? "active" : ""}" type="button" role="radio" aria-label="${escapeAttr(avatar.label)}" aria-checked="${avatar.id === selectedAvatar}" data-action="select-avatar" data-avatar="${avatar.id}"><img src="/avatars/${avatar.id}.svg" alt=""></button>`).join("")}${user.avatar === "custom" ? `<button class="avatar-choice ${selectedAvatar === "custom" ? "active" : ""}" type="button" aria-label="当前自定义头像" data-action="select-avatar" data-avatar="custom">${avatarVisual(user, user.username)}</button>` : ""}<button class="avatar-upload-button" type="button" data-action="choose-avatar-file"><span aria-hidden="true">＋</span>上传图片</button><input id="avatar-file-input" type="file" accept="image/jpeg,image/png,image/webp" hidden></div></div></div><label class="field"><span>昵称</span><input name="username" minlength="3" maxlength="32" value="${escapeAttr(user.username || "")}" required></label><div class="profile-fields-grid"><label class="field"><span>生日</span><input name="birthday" type="date" value="${escapeAttr(user.birthday || "")}"></label><label class="field"><span>性别</span><select name="gender"><option value="">未设置</option><option value="男" ${user.gender === "男" ? "selected" : ""}>男</option><option value="女" ${user.gender === "女" ? "selected" : ""}>女</option><option value="保密" ${user.gender === "保密" ? "selected" : ""}>保密</option></select></label><label class="field"><span>学校</span><input name="school" maxlength="120" value="${escapeAttr(user.school || "")}"></label><label class="field"><span>专业</span><input name="major" maxlength="120" value="${escapeAttr(user.major || "")}"></label></div><fieldset class="privacy-settings"><legend>资料可见性</legend>${[["birthday","生日"],["gender","性别"],["school","学校"],["major","专业"]].map(([key,label]) => `<label><input name="privacy_${key}" type="checkbox" ${user.privacy?.[key] ? "checked" : ""}><span>${label}对其他人可见</span></label>`).join("")}</fieldset><div class="question-actions"><button class="button button-primary" type="submit">保存资料</button></div></form></section></div>`;
}

function renderSettingsPage() {
  $("#page-settings").innerHTML = `<div class="settings-page"><button class="profile-back-button" type="button" data-action="back-to-my-home">‹ 我的</button><section class="card settings-card"><div class="section-heading"><div><h1>设置</h1><p>账号与安全、绑定和应用偏好。</p></div></div><div class="settings-list"><button type="button" class="settings-list-item" data-action="show-placeholder" data-title="第三方账号绑定"><span><strong>第三方账号绑定</strong><small>暂未开放第三方登录</small></span><b>›</b></button><button type="button" class="settings-list-item" data-action="show-placeholder" data-title="通知设置"><span><strong>通知设置</strong><small>管理消息提醒</small></span><b>›</b></button></div><form id="settings-password-form" class="stack-form settings-password-form"><h2>修改密码</h2><label class="field"><span>当前密码</span><input name="current_password" type="password" autocomplete="current-password" required></label><label class="field"><span>新密码</span><input name="new_password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required></label><label class="field"><span>确认新密码</span><input name="confirm_password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required></label><p class="form-error" data-profile-password-error></p><button class="button button-primary" type="submit">保存新密码</button></form></section></div>`;
}

async function toggleFollowProfile(button) {
  const userId = button.dataset.userId;
  const following = button.dataset.following === "true";
  if (!userId) return;
  try {
    await api(`/profiles/${encodeURIComponent(userId)}/follow`, { method: following ? "DELETE" : "POST", body: {} });
    await renderPublicProfilePage(userId);
    showToast(following ? "已取消关注" : "已关注");
  } catch (error) { showToast(error.message, "error"); }
}

async function messageProfile(userId, name) {
  const body = await textEditorModal(`给 ${name || "用户"} 发私信`, "", 2000, "发送");
  if (!body) return;
  try {
    await api("/messages", { method: "POST", body: { receiver_id: userId, body } });
    state.messageData = null;
    showToast("私信已发送");
  } catch (error) { showToast(error.message, "error"); }
}

function handleHomeUtility(utility) {
  if (utility === "settings") return navigateHash("#settings");
  const labels = { download: "下载", help: "帮助中心", contact: "联系客服", feedback: "意见反馈" };
  return showToast(`${labels[utility] || "该功能"}即将开放`);
}

async function renderMessagesPage() {
  const container = $("#page-messages");
  if (state.messageData) {
    renderMessageCenter(state.messageData);
    return;
  }
  container.innerHTML = `${messagePageHeader()}${loadingMarkup("正在读取消息")}`;
  try {
    state.messageData = await api("/messages");
    renderMessageCenter(state.messageData);
  } catch (error) {
    container.innerHTML = `${messagePageHeader()}${emptyMarkup("消息读取失败", error.message, "!")}`;
  }
}

function messagePageHeader() {
  return `<div class="page-header account-page-header"><div class="page-title"><p class="eyebrow">我的消息</p><h1>消息中心</h1></div><div class="header-actions"><button class="button button-secondary button-small" type="button" data-action="return-from-account">‹ 返回</button></div></div>`;
}

function messageGroups(payload) {
  const notifications = payload.notifications || [];
  return [
    { key: "system", label: "系统通知", items: notifications.filter((item) => item.kind === "system") },
    { key: "direct", label: "私信对话", items: payload.messages || [] },
    { key: "replies", label: "回复与@", items: notifications.filter((item) => item.kind === "reply" || item.kind === "mention") },
    { key: "agreements", label: "收到赞同", items: notifications.filter((item) => item.kind === "agree") },
  ];
}

function renderMessageCenter(payload) {
  const container = $("#page-messages");
  if (!container) return;
  const groups = messageGroups(payload);
  const active = groups.find((group) => group.key === state.messageTab) || groups[0];
  state.messageTab = active.key;
  container.innerHTML = `${messagePageHeader()}
    <nav class="message-tabs card" aria-label="消息分类">${groups.map((group) => `<button class="message-tab ${group.key === active.key ? "active" : ""}" type="button" data-action="message-tab" data-tab="${group.key}" aria-pressed="${group.key === active.key}"><span>${group.label}</span><small>${formatNumber(group.items.length)}</small></button>`).join("")}</nav>
    <section class="card message-panel" aria-live="polite"><div class="message-panel-head"><h2>${active.label}</h2><span>${formatNumber(active.items.length)} 条</span></div>${active.items.length ? `<div class="message-list">${active.items.slice(0, 50).map((item) => `<article><strong>${escapeHtml(item.sender_name || active.label)}</strong><p>${escapeHtml(item.body || (item.kind === "agree" ? "赞同了你的评论" : "你收到一条新消息"))}</p><time>${escapeHtml(formatDate(item.created_at))}</time></article>`).join("")}</div>` : `<p class="message-empty">暂无${active.label}</p>`}</section>`;
}

function renderPlaceholderPage(page) {
  const meta = page === "ai" ? ["AI", "AI 助手", "对话与智能学习功能将在后续接入。", "✦"] : ["论坛", "学习论坛", "主题讨论功能将在后续开放。", "◌"];
  $(`#page-${page}`).innerHTML = emptyMarkup(meta[1], meta[2], meta[3]);
}

async function renderMyCommentsPage({ force = false } = {}) {
  const container = $("#page-my-comments");
  if (!state.myComments || force) container.innerHTML = `${personalPageHeader("评论", "正在读取评论")}${loadingMarkup("正在读取评论")}`;
  try {
    if (!state.myComments || force) state.myComments = await api("/study/comments/mine?limit=50&offset=0");
    const items = state.myComments.items || [];
    container.innerHTML = `${personalPageHeader("评论", `共 ${formatNumber(state.myComments.total || 0)} 条`)}${items.length ? personalFeedMarkup(items, "comment", state.myComments.total) : emptyMarkup("暂无评论", "完成题目后，可在标准解析下方参与讨论。", "◌")}`;
  } catch (error) {
    container.innerHTML = `${personalPageHeader("评论", "管理自己的讨论内容")}${emptyMarkup("评论读取失败", error.message, "!")}`;
  }
}

async function renderMyNotesPage({ force = false } = {}) {
  const container = $("#page-notes");
  if (!state.myNotes || force) container.innerHTML = `${personalPageHeader("笔记", "仅自己可见")}${loadingMarkup("正在读取笔记")}`;
  try {
    if (!state.myNotes || force) state.myNotes = await api("/study/notes?limit=50&offset=0");
    const items = state.myNotes.items || [];
    container.innerHTML = `${personalPageHeader("笔记", `共 ${formatNumber(state.myNotes.total || 0)} 条 · 仅自己可见`)}${items.length ? personalFeedMarkup(items, "note", state.myNotes.total) : emptyMarkup("暂无笔记", "答案解析解锁后，可在每道题下方保存个人笔记。", "□")}`;
  } catch (error) {
    container.innerHTML = `${personalPageHeader("笔记", "仅自己可见")}${emptyMarkup("笔记读取失败", error.message, "!")}`;
  }
}

async function renderMyLikesPage({ force = false } = {}) {
  const container = $("#page-my-likes");
  if (!state.myLikes || force) container.innerHTML = `${personalPageHeader("点赞", "赞同过的评论")}${loadingMarkup("正在读取点赞")}`;
  try {
    if (!state.myLikes || force) state.myLikes = await api("/study/comments/likes/mine?limit=50&offset=0");
    const items = state.myLikes.items || [];
    container.innerHTML = `${personalPageHeader("点赞", `共 ${formatNumber(state.myLikes.total || 0)} 条`)}${items.length ? personalFeedMarkup(items, "like", state.myLikes.total) : emptyMarkup("暂无点赞", "在评论区点击赞同后，内容会集中显示在这里。", "♡")}`;
  } catch (error) {
    container.innerHTML = `${personalPageHeader("点赞", "赞同过的评论")}${emptyMarkup("点赞读取失败", error.message, "!")}`;
  }
}

function personalPageHeader(label, copy) {
  return secondaryPageHeader(label, copy);
}

function personalFeedMarkup(items, kind, total) {
  const shown = items.length;
  const totalCount = Number(total || shown);
  return `<div class="personal-feed">${items.map((item) => personalContentCard(item, kind)).join("")}</div>${shown < totalCount ? `<div class="personal-feed-footer"><span>已显示 ${formatNumber(shown)} / ${formatNumber(totalCount)}</span><button class="button button-secondary button-small" type="button" data-action="load-more-personal" data-kind="${kind}">加载更多</button></div>` : ""}`;
}

async function loadMorePersonalFeed(kind, button) {
  const config = {
    comment: { key: "myComments", endpoint: "/study/comments/mine", render: renderMyCommentsPage, identity: "comment_id" },
    note: { key: "myNotes", endpoint: "/study/notes", render: renderMyNotesPage, identity: "occurrence_id" },
    like: { key: "myLikes", endpoint: "/study/comments/likes/mine", render: renderMyLikesPage, identity: "comment_id" },
  }[kind];
  if (!config) return;
  const current = state[config.key];
  if (!current || (current.items || []).length >= Number(current.total || 0)) return;
  setButtonBusy(button, true, "加载中");
  try {
    const next = await api(`${config.endpoint}?limit=50&offset=${(current.items || []).length}`);
    const seen = new Set((current.items || []).map((item) => String(item[config.identity])));
    const appended = (next.items || []).filter((item) => !seen.has(String(item[config.identity])));
    state[config.key] = {
      ...current,
      ...next,
      items: [...(current.items || []), ...appended],
      total: Number(next.total ?? current.total ?? 0),
      offset: 0,
    };
    await config.render();
  } catch (error) {
    showToast(error.message, "error");
    setButtonBusy(button, false);
  }
}

function personalContentCard(item, kind) {
  const question = item.question || {};
  const occurrenceId = Number(item.occurrence_id || question.occurrence_id) || 0;
  const canOpen = Boolean(question.collection_id && question.collection_position);
  const timestamp = kind === "like" ? item.liked_at : item.updated_at;
  const sourcePath = displaySourcePath(question.relative_path || "");
  const sourceMeta = [sourcePath, question.source_code || (question.collection_position ? `第 ${question.collection_position} 题` : "")].filter(Boolean).join(" · ");
  return `<article class="card personal-content-card">
    <div class="personal-content-meta"><span class="badge badge-neutral">${escapeHtml(question.type_label || "题目")}</span>${kind === "like" ? `<button class="personal-content-author" type="button" data-action="open-profile" data-user-id="${escapeAttr(item.author_id || "")}">评论者 ${escapeHtml(item.author_name || "用户")}</button>` : ""}<time datetime="${escapeAttr(timestamp || "")}">${escapeHtml(formatDate(timestamp))}</time></div>
    <h2>${escapeHtml(question.stem || "原题已不可用")}</h2>
    ${sourceMeta ? `<p class="personal-content-path">${escapeHtml(sourceMeta)}</p>` : ""}
    <div class="personal-content-body ${kind}">${escapeHtml(item.body || "")}</div>
    ${kind !== "note" ? `<div class="personal-content-stats"><span>赞同 ${formatNumber(item.agree_count || 0)}</span><span>反对 ${formatNumber(item.disagree_count || 0)}</span><span>回复 ${formatNumber(item.reply_count || 0)}</span></div>` : ""}
    <div class="question-actions">
      ${canOpen ? `<button class="button button-secondary button-small" type="button" data-action="open-personal-source" data-collection="${escapeAttr(question.collection_id)}" data-position="${Number(question.collection_position)}" data-path="${escapeAttr(question.relative_path || "")}">进入原题</button>` : `<span class="badge badge-neutral">原题不可用</span>`}
      ${kind === "comment" ? `<button class="button button-secondary button-small" type="button" data-action="edit-comment" data-comment-id="${escapeAttr(item.comment_id)}" data-occurrence="${occurrenceId}">编辑</button><button class="button button-danger button-small" type="button" data-action="delete-comment" data-comment-id="${escapeAttr(item.comment_id)}" data-occurrence="${occurrenceId}" data-from-list="true">删除</button>` : kind === "note" ? `<button class="button button-secondary button-small" type="button" data-action="edit-personal-note" data-occurrence="${occurrenceId}">编辑</button><button class="button button-danger button-small" type="button" data-action="delete-note" data-occurrence="${occurrenceId}" data-from-list="true">删除</button>` : `<button class="button button-danger button-small" type="button" data-action="react-comment" data-comment-id="${escapeAttr(item.comment_id)}" data-reaction="agree" data-occurrence="${occurrenceId}">取消赞同</button>`}
    </div>
  </article>`;
}

async function saveProfile(form) {
  const values = Object.fromEntries(new FormData(form));
  const username = String(values.username || "").trim();
  try {
    const result = await api("/auth/profile", {
      method: "PUT",
      body: {
        username,
        avatar: (state.profileAvatarDraft ?? state.user.avatar) || "preset-01",
        birthday: values.birthday || "",
        gender: values.gender || "",
        school: values.school || "",
        major: values.major || "",
        privacy: { birthday: Boolean(values.privacy_birthday), gender: Boolean(values.privacy_gender), school: Boolean(values.privacy_school), major: Boolean(values.privacy_major) },
      },
    });
    state.user = result.user;
    state.profileAvatarDraft = null;
    state.comments = {};
    state.myComments = null;
    state.myLikes = null;
    syncUserChrome(state.user);
    state.profileEditing = false;
    await renderPublicProfilePage(state.user.user_id);
    showToast("个人资料已保存");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveProfilePassword(form) {
  const values = Object.fromEntries(new FormData(form));
  const errorElement = $("[data-profile-password-error]", form);
  if (values.new_password !== values.confirm_password) {
    errorElement.textContent = "两次输入的新密码不一致";
    return;
  }
  try {
    await api("/auth/password", {
      method: "POST",
      body: { current_password: values.current_password, new_password: values.new_password },
    });
    state.user = null;
    state.session = null;
    state.comments = {};
    state.notes = {};
    state.myComments = null;
    state.myNotes = null;
    location.hash = "";
    showAuth("login");
    showToast("密码已修改，请重新登录");
  } catch (error) {
    errorElement.textContent = error.message;
  }
}

async function renderAdminPage() {
  const container = $("#page-admin");
  container.innerHTML = `${adminHeaderMarkup()}${loadingMarkup("正在读取系统状态")}`;
  try {
    const [dashboard, settings, users, comments, vectors] = await Promise.all([
      api("/admin/dashboard"),
      api("/admin/settings"),
      api("/admin/users"),
      api("/admin/comments?limit=100"),
      api("/admin/vectors"),
    ]);
    state.adminData = { dashboard, settings, users: users.users || [], comments, vectors };
    renderAdminContent();
    if (["queued", "running"].includes(vectors.operation?.status)) scheduleVectorPoll();
  } catch (error) {
    container.innerHTML += emptyMarkup("管理数据读取失败", error.message, "!");
  }
}

function renderAdminContent() {
  const data = state.adminData;
  if (!data) return;
  $("#page-admin").innerHTML = `
    ${adminHeaderMarkup()}
    <div class="admin-layout">
      <nav class="card admin-nav" aria-label="管理导航">
        ${adminTabButton("overview", "运行概览")}${adminTabButton("users", "账号管理")}${adminTabButton("comments", "评论管理")}${adminTabButton("models", "模型管理")}${adminTabButton("site", "网站设置")}
      </nav>
      <div class="admin-content">${renderAdminTab()}</div>
    </div>`;
}

function adminHeaderMarkup() {
  return `<div class="page-header"><div class="page-title"><p class="eyebrow">系统管理</p><h1>管理控制台</h1><p class="section-copy">账号、模型、检索能力与运行状态集中管理。</p></div><div class="header-actions"><button class="button button-secondary button-small" type="button" data-action="return-from-admin">‹ 返回</button></div></div>`;
}

function adminTabButton(tab, label) {
  return `<button class="${state.adminTab === tab ? "active" : ""}" type="button" data-action="admin-tab" data-tab="${tab}">${label}</button>`;
}

function renderAdminTab() {
  const { dashboard, settings, users, comments } = state.adminData;
  if (state.adminTab === "users") return renderUsers(users);
  if (state.adminTab === "comments") return renderAdminComments(comments);
  if (state.adminTab === "models") return renderModelSettings(settings);
  if (state.adminTab === "site") return renderSiteSettings(settings);
  const vector = dashboard.vector_index || {};
  const vectorReady = vector.status === "ready";
  return `<section class="card admin-section">
    <div class="section-heading"><div><h2>运行概览</h2><p>题库、用户、学习数据与检索服务状态。</p></div></div>
    <div class="admin-status-banner ${vectorReady ? "ready" : "pending"}">
      <span class="status-dot" aria-hidden="true"></span>
      <div><strong>${vectorReady ? "系统运行正常，语义检索可用" : "系统运行正常，语义检索待配置"}</strong><small>${vectorReady ? `${escapeHtml(vector.model || "嵌入模型")} · ${formatNumber(vector.count || 0)} 条向量` : "关键词搜题、刷题、收藏与错题功能不受影响"}</small></div>
      <span class="badge ${vectorReady ? "" : "badge-warn"}">${vectorReady ? "全部可用" : "基础功能可用"}</span>
    </div>
    <div class="stats-grid">
      ${statCard("注册用户", dashboard.accounts.users, `${dashboard.accounts.admins} 位管理员`)}
      ${statCard("有效登录", dashboard.accounts.active_sessions, "当前未过期会话")}
      ${statCard("答题次数", dashboard.activity.answers, `${dashboard.activity.sessions} 份练习`)}
      ${statCard("规范题目", dashboard.catalog.questions, `${formatNumber(dashboard.catalog.collections)} 个题库文件`)}
    </div>
    <div class="settings-grid">
      <div class="settings-group"><h3>学习数据</h3><div class="solution-row"><strong>收藏</strong><span>${formatNumber(dashboard.activity.favorites)}</span></div><div class="solution-row"><strong>错题</strong><span>${formatNumber(dashboard.activity.wrong_items)}</span></div><div class="solution-row"><strong>评论</strong><span>${formatNumber(dashboard.activity.comments)}</span></div><div class="solution-row"><strong>私有笔记</strong><span>${formatNumber(dashboard.activity.notes)}</span></div></div>
      <div class="settings-group"><h3>向量索引</h3><div class="solution-row"><strong>状态</strong><span>${escapeHtml(vector.status || "unavailable")}</span></div><div class="solution-row"><strong>模型</strong><span>${escapeHtml(vector.model || "尚未构建")}</span></div><div class="solution-row"><strong>向量数</strong><span>${formatNumber(vector.count || 0)}</span></div></div>
    </div>
  </section>`;
}

function renderUsers(users) {
  const admins = users.filter((user) => user.role === "admin" && user.status === "active").length;
  const disabled = users.filter((user) => user.status === "disabled").length;
  return `<section class="card admin-section">
    <div class="section-heading"><div><h2>账号管理</h2><p>创建账号、调整角色、重置密码或停用访问权限。</p></div></div>
    <div class="admin-mini-stats"><span><strong>${users.length}</strong>全部账号</span><span><strong>${admins}</strong>有效管理员</span><span><strong>${disabled}</strong>已停用</span></div>
    <form id="create-user-form" class="settings-group create-user-box">
      <h3>创建账号</h3><div class="inline-fields"><label class="field"><span>用户名</span><input name="username" required minlength="3" maxlength="32"></label><label class="field"><span>角色</span><select name="role"><option value="user">普通用户</option><option value="admin">管理员</option></select></label></div>
      <label class="field field-spaced"><span>初始密码</span><input name="password" type="password" required minlength="10"></label>
      <div class="question-actions"><button class="button button-primary button-small" type="submit">创建账号</button></div>
    </form>
    <div class="table-wrap user-table-wrap"><table class="data-table user-table"><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${users.map((user) => `<tr>
       <td data-label="用户"><div class="user-identity"><span>${avatarVisual(user, user.username)}</span><strong>${escapeHtml(user.username)}</strong></div></td>
      <td data-label="角色"><select class="compact-select" data-user-role="${escapeAttr(user.user_id)}" ${user.user_id === state.user.user_id ? "disabled" : ""}><option value="user" ${user.role === "user" ? "selected" : ""}>普通用户</option><option value="admin" ${user.role === "admin" ? "selected" : ""}>管理员</option></select></td>
      <td data-label="状态"><span class="status-pill ${user.status}">${user.status === "active" ? "正常" : "已停用"}</span></td>
      <td data-label="最近登录">${formatDate(user.last_login_at)}</td>
      <td data-label="操作"><div class="table-actions">${user.user_id === state.user.user_id ? `<span class="current-user-label">当前账号</span><button class="button button-secondary button-small" type="button" data-action="reset-user-password" data-id="${escapeAttr(user.user_id)}" data-name="${escapeAttr(user.username)}">修改密码</button>` : `<button class="button button-secondary button-small" type="button" data-action="update-user-role" data-id="${escapeAttr(user.user_id)}">保存角色</button><button class="button button-secondary button-small" type="button" data-action="reset-user-password" data-id="${escapeAttr(user.user_id)}" data-name="${escapeAttr(user.username)}">重置密码</button><button class="button ${user.status === "active" ? "button-danger" : "button-quiet"} button-small" type="button" data-action="toggle-user-status" data-id="${escapeAttr(user.user_id)}" data-status="${user.status === "active" ? "disabled" : "active"}">${user.status === "active" ? "停用" : "启用"}</button>`}</div></td>
    </tr>`).join("")}</tbody></table></div>
  </section>`;
}

function renderAdminComments(payload = {}) {
  const items = payload.items || [];
  return `<section class="card admin-section">
    <div class="section-heading"><div><h2>评论管理</h2><p>查看全站讨论，按用户或内容筛选，并处理不适当评论。</p></div></div>
    <div class="admin-mini-stats"><span><strong>${formatNumber(payload.total || 0)}</strong>全部评论</span><span><strong>${formatNumber(items.length)}</strong>当前显示</span></div>
    <form id="admin-comment-search" class="admin-comment-search"><label class="field"><span>筛选评论</span><input name="query" value="${escapeAttr(payload.query || "")}" placeholder="输入用户名或评论内容"></label><button class="button button-secondary" type="submit">筛选</button></form>
    ${items.length ? `<div class="admin-comment-list">${items.map((item) => {
      const question = item.question || {};
      return `<article class="admin-comment-item">
        <div class="comment-avatar">${avatarVisual(item, item.author_name)}</div>
        <div class="admin-comment-main"><div class="comment-meta"><strong>${escapeHtml(item.author_name || "用户")}</strong><time>${escapeHtml(formatDate(item.updated_at))}${item.edited ? " · 已编辑" : ""}</time></div><p>${escapeHtml(item.body)}</p><div class="admin-comment-source"><span>${escapeHtml(question.stem || "原题已不可用")}</span><small>${escapeHtml(displaySourcePath(question.relative_path || ""))}</small></div></div>
        <button class="button button-danger button-small" type="button" data-action="admin-delete-comment" data-comment-id="${escapeAttr(item.comment_id)}">删除</button>
      </article>`;
    }).join("")}</div>` : emptyMarkup("没有匹配的评论", "调整筛选条件后重试。", "◌")}
  </section>`;
}

function renderModelSettings(settings) {
  const embedding = settings.embedding || {};
  const llm = settings.llm || {};
  const vectorPayload = state.adminData?.vectors || {};
  const vector = vectorPayload.vector_index || settings.vector_index || {};
  const operation = vectorPayload.operation || { status: "idle" };
  const vectorReady = vector.status === "ready";
  return `<section class="card admin-section">
    <div class="section-heading"><div><h2>模型管理</h2><p>嵌入模型与大语言模型独立配置，未保存密钥只用于当前连接请求。</p></div></div>
    <div class="model-status-grid">
      <div><span class="status-dot ${embedding.api_key_configured ? "ready" : ""}"></span><small>嵌入模型</small><strong>${embedding.api_key_configured ? "已配置" : "未配置"}</strong></div>
      <div><span class="status-dot ${vectorReady ? "ready" : ""}"></span><small>向量索引</small><strong>${vectorReady ? `${formatNumber(vector.count || 0)} 条` : "未构建"}</strong></div>
      <div><span class="status-dot ${llm.api_key_configured ? "ready" : ""}"></span><small>大语言模型</small><strong>${llm.api_key_configured ? "已配置" : "未配置"}</strong></div>
    </div>
    <div class="provider-settings-grid">
      ${providerPanel("embedding", "嵌入模型", embedding, true)}
      ${providerPanel("llm", "大语言模型", llm, false)}
    </div>
    <form id="semantic-settings-form" class="settings-group semantic-settings-card">
      <h3>检索策略</h3>
      <div class="switch-row"><div class="switch-copy"><strong>启用语义检索</strong><small>需要当前嵌入配置与已激活向量索引完全匹配</small></div><label class="switch"><input name="semantic_enabled" type="checkbox" ${settings.semantic_enabled ? "checked" : ""}><span></span></label></div>
      <label class="field"><span>默认搜索模式</span><select name="default_search_mode"><option value="keyword" ${settings.default_search_mode === "keyword" ? "selected" : ""}>关键词</option><option value="hybrid" ${settings.default_search_mode === "hybrid" ? "selected" : ""}>混合</option><option value="semantic" ${settings.default_search_mode === "semantic" ? "selected" : ""}>语义</option></select></label>
      <div class="question-actions"><button class="button button-primary" type="submit">保存检索设置</button></div>
    </form>
    ${renderVectorManagement(vector, operation)}
  </section>`;
}

function providerPanel(prefix, title, provider, dimensions) {
  const modelState = state.providerModels[prefix] || { status: "idle", items: [], open: false, error: "" };
  return `<form class="settings-group provider-settings-card" data-provider-form="${prefix}">
    <div class="provider-card-heading"><div><h3>${title}</h3><span>${provider.api_key_configured ? `已保存 ${escapeHtml(provider.api_key_masked || "")}` : "尚未保存密钥"}</span></div><span class="status-dot ${provider.api_key_configured ? "ready" : ""}"></span></div>
    <label class="field"><span>API 地址</span><input name="api_url" type="url" value="${escapeAttr(provider.api_url || "")}" required></label>
    <div class="field model-select-field"><span>模型</span><div class="model-combobox"><input name="model" data-provider-model="${prefix}" value="${escapeAttr(provider.model || "")}" required autocomplete="off"><button type="button" data-action="toggle-model-list" data-provider="${prefix}" aria-label="展开上游模型" aria-expanded="${modelState.open ? "true" : "false"}">⌄</button></div>${renderProviderModelDropdown(prefix, modelState)}</div>
    ${dimensions ? `<label class="field"><span>向量维度</span><input name="dimensions" type="number" min="64" max="1024" step="64" value="${provider.dimensions || 1024}" required></label>` : ""}
    <label class="field"><span>API 密钥</span><input name="api_key" data-provider-key="${prefix}" type="password" autocomplete="new-password" placeholder="${provider.api_key_configured ? `留空则继续使用 ${escapeAttr(provider.api_key_masked || "")}` : "输入后自动读取上游模型"}"></label>
    <div class="provider-card-actions"><button class="button button-secondary" type="button" data-action="test-provider" data-provider="${prefix}">测试连接</button><button class="button button-primary" type="submit">保存配置</button></div>
  </form>`;
}

function renderProviderModelDropdown(provider, modelState) {
  const items = modelState.items || [];
  const content = modelState.status === "loading"
    ? `<div class="model-dropdown-state"><span class="loading-spinner"></span>正在读取上游模型</div>`
    : modelState.error
      ? `<div class="model-dropdown-state error"><span>${escapeHtml(modelState.error)}</span><button class="text-button" type="button" data-action="discover-models" data-provider="${provider}">重试</button></div>`
      : items.length
        ? `<div class="model-option-list">${items.map((item) => `<button type="button" data-action="select-provider-model" data-provider="${provider}" data-model="${escapeAttr(item.id)}"><strong>${escapeHtml(item.id)}</strong>${item.owned_by ? `<small>${escapeHtml(item.owned_by)}</small>` : ""}</button>`).join("")}</div>`
        : `<div class="model-dropdown-state">输入 API 密钥后自动读取，或点击刷新。</div>`;
  return `<div id="${provider}-model-dropdown" class="model-dropdown" ${modelState.open ? "" : "hidden"}><div class="model-dropdown-head"><span>${provider === "embedding" ? "嵌入模型" : "大语言模型"} · ${formatNumber(items.length)} 个</span><button class="text-button" type="button" data-action="discover-models" data-provider="${provider}">刷新</button></div>${content}</div>`;
}

function renderVectorManagement(vector, operation) {
  const running = ["queued", "running"].includes(operation.status);
  const complete = Number(operation.completed || 0);
  const total = Number(operation.total || 0);
  const percent = total ? Math.min(100, Math.round((complete / total) * 100)) : 0;
  const statusLabel = running ? "正在构建" : operation.status === "failed" ? "构建异常" : vector.status === "ready" ? "已激活" : "未构建";
  return `<section id="vector-management-card" class="settings-group vector-management-card">
    <div class="vector-management-head"><div><h3>向量索引</h3><p>支持中断续建；catalog 更新后会复用未变化题目的向量，仅补齐新增或变化内容。</p></div><span class="status-pill ${running ? "active" : vector.status === "ready" ? "complete" : "disabled"}">${statusLabel}</span></div>
    ${running ? `<div class="vector-progress"><div><span style="width:${percent}%"></span></div><p><strong>${formatNumber(complete)} / ${formatNumber(total || 0)}</strong><span>${percent}%</span></p></div>` : ""}
    ${operation.status === "failed" ? `<p class="vector-operation-error">${escapeHtml(operation.error || "构建失败，可重新开始以续建")}</p>` : ""}
    <div class="vector-facts"><span><small>当前模型</small><strong>${escapeHtml(vector.model || "尚未构建")}</strong></span><span><small>向量数量</small><strong>${formatNumber(vector.count || 0)}</strong></span><span><small>维度</small><strong>${formatNumber(vector.dimensions || 0)}</strong></span></div>
    <div class="provider-card-actions"><button class="button button-primary" type="button" data-action="build-vectors" ${running ? "disabled" : ""}>${operation.status === "failed" ? "继续构建" : vector.status === "ready" ? "检查并增量更新" : "构建向量索引"}</button><button class="button button-danger" type="button" data-action="delete-vectors" ${running || vector.status !== "ready" ? "disabled" : ""}>删除向量数据</button></div>
  </section>`;
}

function renderSiteSettings(settings) {
  return `<section class="card admin-section"><div class="section-heading"><div><h2>网站设置</h2><p>设置站点名称、注册策略和默认结果数量。</p></div></div><form id="site-settings-form" class="stack-form"><label class="field"><span>站点名称</span><input name="site_name" value="${escapeAttr(settings.site_name || "医考知库")}" minlength="2" maxlength="30" required></label><label class="field"><span>默认搜索结果数</span><input name="search_result_limit" type="number" min="1" max="50" value="${settings.search_result_limit || 10}" required></label><div class="settings-group"><div class="switch-row"><div class="switch-copy"><strong>开放用户注册</strong><small>关闭后仅管理员可在账号管理中创建用户</small></div><label class="switch"><input name="allow_registration" type="checkbox" ${settings.allow_registration ? "checked" : ""}><span></span></label></div></div><div class="question-actions"><button class="button button-primary" type="submit">保存网站设置</button></div></form></section>`;
}

async function handleClick(event) {
  if (Date.now() < state.suppressClickUntil) return;
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "toggle-password") {
    const input = $("#auth-password");
    input.type = input.type === "password" ? "text" : "password";
    target.textContent = input.type === "password" ? "显示" : "隐藏";
    return;
  }
  if (action === "auth-mode") return showAuth(target.dataset.mode);
  if (action === "open-messages") {
    if (!location.hash.startsWith("#messages")) state.accountReturnHash = location.hash || "#library";
    state.messageData = null;
    return navigateHash("#messages");
  }
  if (action === "toggle-theme") return toggleTheme();
  if (action === "message-tab") {
    state.messageTab = target.dataset.tab || "system";
    if (state.messageData) renderMessageCenter(state.messageData);
    return;
  }
  if (action === "open-personal-page") {
    event.preventDefault();
    const page = String(target.dataset.page || "");
    if (!["favorites", "wrong", "notes", "my-comments", "my-likes"].includes(page)) return;
    state.studyReturnHash = normalizeStudyReturnHash(target.dataset.returnHash, "#library");
    const destination = studyPageHash(page, state.studyReturnHash);
    if (location.hash === destination) return;
    return navigateHash(destination);
  }
  if (action === "open-own-profile") { state.profileEditing = false; state.profileReturnHash = "#profile"; return navigateHash(`#profile-detail?user=${encodeURIComponent(state.user.user_id)}`); }
  if (action === "back-to-my-home") { state.profileEditing = false; return navigateHash("#profile"); }
  if (action === "edit-profile") { state.profileEditing = true; return renderPublicProfilePage(state.user.user_id); }
  if (action === "cancel-profile-edit") { state.profileEditing = false; return renderPublicProfilePage(state.user.user_id); }
  if (action === "toggle-follow") return toggleFollowProfile(target);
  if (action === "message-profile") return messageProfile(target.dataset.userId, target.dataset.name);
  if (action === "home-utility") return handleHomeUtility(target.dataset.utility);
  if (action === "show-placeholder") return showToast(`${target.dataset.title || "该功能"}即将开放`);
  if (action === "open-profile") { state.profileEditing = false; state.profileReturnHash = location.hash || "#profile"; return navigateHash(`#profile-detail?user=${encodeURIComponent(target.dataset.userId || state.user.user_id)}`); }
  if (action === "return-from-profile") return navigateHash(state.profileReturnHash || "#profile");
  if (action === "return-from-account") return navigateHash(state.accountReturnHash || "#library");
  if (action === "return-to-profile") return navigateHash("#profile");
  if (action === "return-from-study") return navigateHash(state.studyReturnHash || "#library");
  if (action === "select-avatar") {
    state.profileAvatarDraft = target.dataset.avatar || "";
    renderProfileEditorPage(state.user);
    return;
  }
  if (action === "choose-avatar-file") {
    $("#avatar-file-input")?.click();
    return;
  }
  if (action === "logout") return logout();
  if (action === "open-admin") {
    if (!location.hash.startsWith("#admin")) state.adminReturnHash = location.hash || "#library";
    return navigateHash("#admin");
  }
  if (action === "return-from-admin") return navigateHash(state.adminReturnHash || "#library");
  if (action === "library-home") {
    event.preventDefault();
    state.libraryPath.library = "";
    state.outline = null;
    setPracticeOutline(false);
    return navigateHash("#library");
  }
  if (action === "library-view-mode") {
    const mode = target.dataset.mode === "guided" ? "guided" : "folders";
    if (state.libraryViewMode === mode) return;
    state.libraryViewMode = mode;
    if (mode === "guided") {
      const parts = String(state.libraryPath.library || "").split("/").filter(Boolean);
      state.libraryGuideExam = parts[0] || state.libraryGuideExam;
      state.libraryGuideKind = parts.length > 1 ? `${parts[0]}/${parts[1]}` : "";
      state.libraryGuideExpanded.clear();
    }
    savePreferences();
    return renderLibrary("library", { preserveContent: true, preserveScroll: true });
  }
  if (action === "guide-select") return selectGuideLevel(target.dataset.level, target.dataset.path);
  if (action === "toggle-guide-tags") {
    const level = target.dataset.level;
    if (!Object.prototype.hasOwnProperty.call(state.libraryGuideTagsExpanded, level)) return;
    const expanded = !state.libraryGuideTagsExpanded[level];
    state.libraryGuideTagsExpanded[level] = expanded;
    target.setAttribute("aria-expanded", String(expanded));
    target.setAttribute("aria-label", `${expanded ? "收起" : "展开"}${level === "exam" ? "考试" : "题库"}标签`);
    target.innerHTML = `${expanded ? "收起" : "展开"}<span aria-hidden="true">${expanded ? "⌃" : "⌄"}</span>`;
    const tags = target.closest(".guide-tag-row")?.querySelector(".guide-tags");
    tags?.classList.toggle("expanded", expanded);
    tags?.classList.toggle("collapsed", !expanded);
    return;
  }
  if (action === "toggle-guide-branch") return toggleGuideBranch(target.dataset.path);
  if (action === "open-directory") {
    state.libraryPath[target.dataset.kind] = target.dataset.path;
    return navigateHash(libraryRouteHash(target.dataset.kind, target.dataset.path));
  }
  if (action === "library-breadcrumb") {
    state.libraryPath[target.dataset.kind] = target.dataset.path;
    return navigateHash(libraryRouteHash(target.dataset.kind, target.dataset.path));
  }
  if (action === "open-collection") {
    const kind = target.dataset.kind;
    if (target.dataset.parentPath) state.libraryPath[kind] = target.dataset.parentPath;
    return navigateHash(libraryRouteHash(kind, state.libraryPath[kind], target.dataset.id));
  }
  if (action === "close-outline") {
    const kind = target.dataset.kind;
    return navigateHash(libraryRouteHash(kind, state.libraryPath[kind]));
  }
  if (action === "outline-mode") {
    state.outlineMode = target.dataset.mode;
    renderOutline();
    return;
  }
  if (action === "reset-collection-progress") return resetCollectionProgress(target);
  if (action === "start-session") {
    return startSession({ collectionId: target.dataset.id, position: target.dataset.position, kind: target.dataset.kind, mode: state.outlineMode });
  }
  if (action === "practice-source") {
    return startSession({ collectionId: target.dataset.id, path: target.dataset.path, position: target.dataset.position, kind: "library", mode: "practice" });
  }
  if (action === "search-mode") {
    state.searchMode = target.dataset.mode;
    savePreferences();
    renderSearchPage();
    return;
  }
  if (action === "search-solution-toggle") return toggleSearchSolution(target.dataset.field);
  if (action === "search-field-toggle") return toggleSearchField(target.dataset.field);
  if (action === "search-question-type") return toggleSearchQuestionType(target.dataset.type);
  if (action === "search-all-toggle") return toggleSearchAll(target.dataset.group);
  if (action === "reset-search-settings") return resetSearchSettings();
  if (action === "toggle-search-settings") return toggleSearchSettings();
  if (action === "leave-session") {
    clearSelectionTimer();
    setPracticeOutline(false);
    const kind = state.session?.saved_kind === "favorite" ? "favorites" : state.session?.saved_kind === "wrong" ? "wrong" : "library";
    const collectionId = state.session?.collection_id || "";
    const returnHash = state.sessionReturnTarget?.hash
      || libraryRouteHash(kind, state.libraryPath[kind], collectionId);
    return navigateHash(returnHash);
  }
  if (action === "session-prev") return moveSession(-1);
  if (action === "session-next") return moveSession(1);
  if (action === "session-jump") {
    state.sessionIndex = Number(target.dataset.index);
    setPracticeOutline(false);
    renderPractice();
    return;
  }
  if (action === "toggle-outline") {
    setPracticeOutline(!$("#practice-outline")?.classList.contains("open"));
    return;
  }
  if (action === "close-practice-outline") return setPracticeOutline(false);
  if (action === "select-option") return selectAnswerOption(target.dataset.label);
  if (action === "submit-answer") return submitCurrentAnswer();
  if (action === "finish-session") return finishCurrentSession();
  if (action === "toggle-favorite") return toggleFavorite(target);
  if (action === "remove-current-wrong") return removeCurrentWrong(target.dataset.key);
  if (action === "remove-saved-outline") return removeSavedOutlineItem(target.dataset.kind, target.dataset.key);
  if (action === "reload-comments") return ensureQuestionComments(Number(target.dataset.occurrence), { force: true });
  if (action === "delete-comment") return deleteQuestionComment(target.dataset.commentId, Number(target.dataset.occurrence));
  if (action === "edit-comment") return editQuestionComment(target.dataset.commentId, Number(target.dataset.occurrence));
  if (action === "react-comment") return reactComment(target.dataset.commentId, target.dataset.reaction, Number(target.dataset.occurrence));
  if (action === "reply-comment") return replyToComment(target.dataset.commentId, Number(target.dataset.occurrence));
  if (action === "reload-note") return ensureQuestionNote(Number(target.dataset.occurrence), { force: true });
  if (action === "delete-note") return deleteQuestionNote(Number(target.dataset.occurrence), { fromList: target.dataset.fromList === "true" });
  if (action === "edit-personal-note") return editPersonalNote(Number(target.dataset.occurrence));
  if (action === "load-more-personal") return loadMorePersonalFeed(target.dataset.kind, target);
  if (action === "toggle-model-list") return toggleProviderModelList(target.dataset.provider);
  if (action === "discover-models") return discoverProviderModels(target.dataset.provider);
  if (action === "select-provider-model") return selectProviderModel(target.dataset.provider, target.dataset.model);
  if (action === "build-vectors") return buildVectors();
  if (action === "delete-vectors") return deleteVectors();
  if (action === "admin-delete-comment") return adminDeleteComment(target.dataset.commentId);
  if (action === "open-personal-source") return openPersonalSource(target);
  if (action === "admin-tab") {
    state.adminTab = target.dataset.tab;
    renderAdminContent();
    return;
  }
  if (action === "toggle-user-status") return toggleUserStatus(target.dataset.id, target.dataset.status);
  if (action === "update-user-role") return updateUserRole(target.dataset.id);
  if (action === "reset-user-password") return resetUserPassword(target.dataset.id, target.dataset.name);
  if (action === "test-provider") return testProvider(target.dataset.provider, target);
}

async function handleSubmit(event) {
  event.preventDefault();
  if (event.target.id === "auth-form") return submitAuth(event.target);
  if (event.target.id === "search-form") return submitSearch(event.target);
  if (event.target.id === "profile-form") return saveProfile(event.target);
  if (event.target.id === "settings-password-form") return saveProfilePassword(event.target);
  if (event.target.id === "semantic-settings-form") return saveSemanticSettings(event.target);
  if (event.target.id === "site-settings-form") return saveSiteSettings(event.target);
  if (event.target.id === "create-user-form") return createUser(event.target);
  if (event.target.id === "admin-comment-search") return searchAdminComments(event.target);
  if (event.target.dataset.providerForm) return saveProviderSettings(event.target);
  if (event.target.dataset.form === "question-comment") return submitQuestionComment(event.target);
  if (event.target.dataset.form === "question-note") return submitQuestionNote(event.target);
}

document.addEventListener("input", (event) => {
  if (event.target.dataset.providerKey) {
    scheduleProviderDiscovery(event.target.dataset.providerKey);
    return;
  }
  if (event.target.dataset.action !== "subjective-draft" || !state.session) return;
  const item = state.session.items[state.sessionIndex];
  state.drafts[item.position] = event.target.value.trim();
  const submit = $('[data-action="submit-answer"]');
  if (submit) submit.disabled = !state.drafts[item.position];
});

document.addEventListener("change", (event) => {
  if (event.target.id !== "avatar-file-input") return;
  const file = event.target.files?.[0];
  if (file) openAvatarCrop(file);
  event.target.value = "";
});

async function submitAuth(form) {
  const submit = $("#auth-submit");
  setButtonBusy(submit, true, "请稍候");
  setAuthError("");
  try {
    const data = Object.fromEntries(new FormData(form));
    const path = state.authMode === "setup" ? "/auth/setup" : state.authMode === "register" ? "/auth/register" : "/auth/login";
    const result = await api(path, { method: "POST", body: data, authRedirect: false });
    await enterApp(result.user);
  } catch (error) {
    setAuthError(error.message);
  } finally {
    setButtonBusy(submit, false);
  }
}

async function logout() {
  try {
    await api("/auth/logout", { method: "POST", body: {} });
  } catch (_) {
    // Clearing the local UI is still correct when the server session expired.
  }
  state.user = null;
  state.session = null;
  state.comments = {};
  state.notes = {};
  state.myComments = null;
  state.myNotes = null;
  location.hash = "";
  showAuth("login");
}

async function submitSearch(form) {
  const query = new FormData(form).get("query")?.trim();
  if (!query) return;
  await runSearch(query);
}

async function runSearch(query) {
  state.lastSearchQuery = query;
  const results = $("#search-results");
  if (!results) return;
  results.innerHTML = loadingMarkup("正在检索题库");
  try {
    const includeSolution = ["answer", "point", "explanation"]
      .some((field) => state.searchSolutionVisibility[field]);
    const searchFields = Object.entries(state.searchFields)
      .filter(([, enabled]) => enabled)
      .map(([field]) => field);
    const payload = await api("/search", {
      method: "POST",
      body: {
        query,
        mode: state.searchMode,
        limit: state.settings?.search_result_limit || 10,
        include_solution: includeSolution,
        search_fields: searchFields,
        question_types: state.searchQuestionTypes,
      },
    });
    state.searchResults = payload.results || [];
    state.searchMetadata = payload;
    results.innerHTML = renderSearchResults(state.searchResults, payload);
    refreshFavoriteButtons();
  } catch (error) {
    results.innerHTML = emptyMarkup("检索失败", error.message, "!");
  }
}

function selectAnswerOption(label) {
  const item = state.session.items[state.sessionIndex];
  const mode = state.session.mode || "practice";
  if (mode === "speed" && item.submission) {
    const correct = new Set(String(item.submission.correct_answer || "").split(""));
    if (correct.has(label)) moveSession(1);
    return;
  }
  const current = new Set(String(state.drafts[item.position] || "").split(""));
  if (item.question.question_type === "多选题") {
    current.has(label) ? current.delete(label) : current.add(label);
    state.drafts[item.position] = [...current].sort().join("");
  } else {
    state.drafts[item.position] = label;
  }
  renderPractice();
  if (["speed", "test"].includes(mode)) {
    clearSelectionTimer();
    const position = item.position;
    const delay = item.question.question_type === "多选题" ? 520 : 0;
    state.selectionTimer = window.setTimeout(() => {
      state.selectionTimer = null;
      const currentItem = state.session?.items?.[state.sessionIndex];
      if (currentItem?.position === position && !currentItem.submission) {
        submitCurrentAnswer({ autoAdvance: true });
      }
    }, delay);
  }
}

function moveSession(delta) {
  const next = state.sessionIndex + delta;
  if (next < 0) {
    showToast("已经是第一题");
    return;
  }
  if (next >= state.session.items.length) {
    showToast("已经是最后一题");
    return;
  }
  clearSelectionTimer();
  state.navigationDirection = delta > 0 ? "forward" : "backward";
  state.sessionIndex = next;
  renderPractice();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function clearSelectionTimer() {
  if (state.selectionTimer) window.clearTimeout(state.selectionTimer);
  state.selectionTimer = null;
}

function handlePracticePointerDown(event) {
  if (!event.isPrimary || !document.body.classList.contains("in-session")) return;
  const outlineHandle = event.target.closest(".practice-outline-grabber, .practice-outline-head");
  if (outlineHandle && $("#practice-outline")?.classList.contains("open")) {
    state.gestureStart = { kind: "outline", x: event.clientX, y: event.clientY, time: Date.now(), pointerId: event.pointerId };
    return;
  }
  if (!event.target.closest(".practice-question") || event.target.closest("input, textarea, select")) return;
  state.gestureStart = { kind: "question", x: event.clientX, y: event.clientY, time: Date.now(), pointerId: event.pointerId };
}

function handlePracticePointerUp(event) {
  const start = state.gestureStart;
  state.gestureStart = null;
  if (!start || start.pointerId !== event.pointerId) return;
  const dx = event.clientX - start.x;
  const dy = event.clientY - start.y;
  if (Date.now() - start.time > 750) return;
  if (start.kind === "outline") {
    if (dy > 56 && Math.abs(dy) > Math.abs(dx) * 1.2) {
      state.suppressClickUntil = Date.now() + 250;
      setPracticeOutline(false);
    }
    return;
  }
  if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
  state.suppressClickUntil = Date.now() + 250;
  moveSession(dx < 0 ? 1 : -1);
}

function clearPracticeGesture() {
  state.gestureStart = null;
}

function handlePracticeKeydown(event) {
  if (event.key === "Escape") {
    closeProfileMenu();
    if (!document.body.classList.contains("in-session")) return;
    setPracticeOutline(false);
    return;
  }
  if (!document.body.classList.contains("in-session")) return;
  if (event.target.matches("input, textarea, select")) return;
  if (event.key === "ArrowLeft") moveSession(-1);
  if (event.key === "ArrowRight") moveSession(1);
}

function handlePracticeResize() {
  if (window.innerWidth > 760) setPracticeOutline(false);
}

function setPracticeOutline(open) {
  const outline = $("#practice-outline");
  const backdrop = $("#practice-outline-backdrop");
  const trigger = $('[data-action="toggle-outline"]');
  const shouldOpen = Boolean(open) && window.matchMedia("(max-width: 760px)").matches;
  outline?.classList.toggle("open", shouldOpen);
  backdrop?.classList.toggle("open", shouldOpen);
  trigger?.setAttribute("aria-expanded", String(shouldOpen));
  document.body.classList.toggle("outline-open", shouldOpen);
}

async function toggleFavorite(button) {
  const occurrence = button.dataset.occurrence;
  button.disabled = true;
  try {
    const result = await api("/study/favorites/toggle", {
      method: "POST",
      body: {
        occurrence_id: occurrence ? Number(occurrence) : null,
        question_id: button.dataset.question || "",
      },
    });
    invalidateStudySummary();
    applyFavoriteState(result.occurrence_id, Boolean(result.favorited));
    showToast(result.favorited ? "已收藏，并按原文件路径归档" : "已取消收藏");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function refreshFavoriteButtons() {
  const buttons = $$('[data-action="toggle-favorite"][data-occurrence]')
    .filter((button) => button.dataset.occurrence);
  const occurrenceIds = [...new Set(buttons.map((button) => Number(button.dataset.occurrence)).filter(Number.isFinite))];
  if (!occurrenceIds.length) return;
  try {
    const result = await api(`/study/favorites/status?occurrence_ids=${occurrenceIds.join(",")}`);
    const saved = new Set((result.occurrence_ids || []).map(Number));
    occurrenceIds.forEach((occurrenceId) => applyFavoriteState(occurrenceId, saved.has(occurrenceId)));
  } catch (_) {
    // 收藏状态读取失败不阻断搜题和刷题。
  }
}

function applyFavoriteState(occurrenceId, favorited) {
  $$(`[data-action="toggle-favorite"][data-occurrence="${Number(occurrenceId)}"]`).forEach((button) => {
    button.dataset.favorited = String(favorited);
    button.setAttribute("aria-pressed", String(favorited));
    button.setAttribute("aria-label", favorited ? "取消收藏本题" : "收藏本题");
    button.classList.toggle("favorited", favorited);
    const icon = $(".favorite-icon", button);
    const label = $(".favorite-label", button);
    if (icon) icon.textContent = favorited ? "★" : "☆";
    if (label) label.textContent = favorited ? "已收藏" : "收藏";
  });
}

async function removeSavedOutlineItem(kind, itemKey) {
  if (!state.outline || !itemKey) return;
  try {
    await api(`/study/${encodeURIComponent(kind)}/${encodeURIComponent(itemKey)}`, { method: "DELETE", body: {} });
    state.outline.payload.items = (state.outline.payload.items || [])
      .filter((item) => item.saved?.item_key !== itemKey);
    state.outline.payload.summary = {
      ...(state.outline.payload.summary || {}),
      total: state.outline.payload.items.length,
      saved: state.outline.payload.items.length,
    };
    invalidateStudySummary();
    if (!state.outline.payload.items.length) {
      const returnHash = libraryRouteHash(kind, state.libraryPath[kind]);
      state.outline = null;
      showToast(kind === "wrong" ? "错题已删除" : "收藏已取消");
      await navigateHash(returnHash);
      return;
    }
    renderOutline();
    showToast(kind === "wrong" ? "错题已删除" : "收藏已取消");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function removeCurrentWrong(itemKey) {
  if (!itemKey || itemKey.endsWith("undefined")) return;
  try {
    await api(`/study/wrong/${encodeURIComponent(itemKey)}`, { method: "DELETE", body: {} });
    invalidateStudySummary();
    showToast("已从错题中移除");
    state.session.items.splice(state.sessionIndex, 1);
    if (!state.session.items.length) {
      await navigateHash(state.sessionReturnTarget?.hash || "#wrong");
      return;
    }
    state.sessionIndex = Math.min(state.sessionIndex, state.session.items.length - 1);
    renderPractice();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function toggleUserStatus(userId, status) {
  const verb = status === "disabled" ? "停用" : "启用";
  const confirmed = await confirmModal(`${verb}账号`, `确认${verb}该账号？停用会立即撤销其全部登录会话。`, `确认${verb}`);
  if (!confirmed) return;
  try {
    const updated = await api(`/admin/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: { status } });
    const index = state.adminData.users.findIndex((user) => user.user_id === userId);
    if (index >= 0) state.adminData.users[index] = updated;
    renderAdminContent();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function updateUserRole(userId) {
  const select = $(`[data-user-role="${userId}"]`);
  if (!select) return;
  const current = state.adminData.users.find((user) => user.user_id === userId);
  const role = select.value;
  if (!current || current.role === role) {
    showToast("角色没有变化");
    return;
  }
  if (current.role === "admin" && role === "user") {
    const confirmed = await confirmModal("调整管理员角色", `确认将 ${current.username} 调整为普通用户？`, "确认调整");
    if (!confirmed) {
      select.value = current.role;
      return;
    }
  }
  try {
    const updated = await api(`/admin/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: { role } });
    const index = state.adminData.users.findIndex((user) => user.user_id === userId);
    if (index >= 0) state.adminData.users[index] = updated;
    renderAdminContent();
    showToast("账号角色已更新");
  } catch (error) {
    select.value = current.role;
    showToast(error.message, "error");
  }
}

async function resetUserPassword(userId, username) {
  const password = await passwordModal(username);
  if (!password) return;
  try {
    await api(`/admin/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: { password } });
    if (userId === state.user.user_id) {
      state.user = null;
      state.session = null;
      showAuth("login");
      showToast("密码已修改，请使用新密码重新登录");
      return;
    }
    showToast("密码已重置，该账号的既有会话已撤销");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function createUser(form) {
  const button = $('button[type="submit"]', form);
  setButtonBusy(button, true, "创建中");
  try {
    const body = Object.fromEntries(new FormData(form));
    const user = await api("/admin/users", { method: "POST", body });
    state.adminData.users.push(user);
    renderAdminContent();
    showToast("账号已创建");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveProviderSettings(form) {
  const provider = form.dataset.providerForm;
  const values = Object.fromEntries(new FormData(form));
  const button = $('button[type="submit"]', form);
  setButtonBusy(button, true, "保存中");
  try {
    const config = { api_url: values.api_url, model: values.model };
    if (provider === "embedding") config.dimensions = Number(values.dimensions);
    if (values.api_key) config.api_key = values.api_key;
    state.adminData.settings = await api("/admin/settings", { method: "PUT", body: { providers: { [provider]: config } } });
    state.settings = state.adminData.settings;
    state.providerModels[provider] = { status: "idle", items: [], open: false, error: "" };
    renderAdminContent();
    showToast(`${provider === "embedding" ? "嵌入模型" : "大语言模型"}配置已保存`);
  } catch (error) {
    showToast(error.message, "error");
    setButtonBusy(button, false);
  }
}

async function saveSemanticSettings(form) {
  try {
    state.adminData.settings = await api("/admin/settings", {
      method: "PUT",
      body: {
        app: {
          semantic_enabled: form.elements.semantic_enabled.checked,
          default_search_mode: form.elements.default_search_mode.value,
        },
      },
    });
    state.settings = state.adminData.settings;
    renderAdminContent();
    showToast("检索策略已保存");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function providerForm(provider) {
  return $(`[data-provider-form="${provider}"]`);
}

function providerPreview(provider) {
  const form = providerForm(provider);
  if (!form) return { provider };
  const values = Object.fromEntries(new FormData(form));
  return {
    provider,
    api_url: values.api_url,
    model: values.model,
    api_key: values.api_key || undefined,
    dimensions: provider === "embedding" ? Number(values.dimensions) : undefined,
  };
}

function toggleProviderModelList(provider) {
  const modelState = state.providerModels[provider] || { status: "idle", items: [], open: false, error: "" };
  modelState.open = !modelState.open;
  state.providerModels[provider] = modelState;
  const dropdown = $(`#${provider}-model-dropdown`);
  const trigger = $(`[data-action="toggle-model-list"][data-provider="${provider}"]`);
  if (dropdown) dropdown.hidden = !modelState.open;
  if (trigger) trigger.setAttribute("aria-expanded", String(modelState.open));
  if (modelState.open && !modelState.items.length && modelState.status !== "loading") discoverProviderModels(provider);
}

async function discoverProviderModels(provider) {
  const modelState = state.providerModels[provider] || { status: "idle", items: [], open: true, error: "" };
  modelState.status = "loading";
  modelState.open = true;
  modelState.error = "";
  state.providerModels[provider] = modelState;
  updateProviderModelDropdown(provider);
  try {
    const result = await api("/admin/settings/models", { method: "POST", body: providerPreview(provider) });
    modelState.status = "ready";
    modelState.items = result.models || [];
  } catch (error) {
    modelState.status = "error";
    modelState.error = error.message || "模型读取失败";
  }
  updateProviderModelDropdown(provider);
}

function scheduleProviderDiscovery(provider) {
  if (state.providerDiscoveryTimers[provider]) clearTimeout(state.providerDiscoveryTimers[provider]);
  state.providerDiscoveryTimers[provider] = setTimeout(() => {
    const form = providerForm(provider);
    const key = form?.elements.api_key?.value || "";
    if (key.trim().length >= 8) discoverProviderModels(provider);
  }, 700);
}

function updateProviderModelDropdown(provider) {
  const slot = $(`#${provider}-model-dropdown`);
  const stateValue = state.providerModels[provider] || { status: "idle", items: [], open: false, error: "" };
  if (!slot) return;
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderProviderModelDropdown(provider, stateValue);
  const next = wrapper.firstElementChild;
  slot.replaceWith(next);
  const trigger = $(`[data-action="toggle-model-list"][data-provider="${provider}"]`);
  if (trigger) trigger.setAttribute("aria-expanded", String(stateValue.open));
}

function selectProviderModel(provider, model) {
  const form = providerForm(provider);
  if (form?.elements.model) form.elements.model.value = model;
  state.providerModels[provider].open = false;
  updateProviderModelDropdown(provider);
}

async function testProvider(provider, button) {
  setButtonBusy(button, true, "测试中");
  try {
    const result = await api("/admin/settings/test", { method: "POST", body: providerPreview(provider) });
    showToast(`${provider === "embedding" ? "嵌入模型" : "大语言模型"}连接正常${result.model ? ` · ${result.model}` : ""}`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveSiteSettings(form) {
  const values = Object.fromEntries(new FormData(form));
  try {
    state.adminData.settings = await api("/admin/settings", {
      method: "PUT",
      body: {
        app: {
          site_name: values.site_name,
          search_result_limit: Number(values.search_result_limit),
          allow_registration: form.elements.allow_registration.checked,
        },
      },
    });
    state.settings = state.adminData.settings;
    $("#site-brand-name").textContent = state.settings.site_name;
    renderAdminContent();
    showToast("网站设置已保存");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function refreshVectorOperation({ render = true } = {}) {
  if (!state.adminData) return;
  try {
    state.adminData.vectors = await api("/admin/vectors");
    const vector = state.adminData.vectors.vector_index || {};
    state.adminData.settings.vector_index = vector;
    state.adminData.dashboard.vector_index = vector;
    if (render && state.adminTab === "models" && location.hash.startsWith("#admin")) {
      const current = $("#vector-management-card");
      if (current) {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = renderVectorManagement(vector, state.adminData.vectors.operation || {});
        current.replaceWith(wrapper.firstElementChild);
      } else {
        renderAdminContent();
      }
    }
    const operation = state.adminData.vectors.operation || {};
    if (["queued", "running"].includes(operation.status)) scheduleVectorPoll();
    else stopVectorPoll();
  } catch (error) {
    if (render) showToast(error.message, "error");
  }
}

function scheduleVectorPoll() {
  if (state.vectorPollHandle) return;
  state.vectorPollHandle = setInterval(() => refreshVectorOperation(), 2500);
}

function stopVectorPoll() {
  if (!state.vectorPollHandle) return;
  clearInterval(state.vectorPollHandle);
  state.vectorPollHandle = null;
}

async function buildVectors() {
  const confirmed = await confirmModal("构建向量索引", "将使用当前嵌入配置构建或续建向量索引。已有题目的向量会复用，新增或变化的题目才会调用上游模型。确定开始？", "开始构建");
  if (!confirmed) return;
  try {
    await api("/admin/vectors/build", { method: "POST", body: { confirmed: true } });
    showToast("向量构建已启动，可离开页面，进度会在下次打开管理页时恢复");
    await refreshVectorOperation();
    scheduleVectorPoll();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function deleteVectors() {
  const confirmed = await confirmModal("删除向量数据", "将删除当前所有向量制品与激活指针，关键词搜题和刷题不受影响；之后需要重新构建才能使用语义搜索。确定删除？", "确认删除");
  if (!confirmed) return;
  try {
    await api("/admin/vectors", { method: "DELETE", body: { confirmed: true } });
    showToast("向量数据已删除，语义搜索已暂时停用");
    await refreshVectorOperation();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function searchAdminComments(form) {
  const query = String(new FormData(form).get("query") || "").trim();
  try {
    state.adminData.comments = await api(`/admin/comments?limit=100&query=${encodeURIComponent(query)}`);
    renderAdminContent();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function adminDeleteComment(commentId) {
  const confirmed = await confirmModal("删除评论", "确认删除这条用户评论？此操作不可恢复。", "确认删除");
  if (!confirmed) return;
  try {
    await api(`/study/comments/${encodeURIComponent(commentId)}`, { method: "DELETE", body: {} });
    const payload = state.adminData.comments;
    payload.items = (payload.items || []).filter((item) => item.comment_id !== commentId);
    payload.total = Math.max(0, Number(payload.total || 0) - 1);
    if (state.adminData.dashboard.activity) state.adminData.dashboard.activity.comments = Math.max(0, Number(state.adminData.dashboard.activity.comments || 0) - 1);
    state.myComments = null;
    state.myLikes = null;
    invalidateStudySummary();
    renderAdminContent();
    showToast("评论已删除");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function openPersonalSource(target) {
  const collectionId = target.dataset.collection;
  const position = Number(target.dataset.position);
  if (!collectionId || !position) return;
  const returnHash = normalizeStudyReturnHash(location.hash, "#library");
  await startSession({
    collectionId,
    position,
    path: collectionParentPath(target.dataset.path || ""),
    kind: "library",
    mode: "practice",
    returnHash,
  });
}

function setButtonBusy(button, busy, label = "处理中") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = label;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function openAvatarCrop(file) {
  if (!file.type.startsWith("image/")) {
    showToast("请选择 JPG、PNG 或 WebP 图片", "error");
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    showToast("原始图片不能超过 20 MB", "error");
    return;
  }
  const url = URL.createObjectURL(file);
  const image = new Image();
  image.onload = () => {
    const baseScale = Math.max(512 / image.naturalWidth, 512 / image.naturalHeight);
    state.avatarCrop = { image, url, baseScale, zoom: 1, offsetX: (512 - image.naturalWidth * baseScale) / 2, offsetY: (512 - image.naturalHeight * baseScale) / 2, dragging: false, pointerX: 0, pointerY: 0 };
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop avatar-crop-backdrop" role="presentation"><section class="modal avatar-crop-modal" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title"><div class="modal-heading"><div><h2 id="avatar-crop-title">调整头像</h2><p>拖动图片调整构图，使用滑杆缩放。</p></div><button class="icon-button" type="button" data-modal="cancel" aria-label="取消">×</button></div><div class="avatar-crop-canvas-wrap"><canvas id="avatar-crop-canvas" width="512" height="512"></canvas><span class="avatar-crop-guide" aria-hidden="true"></span></div><label class="avatar-zoom-control"><span>缩放</span><input id="avatar-crop-zoom" type="range" min="1" max="3" step="0.01" value="1"><output>100%</output></label><div class="modal-actions"><button class="button button-secondary" type="button" data-modal="cancel">取消</button><button class="button button-primary" type="button" data-modal="confirm">使用此头像</button></div></section></div>`;
    const canvas = $("#avatar-crop-canvas");
    const zoom = $("#avatar-crop-zoom");
    const output = $("output", zoom.parentElement);
    const draw = () => drawAvatarCrop(canvas, output);
    zoom.addEventListener("input", () => {
      const previousScale = state.avatarCrop.baseScale * state.avatarCrop.zoom;
      const centerImageX = (256 - state.avatarCrop.offsetX) / previousScale;
      const centerImageY = (256 - state.avatarCrop.offsetY) / previousScale;
      state.avatarCrop.zoom = Number(zoom.value);
      const nextScale = state.avatarCrop.baseScale * state.avatarCrop.zoom;
      state.avatarCrop.offsetX = 256 - centerImageX * nextScale;
      state.avatarCrop.offsetY = 256 - centerImageY * nextScale;
      clampAvatarCrop();
      draw();
    });
    canvas.addEventListener("pointerdown", (event) => {
      state.avatarCrop.dragging = true;
      state.avatarCrop.pointerX = event.clientX;
      state.avatarCrop.pointerY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!state.avatarCrop?.dragging) return;
      const rect = canvas.getBoundingClientRect();
      const scale = 512 / rect.width;
      state.avatarCrop.offsetX += (event.clientX - state.avatarCrop.pointerX) * scale;
      state.avatarCrop.offsetY += (event.clientY - state.avatarCrop.pointerY) * scale;
      state.avatarCrop.pointerX = event.clientX;
      state.avatarCrop.pointerY = event.clientY;
      clampAvatarCrop();
      draw();
    });
    canvas.addEventListener("pointerup", () => { if (state.avatarCrop) state.avatarCrop.dragging = false; });
    canvas.addEventListener("pointercancel", () => { if (state.avatarCrop) state.avatarCrop.dragging = false; });
    $$('[data-modal="cancel"]', root).forEach((button) => button.addEventListener("click", () => closeAvatarCrop()));
    $('[data-modal="confirm"]', root).addEventListener("click", () => uploadCroppedAvatar(canvas));
    $(".avatar-crop-backdrop", root).addEventListener("click", (event) => {
      if (event.target.classList.contains("avatar-crop-backdrop")) closeAvatarCrop();
    });
    draw();
  };
  image.onerror = () => { URL.revokeObjectURL(url); showToast("图片读取失败", "error"); };
  image.src = url;
}

function drawAvatarCrop(canvas, output) {
  const crop = state.avatarCrop;
  if (!crop) return;
  const context = canvas.getContext("2d");
  const scale = crop.baseScale * crop.zoom;
  context.clearRect(0, 0, 512, 512);
  context.fillStyle = "#f3f4f7";
  context.fillRect(0, 0, 512, 512);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(crop.image, crop.offsetX, crop.offsetY, crop.image.naturalWidth * scale, crop.image.naturalHeight * scale);
  if (output) output.value = `${Math.round(crop.zoom * 100)}%`;
}

function clampAvatarCrop() {
  const crop = state.avatarCrop;
  if (!crop) return;
  const scale = crop.baseScale * crop.zoom;
  const width = crop.image.naturalWidth * scale;
  const height = crop.image.naturalHeight * scale;
  crop.offsetX = Math.min(0, Math.max(512 - width, crop.offsetX));
  crop.offsetY = Math.min(0, Math.max(512 - height, crop.offsetY));
}

function closeAvatarCrop() {
  if (state.avatarCrop?.url) URL.revokeObjectURL(state.avatarCrop.url);
  state.avatarCrop = null;
  $("#modal-root").innerHTML = "";
}

async function uploadCroppedAvatar(canvas) {
  const button = $('[data-modal="confirm"]');
  setButtonBusy(button, true, "上传中");
  canvas.toBlob(async (blob) => {
    if (!blob) {
      setButtonBusy(button, false);
      showToast("头像压缩失败", "error");
      return;
    }
    try {
      const form = new FormData();
      form.append("avatar", blob, "avatar.webp");
      const result = await api("/auth/avatar", { method: "POST", body: form });
      state.user = result.user;
      state.profileAvatarDraft = null;
      state.comments = {};
      state.myComments = null;
      syncUserChrome(state.user);
      closeAvatarCrop();
      state.profileEditing = false;
      await renderPublicProfilePage(state.user.user_id);
      showToast("头像已更新");
    } catch (error) {
      showToast(error.message, "error");
      setButtonBusy(button, false);
    }
  }, "image/webp", 0.86);
}

function textEditorModal(title, value, maxLength, confirmLabel) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="editor-modal-title"><div class="modal-heading"><h2 id="editor-modal-title">${escapeHtml(title)}</h2><button class="icon-button" type="button" data-modal="cancel" aria-label="取消">×</button></div><textarea id="editor-modal-input" maxlength="${maxLength}" rows="7">${escapeHtml(value || "")}</textarea><div class="modal-actions"><button class="button button-secondary" type="button" data-modal="cancel">取消</button><button class="button button-primary" type="button" data-modal="confirm">${escapeHtml(confirmLabel)}</button></div></section></div>`;
    const finish = (result) => { root.innerHTML = ""; resolve(result); };
    $$('[data-modal="cancel"]', root).forEach((button) => button.addEventListener("click", () => finish(null)));
    $('[data-modal="confirm"]', root).addEventListener("click", () => finish(String($("#editor-modal-input", root).value || "").trim()));
    $(".modal-backdrop", root).addEventListener("click", (event) => { if (event.target.classList.contains("modal-backdrop")) finish(null); });
    $("#editor-modal-input", root).focus();
  });
}

function confirmModal(title, message, confirmLabel = "确认") {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><h2 id="modal-title">${escapeHtml(title)}</h2><p>${escapeHtml(message)}</p><div class="modal-actions"><button class="button button-secondary" type="button" data-modal="cancel">取消</button><button class="button button-primary" type="button" data-modal="confirm">${escapeHtml(confirmLabel)}</button></div></section></div>`;
    const finish = (value) => { root.innerHTML = ""; resolve(value); };
    $('[data-modal="cancel"]', root).addEventListener("click", () => finish(false));
    $('[data-modal="confirm"]', root).addEventListener("click", () => finish(true));
    $(".modal-backdrop", root).addEventListener("click", (event) => {
      if (event.target.classList.contains("modal-backdrop")) finish(false);
    });
  });
}

function passwordModal(username) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="password-modal-title"><h2 id="password-modal-title">重置密码</h2><p>为 ${escapeHtml(username)} 设置新密码，保存后会撤销该账号的既有登录会话。</p><form id="password-reset-form" class="stack-form"><label class="field"><span>新密码</span><input name="password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required placeholder="至少 10 位，包含三类字符"></label><div class="modal-actions"><button class="button button-secondary" type="button" data-modal="cancel">取消</button><button class="button button-primary" type="submit">确认重置</button></div></form></section></div>`;
    const finish = (value) => { root.innerHTML = ""; resolve(value); };
    $('[data-modal="cancel"]', root).addEventListener("click", () => finish(null));
    $("#password-reset-form", root).addEventListener("submit", (event) => {
      event.preventDefault();
      finish(event.target.elements.password.value);
    });
    $(".modal-backdrop", root).addEventListener("click", (event) => {
      if (event.target.classList.contains("modal-backdrop")) finish(null);
    });
    $("input", root).focus();
  });
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 3600);
}

function fileTitle(path) {
  const name = String(path || "").replaceAll("\\", "/").split("/").pop() || "题库文件";
  return name.replace(/\.(xlsx|docx)$/i, "");
}

function displaySourcePath(path) {
  return String(path || "").replace(/\.xlsx$/i, "");
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

function formatDate(value) {
  if (!value) return "从未登录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

function searchHighlightText(value) {
  const text = String(value ?? "");
  if (state.searchMode !== "keyword" || !state.lastSearchQuery.trim()) return escapeHtml(text);
  const query = state.lastSearchQuery.trim();
  const terms = [...new Set([
    query,
    ...(query.match(/[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9]+/g) || []),
  ])]
    .map((term) => term.trim())
    .filter((term) => term.length > 0)
    .sort((left, right) => right.length - left.length);
  if (!terms.length) return escapeHtml(text);
  const pattern = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const matcher = new RegExp(pattern, "gi");
  let output = "";
  let cursor = 0;
  for (const match of text.matchAll(matcher)) {
    const start = match.index ?? cursor;
    output += escapeHtml(text.slice(cursor, start));
    output += `<mark class="search-hit">${escapeHtml(match[0])}</mark>`;
    cursor = start + match[0].length;
  }
  return output + escapeHtml(text.slice(cursor));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function savePreferences() {
  try {
    localStorage.setItem(PREFERENCE_KEY, JSON.stringify({
      searchMode: state.searchMode,
      searchFields: state.searchFields,
      searchQuestionTypes: state.searchQuestionTypes,
      searchSolutionVisibility: state.searchSolutionVisibility,
      searchSettingsExpanded: state.searchSettingsExpanded,
      libraryViewMode: state.libraryViewMode,
      theme: state.theme,
    }));
  } catch (_) {
    // 浏览器禁用本地存储时仍可在当前会话使用设置。
  }
}

try {
  const preferences = JSON.parse(localStorage.getItem(PREFERENCE_KEY) || "{}");
  if (preferences.searchMode) state.searchMode = preferences.searchMode;
  if (["light", "dark"].includes(preferences.theme)) state.theme = preferences.theme;
  if (["folders", "guided"].includes(preferences.libraryViewMode)) state.libraryViewMode = preferences.libraryViewMode;
  if (Array.isArray(preferences.searchQuestionTypes)) {
    state.searchQuestionTypes = [...new Set(preferences.searchQuestionTypes.map((value) => String(value || "").trim()).filter(Boolean))];
  } else if (typeof preferences.searchQuestionType === "string" && preferences.searchQuestionType) {
    state.searchQuestionTypes = [preferences.searchQuestionType];
  }
  if (preferences.searchFields && typeof preferences.searchFields === "object") {
    for (const field of ["stem", "answer", "point", "explanation"]) {
      if (typeof preferences.searchFields[field] === "boolean") {
        state.searchFields[field] = preferences.searchFields[field];
      }
    }
    if (!Object.values(state.searchFields).some(Boolean)) {
      state.searchFields.stem = true;
    }
    if (!state.searchFields.stem && state.searchMode !== "keyword") {
      state.searchMode = "keyword";
    }
  }
  if (preferences.searchSolutionVisibility && typeof preferences.searchSolutionVisibility === "object") {
    for (const field of ["stem", "answer", "point", "explanation"]) {
      if (typeof preferences.searchSolutionVisibility[field] === "boolean") {
        state.searchSolutionVisibility[field] = preferences.searchSolutionVisibility[field];
      }
    }
  }
  if (typeof preferences.searchSettingsExpanded === "boolean") {
    state.searchSettingsExpanded = preferences.searchSettingsExpanded;
  }
} catch (_) {
  localStorage.removeItem(PREFERENCE_KEY);
}
