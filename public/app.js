const API = "/api/v2";
const PREFERENCE_KEY = "medical-v2-preferences";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  publicSettings: null,
  preferences: loadPreferences(),
  libraryPath: "",
  outline: null,
  outlineStatus: "all",
  session: null,
  practiceIndex: 0,
  selections: new Map(),
  results: new Map(),
  questionStartedAt: 0,
  adminToken: "",
};

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindSearch();
  bindLibrary();
  bindPractice();
  bindWrongBook();
  bindSettings();
  applyPreferences();
  initialize();
});

async function initialize() {
  try {
    const [health, stats, settings] = await Promise.all([
      fetchJSON("/api/health"),
      fetchJSON(`${API}/stats`),
      fetchJSON(`${API}/settings`),
    ]);
    state.publicSettings = settings;
    const status = $("#catalogStatus");
    status.classList.add("healthy");
    status.innerHTML = `<span class="status-dot"></span><span>${formatNumber(health.total_questions)} 题已就绪</span>`;
    const metrics = [
      [stats.counts.questions, "规范题目"],
      [stats.counts.collections, "题库文件"],
      [stats.counts.question_occurrences, "来源记录"],
    ];
    $("#metricRow").innerHTML = metrics
      .map(([value, label]) => `<div><strong>${formatNumber(value)}</strong><span>${label}</span></div>`)
      .join("");
    renderSettingsStatus();
    applySemanticAvailability();
  } catch (error) {
    const status = $("#catalogStatus");
    status.classList.add("error");
    status.innerHTML = `<span class="status-dot"></span><span>题库不可用</span>`;
    toast(error.message, true);
  }
}

function bindNavigation() {
  $$(".mode-tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
}

function switchView(view) {
  $$(".mode-tab").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$('[data-view-panel]').forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (view === "library" && !state.outline && !$("#practiceWorkspace").hidden) return;
  if (view === "library" && !state.outline) loadLibrary(state.libraryPath);
  if (view === "wrong") loadWrongQuestions();
  if (view === "settings") refreshPublicSettings();
}

function bindSearch() {
  $("#searchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await runSearch();
  });
}

async function runSearch() {
  const query = $("#searchInput").value.trim();
  if (!query) {
    toast("请输入题干、题号或关键词", true);
    $("#searchInput").focus();
    return;
  }
  const container = $("#searchResults");
  container.className = "card-list";
  container.innerHTML = loadingMarkup("正在检索结构化题库");
  $("#searchHeading").textContent = "搜索中";
  $("#searchMeta").textContent = "";
  const started = performance.now();
  try {
    const payload = await fetchJSON(`${API}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        mode: $("#searchMode").value,
        limit: Number(state.preferences.resultLimit || 10),
        include_solution: $("#includeSolution").checked,
        filters: {
          exam: $("#searchExam").value,
          question_type: $("#searchType").value,
        },
      }),
    });
    const elapsed = Math.round(performance.now() - started);
    $("#searchHeading").textContent = payload.count ? `找到 ${payload.count} 道相关题目` : "没有找到匹配题目";
    $("#searchMeta").textContent = `${searchModeLabel(payload.mode)} · ${elapsed} ms`;
    if (!payload.count) {
      container.className = "card-list empty-state";
      container.innerHTML = `<div class="empty-icon">∅</div><p>试试更完整的题干，或调整考试、题型与搜索方式。</p>`;
      return;
    }
    container.innerHTML = payload.results.map((question, index) => questionCard(question, index + 1)).join("");
  } catch (error) {
    $("#searchHeading").textContent = "搜索失败";
    container.className = "card-list empty-state";
    container.innerHTML = `<p>${escapeHTML(error.message)}</p>`;
  }
}

function bindLibrary() {
  $("#refreshLibrary").addEventListener("click", () => loadLibrary(state.libraryPath));
  $("#libraryBreadcrumbs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-library-path]");
    if (button) loadLibrary(button.dataset.libraryPath);
  });
  $("#libraryEntries").addEventListener("click", (event) => {
    const directory = event.target.closest("[data-directory-path]");
    if (directory) loadLibrary(directory.dataset.directoryPath);
    const collection = event.target.closest("[data-collection-id]");
    if (collection) selectCollection(collection.dataset.collectionId);
  });
  $("#backToLibrary").addEventListener("click", showLibraryBrowser);
  $("#continuePractice").addEventListener("click", continueCollectionPractice);
  $("#quickPractice").addEventListener("click", () => startCollectionPractice({ orderMode: "random", count: 20, startPosition: 1 }));
  $("#questionDirectory").addEventListener("click", (event) => {
    const button = event.target.closest("[data-collection-position]");
    if (button) startCollectionPractice({ startPosition: Number(button.dataset.collectionPosition), orderMode: "sequential" });
  });
  $$("[data-outline-status]").forEach((button) => {
    button.addEventListener("click", () => {
      state.outlineStatus = button.dataset.outlineStatus;
      $$("[data-outline-status]").forEach((item) => item.classList.toggle("active", item === button));
      renderQuestionDirectory();
    });
  });
  $("#outlineType").addEventListener("change", renderQuestionDirectory);
}

async function loadLibrary(parent = "") {
  state.libraryPath = parent || "";
  state.outline = null;
  $("#libraryBrowser").hidden = false;
  $("#collectionView").hidden = true;
  $("#practiceWorkspace").hidden = true;
  const container = $("#libraryEntries");
  container.innerHTML = loadingMarkup("正在读取原始题库目录");
  try {
    const payload = await fetchJSON(`${API}/library?parent=${encodeURIComponent(state.libraryPath)}`);
    renderBreadcrumbs(payload.breadcrumbs);
    const rows = [
      ...payload.directories.map((item) => libraryEntry(item)),
      ...payload.collections.map((item) => libraryEntry(item)),
    ];
    container.innerHTML = rows.length ? rows.join("") : `<div class="empty-state"><p>此目录没有题库文件。</p></div>`;
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><p>${escapeHTML(error.message)}</p></div>`;
  }
}

function renderBreadcrumbs(items) {
  $("#libraryBreadcrumbs").innerHTML = items.map((item, index) => `
    ${index ? '<span class="breadcrumb-separator">/</span>' : ""}
    <button class="breadcrumb-button" type="button" data-library-path="${escapeHTML(item.path)}">${escapeHTML(item.name)}</button>`).join("");
}

function libraryEntry(item) {
  if (item.kind === "directory") {
    return `<button class="library-entry folder" type="button" data-directory-path="${escapeHTML(item.path)}">
      <span class="entry-icon" aria-hidden="true">▰</span>
      <span class="entry-copy"><strong>${escapeHTML(item.name)}</strong><span>${formatNumber(item.collection_count)} 个文件</span></span>
      <span class="entry-meta">${formatNumber(item.question_count)} 题 ›</span>
    </button>`;
  }
  return `<button class="library-entry file" type="button" data-collection-id="${escapeHTML(item.collection_id)}">
    <span class="entry-icon" aria-hidden="true">▤</span>
    <span class="entry-copy"><strong>${escapeHTML(item.name)}</strong><span>${escapeHTML(item.extension)} · ${formatNumber(item.unique_question_count)} 道规范题</span></span>
    <span class="entry-meta">${formatNumber(item.question_count)} 题 ›</span>
  </button>`;
}

async function selectCollection(collectionId) {
  $("#libraryBrowser").hidden = true;
  $("#collectionView").hidden = false;
  $("#practiceWorkspace").hidden = true;
  $("#questionDirectory").innerHTML = loadingMarkup("正在读取题号目录");
  try {
    state.outline = await fetchJSON(`${API}/practice/collections/${encodeURIComponent(collectionId)}/outline`);
    const collection = state.outline.collection;
    $("#collectionTitle").textContent = collection.title || collection.relative_path.split("/").at(-1);
    $("#collectionPath").textContent = collection.relative_path;
    const types = [...new Set(state.outline.items.map((item) => item.raw_type || item.question_type).filter(Boolean))].sort();
    $("#outlineType").innerHTML = `<option value="">全部题型</option>${types.map((type) => `<option value="${escapeHTML(type)}">${escapeHTML(type)}</option>`).join("")}`;
    $("#continuePractice").textContent = state.outline.active_session ? "继续上次练习" : "从未做题开始";
    renderQuestionDirectory();
  } catch (error) {
    $("#questionDirectory").innerHTML = `<div class="empty-state"><p>${escapeHTML(error.message)}</p></div>`;
  }
}

function showLibraryBrowser() {
  state.outline = null;
  $("#collectionView").hidden = true;
  $("#practiceWorkspace").hidden = true;
  $("#libraryBrowser").hidden = false;
  loadLibrary(state.libraryPath);
}

function renderQuestionDirectory() {
  if (!state.outline) return;
  const selectedType = $("#outlineType").value;
  const items = state.outline.items.filter((item) => {
    const statusMatch = state.outlineStatus === "all" || item.status === state.outlineStatus;
    const typeMatch = !selectedType || (item.raw_type || item.question_type) === selectedType;
    return statusMatch && typeMatch;
  });
  $("#questionDirectory").innerHTML = items.length
    ? items.map((item) => `<button class="question-number ${escapeHTML(item.status)}" type="button"
        data-collection-position="${item.collection_position}" title="${escapeHTML(item.source_code || item.stem.slice(0, 40))}">${item.collection_position}</button>`).join("")
    : `<div class="empty-state"><p>当前筛选没有题目。</p></div>`;
  const summary = state.outline.summary;
  const accuracy = summary.answered ? Math.round(summary.correct / summary.answered * 100) : 0;
  $("#outlineSummary").innerHTML = `
    <span>共 <strong>${summary.total}</strong> 题</span>
    <span>对 <strong>${summary.correct}</strong></span>
    <span>错 <strong>${summary.wrong}</strong></span>
    <span>未做 <strong>${summary.total - summary.answered}</strong></span>
    <span>正确率 <strong>${accuracy}%</strong></span>`;
}

async function continueCollectionPractice() {
  if (!state.outline) return;
  if (state.outline.active_session) {
    try {
      const session = await fetchJSON(`${API}/practice/sessions/${state.outline.active_session.session_id}`);
      activateSession(session);
      const firstUnanswered = session.items.findIndex((item) => !item.submission);
      state.practiceIndex = firstUnanswered >= 0 ? firstUnanswered : 0;
      renderPracticeQuestion();
      return;
    } catch (error) {
      toast(error.message, true);
    }
  }
  const first = state.outline.items.find((item) => item.status === "unanswered") || state.outline.items[0];
  if (first) startCollectionPractice({ startPosition: first.collection_position, orderMode: "sequential" });
}

async function startCollectionPractice({ startPosition = 1, orderMode = "sequential", count = null }) {
  if (!state.outline) return;
  const buttons = [$("#continuePractice"), $("#quickPractice")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const session = await fetchJSON(`${API}/practice/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        collection_id: state.outline.collection.collection_id,
        start_position: startPosition,
        order_mode: orderMode,
        count,
        mode: orderMode === "random" ? "quick" : "practice",
      }),
    });
    activateSession(session);
  } catch (error) {
    toast(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function bindPractice() {
  $("#exitPractice").addEventListener("click", exitPractice);
  $("#practiceCard").addEventListener("click", handlePracticeClick);
  $("#practiceCard").addEventListener("input", handlePracticeInput);
  $("#sessionGrid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-session-position]");
    if (!button || !state.session) return;
    state.practiceIndex = Number(button.dataset.sessionPosition) - 1;
    renderPracticeQuestion();
  });
}

function activateSession(session) {
  state.session = session;
  state.practiceIndex = 0;
  state.selections.clear();
  state.results.clear();
  session.items.forEach((item) => {
    if (item.submission) state.results.set(item.position, item.submission);
  });
  $("#libraryBrowser").hidden = true;
  $("#collectionView").hidden = true;
  $("#practiceWorkspace").hidden = false;
  renderPracticeQuestion();
}

function renderPracticeQuestion() {
  const session = state.session;
  if (!session || !session.items.length) return;
  const item = session.items[state.practiceIndex];
  const question = item.question;
  const result = state.results.get(item.position) || item.submission;
  const selected = state.selections.get(item.position) || new Set(item.submission?.answer ? [item.submission.answer] : []);
  const progress = ((state.practiceIndex + 1) / session.items.length) * 100;
  $("#practiceProgress").textContent = `第 ${state.practiceIndex + 1} / ${session.items.length} 题${item.collection_position ? ` · 原题号 ${item.collection_position}` : ""}`;
  $("#practiceProgressBar").style.width = `${progress}%`;
  state.questionStartedAt = performance.now();

  const choice = question.options.length > 0;
  const multiple = question.question_type === "多选题";
  const selectedLabels = new Set([...selected].flatMap((value) => String(value).split("")));
  const options = choice
    ? `<div class="option-list">${question.options.map((option) => `
        <button class="option-row selectable ${selectedLabels.has(option.label) ? "selected" : ""}" type="button"
                data-option="${escapeHTML(option.label)}" ${result ? "disabled" : ""}>
          <span class="option-label">${escapeHTML(option.label)}</span><span>${escapeHTML(option.text)}</span>
        </button>`).join("")}</div>`
    : question.question_type === "简答题" || question.question_type === "论述题"
      ? `<textarea class="answer-input textarea" id="practiceTextAnswer" placeholder="输入你的答案" ${result ? "disabled" : ""}>${escapeHTML([...selected].join(""))}</textarea>`
      : `<input class="answer-input" id="practiceTextAnswer" value="${escapeHTML([...selected].join(""))}" placeholder="输入你的答案" ${result ? "disabled" : ""}>`;

  const hasOtherUnanswered = session.items.some((candidate) =>
    candidate.position !== item.position
      && !(state.results.get(candidate.position) || candidate.submission));
  $("#practiceCard").innerHTML = `
    ${questionTopline(question, state.practiceIndex + 1)}
    <h3 class="question-stem">${sourceCode(question)}${escapeHTML(question.stem)}</h3>
    ${multiple && !result ? `<p class="source-line">多选题：可选择多个选项</p>` : ""}
    ${options}
    ${result ? resultMarkup(result) : ""}
    <div class="practice-actions">
      <button class="secondary-button" type="button" data-action="previous" ${state.practiceIndex === 0 ? "disabled" : ""}>上一题</button>
      ${result
        ? `<button class="primary-button" type="button" data-action="next">${state.practiceIndex === session.items.length - 1 ? (hasOtherUnanswered ? "继续未答题" : "查看结果") : "下一题"}</button>`
        : `<button class="primary-button" type="button" data-action="submit">提交答案</button>`}
    </div>`;
  renderSessionGrid();
}

function renderSessionGrid() {
  if (!state.session) return;
  $("#sessionGrid").innerHTML = state.session.items.map((item, index) => {
    const result = state.results.get(item.position) || item.submission;
    const status = result ? (result.is_correct === true ? "correct" : result.is_correct === false ? "wrong" : "ungraded") : "unanswered";
    return `<button class="question-number ${status} ${index === state.practiceIndex ? "current" : ""}" type="button" data-session-position="${index + 1}">${item.collection_position || index + 1}</button>`;
  }).join("");
  const answered = state.session.items.filter((item) => state.results.has(item.position) || item.submission).length;
  $("#practiceScore").textContent = `${answered}/${state.session.items.length}`;
}

function handlePracticeInput(event) {
  if (event.target.id !== "practiceTextAnswer" || !state.session) return;
  const item = state.session.items[state.practiceIndex];
  state.selections.set(item.position, new Set([event.target.value]));
}

async function handlePracticeClick(event) {
  const option = event.target.closest("[data-option]");
  if (option && state.session) {
    const item = state.session.items[state.practiceIndex];
    const selected = new Set(state.selections.get(item.position) || []);
    const label = option.dataset.option;
    if (item.question.question_type === "多选题") {
      selected.has(label) ? selected.delete(label) : selected.add(label);
    } else {
      selected.clear(); selected.add(label);
    }
    state.selections.set(item.position, selected);
    renderPracticeQuestion();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "submit") await submitPracticeAnswer();
  if (action === "previous" && state.practiceIndex > 0) { state.practiceIndex -= 1; renderPracticeQuestion(); }
  if (action === "next") advancePractice();
}

async function submitPracticeAnswer() {
  const item = state.session.items[state.practiceIndex];
  const selected = [...(state.selections.get(item.position) || [])];
  const answer = item.question.options.length ? selected.sort().join("") : (selected[0] || "").trim();
  if (!answer) { toast("请先选择或填写答案", true); return; }
  const submit = $('[data-action="submit"]', $("#practiceCard"));
  submit.disabled = true;
  submit.textContent = "提交中…";
  try {
    const result = await fetchJSON(`${API}/practice/sessions/${state.session.session_id}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        position: item.position,
        answer,
        elapsed_ms: Math.max(0, Math.round(performance.now() - state.questionStartedAt)),
      }),
    });
    state.results.set(item.position, result);
    item.submission = result;
    renderPracticeQuestion();
  } catch (error) {
    submit.disabled = false;
    submit.textContent = "提交答案";
    toast(error.message, true);
  }
}

async function advancePractice() {
  if (state.practiceIndex < state.session.items.length - 1) {
    state.practiceIndex += 1; renderPracticeQuestion(); return;
  }
  const firstUnanswered = state.session.items.findIndex((item) =>
    !(state.results.get(item.position) || item.submission));
  if (firstUnanswered >= 0) {
    state.practiceIndex = firstUnanswered;
    renderPracticeQuestion();
    return;
  }
  try { state.session = await fetchJSON(`${API}/practice/sessions/${state.session.session_id}`); } catch (_) {}
  const summary = state.session.summary;
  $("#practiceCard").innerHTML = `
    <p class="eyebrow">SESSION COMPLETE</p><h2>本组练习完成</h2>
    <div class="metric-row"><div><strong>${summary.answered}</strong><span>已作答</span></div><div><strong>${summary.correct}</strong><span>正确</span></div><div><strong>${summary.wrong}</strong><span>错误</span></div></div>
    <div class="practice-actions"><button class="secondary-button" type="button" data-action="review">回看题目</button><button class="primary-button" type="button" data-action="directory">返回题号目录</button></div>`;
  $('[data-action="review"]', $("#practiceCard")).addEventListener("click", () => { state.practiceIndex = 0; renderPracticeQuestion(); }, { once: true });
  $('[data-action="directory"]', $("#practiceCard")).addEventListener("click", exitPractice, { once: true });
}

async function exitPractice() {
  const collectionId = state.session?.collection_id || state.outline?.collection?.collection_id;
  state.session = null;
  state.selections.clear(); state.results.clear();
  $("#practiceWorkspace").hidden = true;
  if (collectionId) await selectCollection(collectionId); else showLibraryBrowser();
}

function bindWrongBook() {
  $("#refreshWrong").addEventListener("click", loadWrongQuestions);
}

async function loadWrongQuestions() {
  const container = $("#wrongResults");
  container.className = "card-list";
  container.innerHTML = loadingMarkup("正在读取本地错题");
  try {
    const payload = await fetchJSON(`${API}/practice/wrong?limit=50`);
    if (!payload.count) {
      container.className = "card-list empty-state";
      container.innerHTML = `<div class="empty-icon">✓</div><p>还没有错题记录。</p>`;
      return;
    }
    container.innerHTML = payload.items.map((item, index) => {
      const card = questionCard(item.question, index + 1);
      const progress = `<div class="result-banner wrong">累计错误 ${item.progress.wrong_count} 次 · 最近答案 ${escapeHTML(item.progress.last_answer || "—")}</div>`;
      return card.replace("</article>", `${progress}</article>`);
    }).join("");
  } catch (error) {
    container.className = "card-list empty-state";
    container.innerHTML = `<p>${escapeHTML(error.message)}</p>`;
  }
}

function bindSettings() {
  $("#savePreferences").addEventListener("click", savePreferences);
  $("#unlockAdmin").addEventListener("click", unlockAdminSettings);
  $("#providerSettings").addEventListener("submit", saveServerSettings);
  $$('[data-test-provider]').forEach((button) => {
    button.addEventListener("click", () => testProvider(button.dataset.testProvider));
  });
}

function applyPreferences() {
  $("#preferenceSearchMode").value = state.preferences.searchMode;
  $("#preferenceResultLimit").value = String(state.preferences.resultLimit);
  $("#preferenceShowSolution").checked = state.preferences.showSolution;
  $("#searchMode").value = state.preferences.searchMode;
  $("#includeSolution").checked = state.preferences.showSolution;
}

function loadPreferences() {
  const defaults = { searchMode: "keyword", resultLimit: 10, showSolution: true };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(PREFERENCE_KEY) || "{}") };
  } catch (_) { return defaults; }
}

function savePreferences() {
  state.preferences = {
    searchMode: $("#preferenceSearchMode").value,
    resultLimit: Number($("#preferenceResultLimit").value),
    showSolution: $("#preferenceShowSolution").checked,
  };
  localStorage.setItem(PREFERENCE_KEY, JSON.stringify(state.preferences));
  applyPreferences();
  applySemanticAvailability();
  toast("浏览器偏好已保存");
}

async function refreshPublicSettings() {
  try {
    state.publicSettings = await fetchJSON(`${API}/settings`);
    renderSettingsStatus();
    applySemanticAvailability();
  } catch (error) { $("#settingsStatus").textContent = error.message; }
}

function renderSettingsStatus() {
  if (!state.publicSettings) return;
  const vector = state.publicSettings.vector_index || {};
  const embedding = state.publicSettings.embedding || {};
  const pieces = [
    `嵌入模型：${embedding.model || "未配置"}`,
    `密钥：${embedding.api_key_configured ? "已配置" : "未配置"}`,
    `向量索引：${vector.status === "ready" ? `${formatNumber(vector.count)} 题 / ${vector.dimensions} 维` : "未就绪"}`,
    `语义搜索：${state.publicSettings.semantic_enabled ? "已启用" : "未启用"}`,
  ];
  $("#settingsStatus").textContent = pieces.join(" · ");
}

function applySemanticAvailability() {
  if (!state.publicSettings) return;
  const ready = state.publicSettings.semantic_enabled
    && state.publicSettings.embedding?.api_key_configured
    && state.publicSettings.vector_index?.status === "ready";
  for (const value of ["semantic", "hybrid"]) {
    const option = $(`#searchMode option[value="${value}"]`);
    option.disabled = !ready;
  }
  const notice = $("#semanticNotice");
  notice.hidden = ready;
  notice.textContent = ready ? "" : "语义搜索尚未就绪；关键词与题号搜索仍完全可用。请由管理员配置嵌入模型并激活匹配的向量索引。";
  if (!ready && $("#searchMode").value !== "keyword") $("#searchMode").value = "keyword";
}

async function unlockAdminSettings() {
  const token = $("#adminToken").value;
  if (!token) { toast("请输入管理员口令", true); return; }
  try {
    const payload = await fetchJSON(`${API}/admin/settings`, { headers: { "X-Admin-Token": token } });
    state.adminToken = token;
    populateAdminSettings(payload);
    $("#providerSettings").hidden = false;
    $("#adminToken").value = "";
    toast("服务器设置已解锁");
  } catch (error) { toast(error.message, true); }
}

function populateAdminSettings(payload) {
  $("#embeddingApiUrl").value = payload.embedding.api_url;
  $("#embeddingModel").value = payload.embedding.model;
  $("#embeddingDimensions").value = String(payload.embedding.dimensions || 1024);
  $("#embeddingApiKey").value = "";
  $("#embeddingKeyState").textContent = payload.embedding.api_key_configured ? `现有密钥：${payload.embedding.api_key_masked}` : "尚未配置密钥";
  $("#llmApiUrl").value = payload.llm.api_url;
  $("#llmModel").value = payload.llm.model;
  $("#llmApiKey").value = "";
  $("#llmKeyState").textContent = payload.llm.api_key_configured ? `现有密钥：${payload.llm.api_key_masked}` : "尚未配置密钥";
  $("#semanticEnabled").checked = payload.semantic_enabled;
}

async function saveServerSettings(event) {
  event.preventDefault();
  if (!state.adminToken) { toast("请先解锁管理员设置", true); return; }
  const payload = {
    app: {
      semantic_enabled: $("#semanticEnabled").checked,
      default_search_mode: $("#preferenceSearchMode").value,
      search_result_limit: Number($("#preferenceResultLimit").value),
    },
    providers: {
      embedding: {
        api_url: $("#embeddingApiUrl").value.trim(),
        model: $("#embeddingModel").value.trim(),
        dimensions: Number($("#embeddingDimensions").value),
        api_key: $("#embeddingApiKey").value.trim(),
      },
      llm: {
        api_url: $("#llmApiUrl").value.trim(),
        model: $("#llmModel").value.trim(),
        api_key: $("#llmApiKey").value.trim(),
      },
    },
  };
  try {
    const updated = await fetchJSON(`${API}/admin/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Admin-Token": state.adminToken },
      body: JSON.stringify(payload),
    });
    populateAdminSettings(updated);
    await refreshPublicSettings();
    toast("服务器设置已保存");
  } catch (error) { toast(error.message, true); }
}

async function testProvider(provider) {
  if (!state.adminToken) { toast("请先解锁管理员设置", true); return; }
  const button = $(`[data-test-provider="${provider}"]`);
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "测试中…";
  try {
    const result = await fetchJSON(`${API}/admin/settings/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": state.adminToken },
      body: JSON.stringify({ provider }),
    });
    toast(`${provider === "embedding" ? "嵌入" : "大模型"}连接成功${result.dimensions ? `，${result.dimensions} 维` : ""}`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = previous; }
}

function questionCard(question, index) {
  const options = question.options?.length
    ? `<div class="option-list">${question.options.map((option) => `<div class="option-row"><span class="option-label">${escapeHTML(option.label)}</span><span>${escapeHTML(option.text)}</span></div>`).join("")}</div>` : "";
  const solution = "answer" in question
    ? `<div class="solution"><div class="solution-block answer"><strong>答案</strong><p>${escapeHTML(question.answer || "暂无")}</p></div>${question.point ? `<div class="solution-block"><strong>考点</strong><p>${escapeHTML(question.point)}</p></div>` : ""}${question.explanation ? `<div class="solution-block"><strong>解析</strong><p>${escapeHTML(question.explanation)}</p></div>` : ""}</div>` : "";
  const source = question.sources?.[0]?.relative_path || "";
  return `<article class="question-card">${questionTopline(question, index)}<h3 class="question-stem">${sourceCode(question)}${escapeHTML(question.stem)}</h3>${options}${solution}${source ? `<div class="source-line">来源：${escapeHTML(source)}${question.source_count > 1 ? ` · 共 ${question.source_count} 个来源` : ""}</div>` : ""}</article>`;
}

function questionTopline(question, index) {
  const labels = { exact: "精确匹配", source_code: "题号匹配", semantic: "语义匹配", hybrid: "混合匹配", fts: "全文匹配", short: "短词匹配" };
  const score = question.match_kind && !["id", "sample", "collection", "occurrence"].includes(question.match_kind)
    ? `<span class="match-score">${labels[question.match_kind] || "相关匹配"}</span>` : "";
  return `<div class="question-topline"><div class="tag-row"><span class="tag">${escapeHTML(question.question_type)}</span><span class="tag muted">#${index}</span></div>${score}</div>`;
}

function sourceCode(question) {
  return question.source_code ? `<span class="source-code">${escapeHTML(question.source_code)}</span>` : "";
}

function resultMarkup(result) {
  const kind = result.is_correct === true ? "correct" : result.is_correct === false ? "wrong" : "ungraded";
  const title = result.is_correct === true ? "回答正确" : result.is_correct === false ? "回答错误" : "已提交，请自行核对";
  return `<div class="result-banner ${kind}">${title} · 正确答案：${escapeHTML(result.correct_answer || "暂无")}</div><div class="solution">${result.point ? `<div class="solution-block"><strong>考点</strong><p>${escapeHTML(result.point)}</p></div>` : ""}${result.explanation ? `<div class="solution-block"><strong>解析</strong><p>${escapeHTML(result.explanation)}</p></div>` : ""}</div>`;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function loadingMarkup(text) { return `<div class="loading-card"><div class="spinner"></div><p>${escapeHTML(text)}</p></div>`; }

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { element.hidden = true; }, 3400);
}

function searchModeLabel(mode) { return ({ keyword: "关键词", semantic: "语义", hybrid: "混合" })[mode] || mode; }
function formatNumber(value) { return new Intl.NumberFormat("zh-CN").format(Number(value || 0)); }
function escapeHTML(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
