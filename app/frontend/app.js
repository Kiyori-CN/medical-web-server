/**
 * 医学搜题系统 — 前端逻辑 v1.0
 * 支持关键词搜索、语义搜索、混合搜索 + AI 辅助对话
 */

// ── API 基础地址 ──────────────────────────────────────────
const API_BASE = (() => {
  const { hostname } = window.location;
  const isDev = hostname === "localhost" || hostname === "127.0.0.1";
  return isDev ? "http://localhost:5001/api" : "/api";
})();

// ── DOM 元素缓存 ──────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const elements = {
  searchInput: $("searchInput"),
  searchBtn: $("searchBtn"),
  randomBtn: $("randomBtn"),
  clearBtn: $("clearBtn"),
  results: $("results"),
  loading: $("loading"),
  stats: $("stats"),
  showAnswer: $("showAnswer"),
  showAnalysis: $("showAnalysis"),
  questionType: $("questionType"),
  searchMode: $("searchMode"),
  searchModeHint: $("searchModeHint"),
  hintText: $("hintText"),
};

// ── 题型映射（前端 + 后端必须完全一致） ─────────────────
const TYPE_MAP = {
  A1型题: "单选题",
  A2型题: "单选题",
  A3型题: "单选题",
  A4型题: "单选题",
  "A3/A4型题": "单选题",
  B型题: "单选题",
  B1型题: "单选题",
  X型题: "多选题",
  单选题: "单选题",
  多选题: "多选题",
  判断题: "判断题",
  填空题: "填空题",
  简答题: "简答题",
  名词解释: "名词解释",
  论述题: "简答题",
};

// ── 搜索模式提示文本 ─────────────────────────────────────
const MODE_HINTS = {
  keyword: "关键词搜索：精确匹配题目中的关键词，速度最快",
  semantic: "语义搜索：理解语义进行匹配，适合描述性查询",
  hybrid: "混合搜索：关键词 + 语义融合，兼顾精确与语义理解",
};

// ── 状态 ──────────────────────────────────────────────────
let lastResults = [];
let lastIsRandom = false;

// ── 工具函数 ──────────────────────────────────────────────
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ── API 请求 ──────────────────────────────────────────────
let _searchAbort = null;

async function searchQuestions(query) {
  if (_searchAbort) _searchAbort.abort();
  _searchAbort = new AbortController();
  const signal = _searchAbort.signal;
  showLoading(true);
  try {
    const response = await fetch(`${API_BASE}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        top_k: 10,
        question_type: elements.questionType.value,
        search_mode: elements.searchMode.value,
      }),
      signal,
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `服务器错误 (${response.status})`);
    }

    const data = await response.json();
    lastResults = data.results || [];
    lastIsRandom = false;
    displayResults(lastResults, false);
    updateStats(data.stats, data.search_mode);
    // 更新 AI 预加载状态
    updateAiPreload();
  } catch (error) {
    if (error.name === "AbortError") return;
    lastResults = [];
    const mode = elements.searchMode.value;
    let msg = error.message;
    if (mode === "semantic" || mode === "hybrid") {
      msg += "（提示：可切换到「关键词搜索」模式继续使用）";
    }
    showError("搜索失败：" + msg);
  } finally {
    showLoading(false);
  }
}

async function getRandomQuestions() {
  showLoading(true);
  try {
    const qt = elements.questionType.value;
    const params = new URLSearchParams({ count: "5" });
    if (qt) params.set("question_type", qt);
    const response = await fetch(`${API_BASE}/random?${params}`);

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `服务器错误 (${response.status})`);
    }

    const data = await response.json();
    lastResults = data.results || [];
    lastIsRandom = true;
    displayResults(lastResults, true);
    elements.stats.innerHTML = "";
    // 更新 AI 预加载状态
    updateAiPreload();
  } catch (error) {
    lastResults = [];
    showError("获取题目失败：" + error.message);
  } finally {
    showLoading(false);
  }
}

// ── 结果渲染 ──────────────────────────────────────────────
function displayResults(results, isRandom = false) {
  if (!results || results.length === 0) {
    elements.results.innerHTML = `
            <div class="no-results">
                <span style="font-size: 2.5rem; display: block; margin-bottom: 12px;">😔</span>
                <p>未找到相关题目，试试其他关键词吧</p>
            </div>`;
    return;
  }

  elements.results.innerHTML = results
    .map((item, index) => {
      const question = parseQuestion(item.content, item.question_type);
      return createQuestionCard(
        question,
        index + 1,
        item.score,
        item.source,
        isRandom
      );
    })
    .join("");
}

// ── 题目解析（核心逻辑） ─────────────────────────────────
function parseQuestion(content, backendType) {
  if (!content) {
    return {
      type: backendType || "未知题型",
      content: "[题干缺失]",
      options: [],
      answer: "",
      reference: "",
      analysis: "",
    };
  }

  const lines = content.split("\n").filter((line) => line.trim());
  const type = TYPE_MAP[backendType] || backendType || "未知题型";
  const question = {
    type,
    content: "",
    options: [],
    answer: "",
    reference: "",
    analysis: "",
  };

  let currentSection = "content";
  let contentLines = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // 跳过文件分隔线
    if (/^---\s*开始文件/.test(trimmed) || /^---\s*$/.test(trimmed)) {
      continue;
    }

    // 匹配题号 + 题型标签，如 "1.[A1型题]" 或 "23. [X型题]" 或 "30.[A3/A4型题]"
    const headerMatch = trimmed.match(/^\d+\.\s*\[.*?\]\s*(.*)/);
    if (headerMatch) {
      const rest = headerMatch[1].trim();
      // 去掉题号后可能残留的年份编码如 "2005N43A "
      const cleaned = rest.replace(/^\d{4}[A-Z0-9][\w-]*\s+/, "").trim();
      if (cleaned) contentLines.push(cleaned);
      currentSection = "content";
      continue;
    }

    // 匹配答案行（支持全角/半角冒号）
    if (/^答案\s*[：:]/.test(trimmed)) {
      currentSection = "answer";
      question.answer = trimmed.replace(/^答案\s*[：:]/, "").trim();
      continue;
    }

    // 匹配考点还原
    if (/^考点还原\s*[：:]/.test(trimmed)) {
      currentSection = "reference";
      question.reference = trimmed.replace(/^考点还原\s*[：:]/, "").trim();
      continue;
    }

    // 匹配解析行
    if (/^解析\s*[：:]/.test(trimmed)) {
      currentSection = "analysis";
      question.analysis = trimmed.replace(/^解析\s*[：:]/, "").trim();
      continue;
    }

    // 匹配选项行 (A. B. C. D. E. ，支持全角句号)
    if (/^[A-E]\s*[\.．]\s*/.test(trimmed)) {
      question.options.push(trimmed);
      currentSection = "options";
      continue;
    }

    // 续行内容：追加到当前段落
    switch (currentSection) {
      case "content":
        contentLines.push(trimmed);
        break;
      case "answer":
        question.answer += "\n" + trimmed;
        break;
      case "reference":
        question.reference += "\n" + trimmed;
        break;
      case "analysis":
        question.analysis += "\n" + trimmed;
        break;
      case "options":
        if (question.options.length > 0) {
          question.options[question.options.length - 1] += " " + trimmed;
        }
        break;
    }
  }

  question.content = contentLines.join(" ").trim() || "[题干缺失]";

  // 清理多余换行
  question.answer = question.answer.trim();
  question.analysis = question.analysis.trim();
  question.reference = question.reference.trim();

  return question;
}

// ── 卡片构建 ──────────────────────────────────────────────
function createQuestionCard(q, index, score, source, isRandom) {
  const showAnswer = elements.showAnswer.checked;
  const showAnalysis = elements.showAnalysis.checked;

  // 分数显示
  const scorePercent = (score * 100).toFixed(1);
  const scoreHtml = isRandom
    ? ""
    : `<span class="question-score" title="匹配得分">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="vertical-align: -2px;">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
            </svg>
            ${scorePercent}%
           </span>`;

  // 来源文件名
  const sourceHtml = source
    ? `<span class="question-source" title="${escapeHtml(source)}">${escapeHtml(source)}</span>`
    : "";

  // 题型 CSS class
  const typeClass = `type-${q.type}`;

  // 选项 HTML
  const optionsHtml =
    q.options.length > 0
      ? `<div class="options">
            ${q.options
              .map((opt) => {
                const letterMatch = opt.match(/^([A-E])\s*[\.．]\s*(.*)/);
                if (letterMatch) {
                  return `<div class="option">
                        <span class="option-letter">${escapeHtml(letterMatch[1])}</span>
                        <span class="option-text">${escapeHtml(letterMatch[2])}</span>
                    </div>`;
                }
                return `<div class="option"><span class="option-text">${escapeHtml(opt)}</span></div>`;
              })
              .join("")}
           </div>`
      : "";

  // 答案区域
  let answerSectionHtml = "";
  const hasAnswer = showAnswer && q.answer;
  const hasAnalysis = showAnalysis && q.analysis;
  const hasReference = showAnalysis && q.reference;

  if (hasAnswer || hasAnalysis || hasReference) {
    answerSectionHtml = '<div class="answer-section">';

    if (hasAnswer) {
      answerSectionHtml += `
                <div class="answer-block answer">
                    <strong>✅ 答案</strong>
                    <div class="answer-content">${formatMultiline(q.answer)}</div>
                </div>`;
    }

    if (hasAnalysis) {
      answerSectionHtml += `
                <div class="answer-block analysis">
                    <strong>💡 解析</strong>
                    <div class="answer-content">${formatMultiline(q.analysis)}</div>
                </div>`;
    }

    if (hasReference) {
      answerSectionHtml += `
                <div class="answer-block reference">
                    <strong>📚 考点还原</strong>
                    <div class="answer-content">${formatMultiline(q.reference)}</div>
                </div>`;
    }

    answerSectionHtml += "</div>";
  }

  return `
        <div class="question-card">
            <div class="question-header">
                <div class="question-meta">
                    <span class="question-type ${typeClass}">${escapeHtml(q.type)}</span>
                    ${sourceHtml}
                </div>
                ${scoreHtml}
            </div>
            <div class="question-content">
                <span class="question-index">${index}</span>
                <span class="question-text">${escapeHtml(q.content)}</span>
            </div>
            ${optionsHtml}
            ${answerSectionHtml}
        </div>
    `;
}

/**
 * 将多行文本安全转换为 HTML，保留换行
 */
function formatMultiline(text) {
  if (!text) return "";
  return text
    .split("\n")
    .map((line) => escapeHtml(line))
    .join("<br>");
}

// ── 统计信息 ──────────────────────────────────────────────
function updateStats(stats, searchMode) {
  if (!stats) {
    elements.stats.innerHTML = "";
    return;
  }
  const modeLabel = {
    keyword: "关键词",
    semantic: "语义",
    hybrid: "混合",
  };
  const modeText = modeLabel[searchMode] || "";
  const modeTag = modeText
    ? `<span class="stats-mode">${modeText}</span>`
    : "";
  elements.stats.innerHTML = `
        <p>
            ${modeTag}
            找到 <strong>${stats.found}</strong> 条结果 · 耗时 <strong>${stats.time_ms}ms</strong> · 题库共 ${(stats.total_questions || 0).toLocaleString()} 题
        </p>
    `;
}

// ── UI 状态控制 ──────────────────────────────────────────
function showLoading(show) {
  elements.loading.style.display = show ? "flex" : "none";
  elements.results.style.display = show ? "none" : "flex";
  elements.searchBtn.disabled = show;
  elements.randomBtn.disabled = show;
}

function showError(message) {
  elements.results.innerHTML = `<div class="error-message">❌ ${escapeHtml(message)}</div>`;
}

function updateClearBtn() {
  elements.clearBtn.style.display = elements.searchInput.value ? "flex" : "none";
}

function updateSearchModeHint() {
  const mode = elements.searchMode.value;
  elements.hintText.textContent = MODE_HINTS[mode] || "";
}

// ── 快速搜索标签 ──────────────────────────────────────────
function quickSearch(keyword) {
  elements.searchInput.value = keyword;
  updateClearBtn();
  searchQuestions(keyword);
}

// ── 欢迎页面 ──────────────────────────────────────────────
function showWelcome() {
  const tags = [
    "高血压",
    "肺炎",
    "糖尿病",
    "骨折",
    "心力衰竭",
    "肝硬化",
    "贫血",
    "甲状腺",
  ];
  elements.results.innerHTML = `
        <div class="welcome-screen">
            <span class="welcome-icon">👋</span>
            <h2>欢迎使用医学搜题系统</h2>
            <p>输入关键词或描述进行搜索，或点击「随机练习」开始刷题</p>
            <div class="welcome-tags">
                ${tags.map((t) => `<span class="welcome-tag" data-keyword="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join("")}
            </div>
            <div class="welcome-features">
                <div class="feature-item">
                    <span class="feature-icon">🔍</span>
                    <span>关键词搜索</span>
                </div>
                <div class="feature-item">
                    <span class="feature-icon">🧠</span>
                    <span>语义搜索</span>
                </div>
                <div class="feature-item">
                    <span class="feature-icon">⚡</span>
                    <span>混合搜索</span>
                </div>
            </div>
            <p class="welcome-hint">题库包含 66,096 题，涵盖人体解剖学、生理学、病理学等多个学科</p>
        </div>
    `;

  // 绑定快速搜索标签的点击事件
  elements.results.querySelectorAll(".welcome-tag").forEach((tag) => {
    tag.addEventListener("click", () => {
      quickSearch(tag.dataset.keyword);
    });
  });
}

// ── 事件绑定 ──────────────────────────────────────────────

// 搜索按钮
elements.searchBtn.addEventListener("click", () => {
  const query = elements.searchInput.value.trim();
  if (!query) {
    elements.searchInput.focus();
    elements.searchInput.classList.add("shake");
    setTimeout(() => elements.searchInput.classList.remove("shake"), 500);
    return;
  }
  if (!elements.searchBtn.disabled) searchQuestions(query);
});

// 回车搜索
elements.searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    elements.searchBtn.click();
  }
});

// 输入框内容变化 → 显示/隐藏清除按钮
elements.searchInput.addEventListener("input", updateClearBtn);

// 清除按钮
elements.clearBtn.addEventListener("click", () => {
  elements.searchInput.value = "";
  updateClearBtn();
  elements.searchInput.focus();
});

// 随机练习
elements.randomBtn.addEventListener("click", getRandomQuestions);

// 显示/隐藏答案和解析 → 重新渲染当前结果
elements.showAnswer.addEventListener("change", () => {
  if (lastResults.length > 0) {
    displayResults(lastResults, lastIsRandom);
  }
});

elements.showAnalysis.addEventListener("change", () => {
  if (lastResults.length > 0) {
    displayResults(lastResults, lastIsRandom);
  }
});

// 搜索模式切换 → 更新提示 + 如果有查询词则重新搜索
elements.searchMode.addEventListener("change", () => {
  updateSearchModeHint();
  const query = elements.searchInput.value.trim();
  if (query) searchQuestions(query);
});

// 题型切换 → 如果有搜索词则重新搜索，如果有随机结果则重新随机
elements.questionType.addEventListener("change", () => {
  const query = elements.searchInput.value.trim();
  if (query) {
    searchQuestions(query);
  } else if (lastIsRandom && lastResults.length > 0) {
    getRandomQuestions();
  }
});

// ══════════════════════════════════════════════════════════
// AI 辅助对话模块（通过后端代理）
// ══════════════════════════════════════════════════════════

const AI_CONFIG = {
  maxInputLength: 2000,
  maxTokens: 4096,
  temperature: 0.7,
  systemPrompt:
    `你是一位资深医学教育专家，专注于中国医学执业资格考试（临床执业医师、执业助理医师、护士资格等）的题目解析与教学辅导。

你的核心能力：
- 精准识别题目考查的知识点和命题意图
- 系统性分析每个选项的对错原因
- 关联相关高频考点，帮助举一反三
- 用简洁清晰的语言解释复杂医学概念

回答规范：
1. **答案判定**：先明确给出正确答案
2. **解题思路**：阐述从题干到答案的推理路径
3. **选项辨析**：逐项说明正确/错误原因（重点解释易混淆项）
4. **考点提炼**：总结本题涉及的核心知识点，标注记忆要点
5. **拓展关联**：如有相关易混考点，简要提示

格式要求：使用 Markdown 格式，善用加粗、列表、分隔线提升可读性。回答语言为中文。`,
};

const aiElements = {
  bubble: $("aiBubble"),
  chat: $("aiChat"),
  close: $("aiClose"),
  newChat: $("aiNewChat"),
  messages: $("aiMessages"),
  input: $("aiInput"),
  send: $("aiSend"),
  charCount: $("aiCharCount"),
  includeQuestions: $("aiIncludeQuestions"),
  preload: $("aiPreload"),
  preloadClear: $("aiPreloadClear"),
};

// AI 对话状态
let aiChatHistory = []; // {role, content} 数组
let aiIsStreaming = false;
let aiAbortController = null;

// 悬浮球拖动相关
let bubbleDragging = false;
let bubbleMoved = false;
let bubbleStartX = 0;
let bubbleStartY = 0;
let bubbleOffsetX = 0;
let bubbleOffsetY = 0;

// ── 悬浮球拖动逻辑 ──────────────────────────────────────
function initBubbleDrag() {
  const bubble = aiElements.bubble;

  // 触摸事件
  bubble.addEventListener("touchstart", onBubbleTouchStart, { passive: false });
  document.addEventListener("touchmove", onBubbleTouchMove, { passive: false });
  document.addEventListener("touchend", onBubbleTouchEnd);

  // 鼠标事件
  bubble.addEventListener("mousedown", onBubbleMouseDown);
  document.addEventListener("mousemove", onBubbleMouseMove);
  document.addEventListener("mouseup", onBubbleMouseUp);
}

function onBubbleTouchStart(e) {
  const touch = e.touches[0];
  startDrag(touch.clientX, touch.clientY);
}

function onBubbleTouchMove(e) {
  if (!bubbleDragging) return;
  e.preventDefault();
  const touch = e.touches[0];
  moveDrag(touch.clientX, touch.clientY);
}

function onBubbleTouchEnd() {
  endDrag();
}

function onBubbleMouseDown(e) {
  e.preventDefault();
  startDrag(e.clientX, e.clientY);
}

function onBubbleMouseMove(e) {
  if (!bubbleDragging) return;
  moveDrag(e.clientX, e.clientY);
}

function onBubbleMouseUp() {
  endDrag();
}

function startDrag(clientX, clientY) {
  const bubble = aiElements.bubble;
  const rect = bubble.getBoundingClientRect();
  bubbleDragging = true;
  bubbleMoved = false;
  bubbleStartX = clientX;
  bubbleStartY = clientY;
  bubbleOffsetX = clientX - rect.left;
  bubbleOffsetY = clientY - rect.top;
  bubble.style.transition = "none";
}

function moveDrag(clientX, clientY) {
  if (!bubbleDragging) return;
  const dx = clientX - bubbleStartX;
  const dy = clientY - bubbleStartY;
  if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
    bubbleMoved = true;
  }

  const bubble = aiElements.bubble;
  const bw = bubble.offsetWidth;
  const bh = bubble.offsetHeight;
  let x = clientX - bubbleOffsetX;
  let y = clientY - bubbleOffsetY;

  // 边界限制
  x = Math.max(0, Math.min(window.innerWidth - bw, x));
  y = Math.max(0, Math.min(window.innerHeight - bh, y));

  bubble.style.left = x + "px";
  bubble.style.top = y + "px";
  bubble.style.right = "auto";
  bubble.style.bottom = "auto";
}

function endDrag() {
  if (!bubbleDragging) return;
  bubbleDragging = false;
  const bubble = aiElements.bubble;
  bubble.style.transition = "";

  // 靠边吸附
  if (bubbleMoved) {
    const rect = bubble.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const midScreen = window.innerWidth / 2;

    bubble.style.top = rect.top + "px";
    bubble.style.bottom = "auto";

    if (centerX < midScreen) {
      bubble.style.left = "12px";
      bubble.style.right = "auto";
    } else {
      bubble.style.left = "auto";
      bubble.style.right = "12px";
    }

    // 拖动后自动收起对话框（位置已变，需重新对齐）
    if (aiElements.chat.style.display !== "none") {
      aiElements.chat.style.display = "none";
      bubble.classList.remove("ai-bubble-active");
    }
  }
}

// ── 悬浮球点击 → 展开/收起对话框 ────────────────────────
aiElements.bubble.addEventListener("click", () => {
  if (bubbleMoved) return;
  toggleAiChat();
});

function toggleAiChat() {
  const chat = aiElements.chat;
  const isVisible = chat.style.display !== "none";
  if (isVisible) {
    chat.style.display = "none";
    aiElements.bubble.classList.remove("ai-bubble-active");
  } else {
    // 仅桌面端动态定位，移动端用 CSS
    if (window.innerWidth > 640) {
      const bRect = aiElements.bubble.getBoundingClientRect();
      const chatW = 380;
      const margin = 10;
      // 对话框底部对齐到悬浮球顶部上方 8px
      let cssBottom = window.innerHeight - bRect.top + 8;
      // 右边缘对齐悬浮球右边缘
      let cssRight = window.innerWidth - bRect.right;
      // 防止右侧超出屏幕
      if (cssRight < margin) cssRight = margin;
      // 防止对话框左侧超出屏幕
      if (window.innerWidth - cssRight - chatW < margin) {
        cssRight = window.innerWidth - chatW - margin;
      }
      // 防止对话框顶部超出屏幕（内容过多时改为向下展开）
      if (cssBottom > window.innerHeight - 80) {
        cssBottom = window.innerHeight - bRect.bottom - 8;
        chat.style.bottom = "auto";
        chat.style.top = (bRect.bottom + 8) + "px";
      } else {
        chat.style.top = "auto";
        chat.style.bottom = cssBottom + "px";
      }
      chat.style.right = cssRight + "px";
      chat.style.left = "auto";
    }
    chat.style.display = "flex";
    aiElements.bubble.classList.add("ai-bubble-active");
    updateAiPreload();
    scrollAiToBottom();
    aiElements.input.focus();
  }
}

// 关闭按钮
aiElements.close.addEventListener("click", () => {
  aiElements.chat.style.display = "none";
  aiElements.bubble.classList.remove("ai-bubble-active");
});

// 新对话
aiElements.newChat.addEventListener("click", () => {
  aiChatHistory = [];
  aiElements.messages.innerHTML = getWelcomeHtml();
  aiElements.input.value = "";
  updateAiCharCount();
  updateAiPreload();
  bindQuickActions();
});

function getWelcomeHtml() {
  return `
    <div class="ai-welcome-msg">
      <div class="ai-welcome-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="36" height="36">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
          <circle cx="12" cy="12" r="3"/>
        </svg>
      </div>
      <p class="ai-welcome-title">医学 AI 解析助手</p>
      <p class="ai-welcome-hint">基于 GPT-5.5 大模型，专注医学执业资格考试题目解析</p>
      <div class="ai-quick-actions" id="aiQuickActions">
        <button class="ai-quick-btn" data-action="analyze">📋 分析当前题目</button>
        <button class="ai-quick-btn" data-action="keypoint">🎯 提炼核心考点</button>
        <button class="ai-quick-btn" data-action="compare">🔍 对比易混选项</button>
      </div>
    </div>`;
}

// ── 快捷操作按钮 ─────────────────────────────────────────
function bindQuickActions() {
  const container = document.getElementById("aiQuickActions");
  if (!container) return;
  container.querySelectorAll(".ai-quick-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      const pageQuestions = getCurrentPageQuestions();
      if (!pageQuestions) {
        showToast("当前页面没有题目，请先搜索");
        return;
      }
      let prompt = "";
      switch (action) {
        case "analyze":
          prompt = "请详细分析以下题目，给出正确答案、解题思路和各选项辨析：\n\n" + pageQuestions;
          break;
        case "keypoint":
          prompt = "请提炼以下题目涉及的核心考点，整理成便于记忆的要点清单：\n\n" + pageQuestions;
          break;
        case "compare":
          prompt = "请对比以下题目中容易混淆的选项，说明它们的区别和鉴别要点：\n\n" + pageQuestions;
          break;
      }
      if (prompt) {
        aiChatHistory.push({ role: "user", content: prompt });
        appendAiMessage("user", btn.textContent.trim());
        callAiApi();
      }
    });
  });
}

function showToast(msg) {
  const toast = document.createElement("div");
  toast.className = "ai-toast";
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add("ai-toast-show"), 10);
  setTimeout(() => { toast.classList.remove("ai-toast-show"); setTimeout(() => toast.remove(), 300); }, 2500);
}

// ── 获取当前页面题目文本 ─────────────────────────────────
function getCurrentPageQuestions() {
  if (!lastResults || lastResults.length === 0) return "";
  return lastResults
    .map((item, i) => `【题目${i + 1}】\n${item.content}`)
    .join("\n\n");
}

// ── 预加载提示 ──────────────────────────────────────────
function updateAiPreload() {
  const hasQuestions = lastResults && lastResults.length > 0;
  const includeChecked = aiElements.includeQuestions.checked;
  if (hasQuestions && includeChecked) {
    aiElements.preload.style.display = "flex";
  } else {
    aiElements.preload.style.display = "none";
  }
}

aiElements.includeQuestions.addEventListener("change", updateAiPreload);

aiElements.preloadClear.addEventListener("click", () => {
  aiElements.includeQuestions.checked = false;
  updateAiPreload();
});

// ── 输入字数统计 + 自动调整高度 ─────────────────────────
aiElements.input.addEventListener("input", () => {
  // 强制截断超长输入
  if (aiElements.input.value.length > AI_CONFIG.maxInputLength) {
    aiElements.input.value = aiElements.input.value.slice(0, AI_CONFIG.maxInputLength);
  }
  updateAiCharCount();
  autoResizeTextarea();
});

function updateAiCharCount() {
  const len = aiElements.input.value.length;
  aiElements.charCount.textContent = `${len}/${AI_CONFIG.maxInputLength}`;
  if (len > AI_CONFIG.maxInputLength * 0.9) {
    aiElements.charCount.style.color = "#c0392b";
  } else {
    aiElements.charCount.style.color = "";
  }
}

function autoResizeTextarea() {
  const ta = aiElements.input;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
}

// ── 发送消息 ──────────────────────────────────────────────
aiElements.send.addEventListener("click", sendAiMessage);
aiElements.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendAiMessage();
  }
});

async function sendAiMessage() {
  if (aiIsStreaming) return;

  const userText = aiElements.input.value.trim();
  const includeQuestions = aiElements.includeQuestions.checked;
  const pageQuestions = getCurrentPageQuestions();

  // 构建实际发送内容
  let messageContent = "";

  if (userText) {
    messageContent = userText;
    if (includeQuestions && pageQuestions) {
      messageContent =
        "以下是当前页面的题目供参考：\n\n" +
        pageQuestions +
        "\n\n---\n\n用户问题：" +
        userText;
    }
  } else if (includeQuestions && pageQuestions) {
    messageContent =
      "请分析以下题目，帮我理解每道题的解题思路和关键知识点：\n\n" +
      pageQuestions;
  } else {
    aiElements.input.focus();
    aiElements.input.classList.add("shake");
    setTimeout(() => aiElements.input.classList.remove("shake"), 500);
    return;
  }

  // 显示用户消息（只显示用户实际输入或简短描述）
  const displayText = userText || "📋 分析当前页面题目";
  appendAiMessage("user", displayText);

  // 清空输入
  aiElements.input.value = "";
  updateAiCharCount();
  autoResizeTextarea();

  // 添加到历史
  aiChatHistory.push({ role: "user", content: messageContent });

  // 发送请求
  await callAiApi();
}

function appendAiMessage(role, content) {
  const msgDiv = document.createElement("div");
  msgDiv.className = `ai-msg ai-msg-${role}`;
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  msgDiv.innerHTML = `
    <div class="ai-msg-wrap">
      <div class="ai-msg-bubble">${role === "assistant" ? renderMarkdown(content) : escapeHtml(content)}</div>
      <div class="ai-msg-meta">${role === "assistant" ? "AI 解析" : "你"} · ${time}</div>
    </div>`;
  aiElements.messages.appendChild(msgDiv);
  scrollAiToBottom();
  return msgDiv;
}

function scrollAiToBottom() {
  requestAnimationFrame(() => {
    aiElements.messages.scrollTop = aiElements.messages.scrollHeight;
  });
}

// Markdown 渲染（医学解析增强版）
function renderMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);

  // 代码块
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="ai-code-block"><code>$2</code></pre>');
  html = html.replace(/`([^`]+)`/g, '<code class="ai-inline-code">$1</code>');

  // 标题（### / ## / #）
  html = html.replace(/^### (.+)$/gm, '<h4 class="ai-md-h4">$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3 class="ai-md-h3">$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h3 class="ai-md-h3">$1</h3>');

  // 分隔线
  html = html.replace(/^---$/gm, '<hr class="ai-md-hr">');

  // 加粗和斜体
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");

  // 无序列表（连续 - 开头行）
  html = html.replace(/((?:^- .+$\n?)+)/gm, (match) => {
    const items = match.trim().split("\n").map(l => `<li>${l.replace(/^- /, "")}</li>`).join("");
    return `<ul class="ai-md-list">${items}</ul>`;
  });

  // 有序列表（连续数字. 开头行）
  html = html.replace(/((?:^\d+\. .+$\n?)+)/gm, (match) => {
    const items = match.trim().split("\n").map(l => `<li>${l.replace(/^\d+\. /, "")}</li>`).join("");
    return `<ol class="ai-md-list">${items}</ol>`;
  });

  // 换行
  html = html.replace(/\n/g, "<br>");
  // 清理列表内多余 br
  html = html.replace(/<\/li><br>/g, "</li>");
  html = html.replace(/<\/ul><br>/g, "</ul>");
  html = html.replace(/<\/ol><br>/g, "</ol>");
  html = html.replace(/<\/h[34]><br>/g, (m) => m.replace("<br>", ""));
  html = html.replace(/<hr class="ai-md-hr"><br>/g, '<hr class="ai-md-hr">');

  return html;
}

// ── 调用 AI API（通过后端代理，流式） ────────────────────
async function callAiApi() {
  aiIsStreaming = true;
  // 不禁用按钮，改为停止按钮（见��方 typing indicator 处）

  // 添加 typing indicator + 停止按钮
  const assistantMsg = appendAiMessage("assistant", "");
  const bubbleEl = assistantMsg.querySelector(".ai-msg-bubble");
  bubbleEl.innerHTML = '<span class="ai-typing">思考中...</span>';
  // 发送期间将发送按钮改为停止按钮
  aiElements.send.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
  aiElements.send.title = "停止";
  aiElements.send.disabled = false;
  const stopHandler = () => { if (aiAbortController) aiAbortController.abort(); };
  aiElements.send.addEventListener("click", stopHandler, { once: true });

  try {
    aiAbortController = new AbortController();

    const response = await fetch(`${API_BASE}/ai/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },

      body: JSON.stringify({
        messages: [
          { role: "system", content: AI_CONFIG.systemPrompt },
          ...aiChatHistory.slice(-10),
        ],
        temperature: AI_CONFIG.temperature,
        max_tokens: AI_CONFIG.maxTokens,
      }),

      signal: aiAbortController.signal,
    });

    if (!response.ok) {
      const errText = await response.text().catch(() => "");
      throw new Error(`API 错误 (${response.status}): ${errText}`);
    }

    // 流式读取
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data: ")) continue;
        const data = trimmed.slice(6);
        if (data === "[DONE]") continue;

        try {
          const parsed = JSON.parse(data);
          // 检查是否是后端返回的错误
          if (parsed.error) {
            throw new Error(parsed.error);
          }
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            fullText += delta;
            bubbleEl.innerHTML = renderMarkdown(fullText);
            scrollAiToBottom();
          }
        } catch (e) {
          // 只忽略 JSON 解析语法错误，其余��如后端错误）继续抛出
          if (!(e instanceof SyntaxError)) {
            throw e;
          }
        }
      }
    }

    // 处理 buffer 中残留的最后一行
    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith("data: ") && trimmed.slice(6) !== "[DONE]") {
        try {
          const parsed = JSON.parse(trimmed.slice(6));
          if (parsed.error) {
            throw new Error(parsed.error);
          }
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            fullText += delta;
            bubbleEl.innerHTML = renderMarkdown(fullText);
          }
        } catch (e) {
          // 忽略解析错误
        }
      }
    }

    if (!fullText) {
      bubbleEl.innerHTML =
        '<span class="ai-error">未收到有效回复，请重试</span>';
    }

    if (fullText) {
      aiChatHistory.push({ role: "assistant", content: fullText });
    }
  } catch (error) {
    if (error.name === "AbortError") {
      bubbleEl.innerHTML = '<span class="ai-error">已取消</span>';
    } else {
      bubbleEl.innerHTML = `<span class="ai-error">请求失败：${escapeHtml(error.message)}</span>`;
    }
  } finally {
    aiIsStreaming = false;
    aiElements.send.removeEventListener("click", stopHandler);
    aiElements.send.disabled = false;
    aiElements.send.classList.remove("ai-send-loading");
    // 恢复��送图标
    aiElements.send.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20"><path d="m22 2-7 20-4-9-9-4z"/><path d="m22 2-11 11"/></svg>';
    aiElements.send.title = "发送";
    aiAbortController = null;
  }
}

// ── 初始化 ────────────────────────────────────────────────
initBubbleDrag();
bindQuickActions();
updateSearchModeHint();
showWelcome();
