const API = "/api/v2";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  session: null,
  practiceIndex: 0,
  selections: new Map(),
  results: new Map(),
};

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindSearch();
  bindPractice();
  bindWrongBook();
  initialize();
});

async function initialize() {
  try {
    const [health, stats] = await Promise.all([
      fetchJSON(`${API}/health`),
      fetchJSON(`${API}/stats`),
    ]);
    const status = $("#catalogStatus");
    status.classList.add("healthy");
    status.innerHTML = `<span class="status-dot"></span><span>${formatNumber(health.total_questions)} 题已就绪</span>`;
    const metrics = [
      [stats.counts.questions, "规范题目"],
      [stats.counts.collections, "题集"],
      [stats.counts.question_occurrences, "来源记录"],
    ];
    $("#metricRow").innerHTML = metrics
      .map(([value, label]) => `<div><strong>${formatNumber(value)}</strong><span>${label}</span></div>`)
      .join("");
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
  if (view === "wrong") loadWrongQuestions();
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
    toast("请输入题干或关键词", true);
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
        limit: 10,
        include_solution: $("#includeSolution").checked,
        filters: {
          exam: $("#searchExam").value,
          question_type: $("#searchType").value,
        },
      }),
    });
    const elapsed = Math.round(performance.now() - started);
    $("#searchHeading").textContent = payload.count ? `找到 ${payload.count} 道相关题目` : "没有找到匹配题目";
    $("#searchMeta").textContent = `${elapsed} ms`;
    if (!payload.count) {
      container.className = "card-list empty-state";
      container.innerHTML = `<div class="empty-icon">∅</div><p>试试更完整的题干，或调整考试与题型筛选。</p>`;
      return;
    }
    container.innerHTML = payload.results.map((question, index) => questionCard(question, index + 1)).join("");
  } catch (error) {
    $("#searchHeading").textContent = "搜索失败";
    container.className = "card-list empty-state";
    container.innerHTML = `<p>${escapeHTML(error.message)}</p>`;
  }
}

function bindPractice() {
  $("#startPractice").addEventListener("click", startPractice);
  $("#newPractice").addEventListener("click", resetPractice);
  $("#practiceCard").addEventListener("click", handlePracticeClick);
  $("#practiceCard").addEventListener("input", handlePracticeInput);
}

async function startPractice() {
  const button = $("#startPractice");
  button.disabled = true;
  button.textContent = "正在组题…";
  const yearValue = $("#practiceYear").value.trim();
  const filters = {
    exam: $("#practiceExam").value,
    question_type: $("#practiceType").value,
  };
  if (yearValue) filters.year = Number(yearValue);
  try {
    const session = await fetchJSON(`${API}/practice/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: Number($("#practiceCount").value), filters }),
    });
    state.session = session;
    state.practiceIndex = 0;
    state.selections.clear();
    state.results.clear();
    $("#practiceSetup").hidden = true;
    $("#practiceWorkspace").hidden = false;
    renderPracticeQuestion();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "开始刷题";
  }
}

function renderPracticeQuestion() {
  const session = state.session;
  if (!session || !session.items.length) return;
  const item = session.items[state.practiceIndex];
  const question = item.question;
  const result = state.results.get(item.position);
  const selected = state.selections.get(item.position) || new Set();
  const progress = ((state.practiceIndex + 1) / session.items.length) * 100;
  $("#practiceProgress").textContent = `第 ${state.practiceIndex + 1} / ${session.items.length} 题`;
  $("#practiceProgressBar").style.width = `${progress}%`;

  const choice = question.options.length > 0;
  const multiple = question.question_type === "多选题";
  const options = choice
    ? `<div class="option-list">${question.options.map((option) => `
        <button class="option-row selectable ${selected.has(option.label) ? "selected" : ""}" type="button"
                data-option="${escapeHTML(option.label)}" ${result ? "disabled" : ""}>
          <span class="option-label">${escapeHTML(option.label)}</span>
          <span>${escapeHTML(option.text)}</span>
        </button>`).join("")}</div>`
    : question.question_type === "简答题"
      ? `<textarea class="answer-input textarea" id="practiceTextAnswer" placeholder="输入你的答案" ${result ? "disabled" : ""}>${escapeHTML([...selected].join(""))}</textarea>`
      : `<input class="answer-input" id="practiceTextAnswer" value="${escapeHTML([...selected].join(""))}" placeholder="输入你的答案" ${result ? "disabled" : ""}>`;

  $("#practiceCard").innerHTML = `
    ${questionTopline(question, state.practiceIndex + 1)}
    <h3 class="question-stem">${sourceCode(question)}${escapeHTML(question.stem)}</h3>
    ${multiple && !result ? `<p class="source-line">多选题：可选择多个选项</p>` : ""}
    ${options}
    ${result ? resultMarkup(result) : ""}
    <div class="practice-actions">
      <button class="secondary-button" type="button" data-action="previous" ${state.practiceIndex === 0 ? "disabled" : ""}>上一题</button>
      ${result
        ? `<button class="primary-button" type="button" data-action="next">${state.practiceIndex === session.items.length - 1 ? "查看结果" : "下一题"}</button>`
        : `<button class="primary-button" type="button" data-action="submit">提交答案</button>`}
    </div>`;
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
      selected.clear();
      selected.add(label);
    }
    state.selections.set(item.position, selected);
    renderPracticeQuestion();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "submit") await submitPracticeAnswer();
  if (action === "previous" && state.practiceIndex > 0) {
    state.practiceIndex -= 1;
    renderPracticeQuestion();
  }
  if (action === "next") advancePractice();
}

async function submitPracticeAnswer() {
  const item = state.session.items[state.practiceIndex];
  const selected = [...(state.selections.get(item.position) || [])];
  const answer = item.question.options.length ? selected.sort().join("") : (selected[0] || "").trim();
  if (!answer) {
    toast("请先选择或填写答案", true);
    return;
  }
  const submit = $('[data-action="submit"]', $("#practiceCard"));
  submit.disabled = true;
  submit.textContent = "提交中…";
  try {
    const result = await fetchJSON(`${API}/practice/sessions/${state.session.session_id}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position: item.position, answer }),
    });
    state.results.set(item.position, result);
    renderPracticeQuestion();
  } catch (error) {
    submit.disabled = false;
    submit.textContent = "提交答案";
    toast(error.message, true);
  }
}

async function advancePractice() {
  if (state.practiceIndex < state.session.items.length - 1) {
    state.practiceIndex += 1;
    renderPracticeQuestion();
    return;
  }
  try {
    state.session = await fetchJSON(`${API}/practice/sessions/${state.session.session_id}`);
  } catch (_) {}
  const summary = state.session.summary;
  $("#practiceCard").innerHTML = `
    <p class="eyebrow">SESSION COMPLETE</p>
    <h2>本组练习完成</h2>
    <div class="metric-row">
      <div><strong>${summary.answered}</strong><span>已作答</span></div>
      <div><strong>${summary.correct}</strong><span>正确</span></div>
      <div><strong>${summary.wrong}</strong><span>错误</span></div>
    </div>
    <div class="practice-actions">
      <button class="secondary-button" type="button" data-action="previous">回看题目</button>
      <button class="primary-button" type="button" data-action="restart">再来一组</button>
    </div>`;
  const restart = $('[data-action="restart"]', $("#practiceCard"));
  restart.addEventListener("click", resetPractice, { once: true });
}

function resetPractice() {
  state.session = null;
  state.practiceIndex = 0;
  state.selections.clear();
  state.results.clear();
  $("#practiceWorkspace").hidden = true;
  $("#practiceSetup").hidden = false;
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

function questionCard(question, index) {
  const options = question.options?.length
    ? `<div class="option-list">${question.options.map((option) => `
        <div class="option-row"><span class="option-label">${escapeHTML(option.label)}</span><span>${escapeHTML(option.text)}</span></div>`).join("")}</div>`
    : "";
  const solution = "answer" in question
    ? `<div class="solution">
        <div class="solution-block answer"><strong>答案</strong><p>${escapeHTML(question.answer || "暂无")}</p></div>
        ${question.point ? `<div class="solution-block"><strong>考点</strong><p>${escapeHTML(question.point)}</p></div>` : ""}
        ${question.explanation ? `<div class="solution-block"><strong>解析</strong><p>${escapeHTML(question.explanation)}</p></div>` : ""}
       </div>`
    : "";
  const source = question.sources?.[0]?.relative_path || "";
  return `<article class="question-card">
    ${questionTopline(question, index)}
    <h3 class="question-stem">${sourceCode(question)}${escapeHTML(question.stem)}</h3>
    ${options}${solution}
    ${source ? `<div class="source-line">来源：${escapeHTML(source)}${question.source_count > 1 ? ` · 共 ${question.source_count} 个来源` : ""}</div>` : ""}
  </article>`;
}

function questionTopline(question, index) {
  const score = question.match_kind && question.match_kind !== "id" && question.match_kind !== "sample"
    ? `<span class="match-score">${question.match_kind === "exact"
      ? "精确匹配"
      : question.match_kind === "source_code" ? "题号匹配" : "全文匹配"}</span>`
    : "";
  return `<div class="question-topline">
    <div class="tag-row"><span class="tag">${escapeHTML(question.question_type)}</span><span class="tag muted">#${index}</span></div>
    ${score}
  </div>`;
}

function sourceCode(question) {
  return question.source_code ? `<span class="source-code">${escapeHTML(question.source_code)}</span>` : "";
}

function resultMarkup(result) {
  const kind = result.is_correct === true ? "correct" : result.is_correct === false ? "wrong" : "ungraded";
  const title = result.is_correct === true ? "回答正确" : result.is_correct === false ? "回答错误" : "已提交，请自行核对";
  return `<div class="result-banner ${kind}">${title} · 正确答案：${escapeHTML(result.correct_answer || "暂无")}</div>
    <div class="solution">
      ${result.point ? `<div class="solution-block"><strong>考点</strong><p>${escapeHTML(result.point)}</p></div>` : ""}
      ${result.explanation ? `<div class="solution-block"><strong>解析</strong><p>${escapeHTML(result.explanation)}</p></div>` : ""}
    </div>`;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function loadingMarkup(text) {
  return `<div class="loading-card"><div class="spinner"></div><p>${escapeHTML(text)}</p></div>`;
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { element.hidden = true; }, 3200);
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
