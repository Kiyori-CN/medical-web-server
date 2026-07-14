# -*- coding: utf-8 -*-
"""
医学题库后端服务器
v2 使用 SQLite/FTS5 提供结构化搜题与刷题，v1 搜索仅作为迁移兼容层。
"""

import os
from pathlib import Path
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("KB_PATH", "/srv/projects/medical-web-server/knowledge-base/西医题库")
os.environ.setdefault("EMBED_API_KEY", "")
os.environ.setdefault("EMBED_API_URL", "https://api.siliconflow.cn/v1/embeddings")
os.environ.setdefault("EMBED_MODEL", "BAAI/bge-m3")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("LLM_API_URL", "https://api.siliconflow.cn/v1/chat/completions")

from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
import time
import random
import re
import math
import json
import requests as http_requests
from collections import OrderedDict
from ikbs_pysearch import get_engine
from config import get_config
from medical_v2.api import create_catalog_blueprint
from medical_v2.catalog import Catalog, CatalogUnavailable, resolve_catalog_path
from medical_v2.practice_api import create_practice_blueprint

# ── 配置 ──────────────────────────────────────────────────────────────────────
config = get_config()
AI_API_URL = config.AI_API_URL
AI_API_KEY = config.AI_API_KEY
AI_MODEL = config.AI_MODEL
app = Flask(__name__)
frontend_dir = Path(
    os.getenv("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "public"))
).resolve()
v2_catalog_path = resolve_catalog_path(config.CATALOG_DB, config.KNOWLEDGE_BASE_ROOT)
app.register_blueprint(create_catalog_blueprint(v2_catalog_path))
app.register_blueprint(create_practice_blueprint(v2_catalog_path, config.PRACTICE_DB))

# CORS 配置
if config.CORS_ORIGINS == '*':
    CORS(app)
else:
    CORS(app, origins=[o.strip() for o in config.CORS_ORIGINS.split(',') if o.strip()])

# ── AI 对话配置（硬编码） ─────────────────────────────────────────────────────

# ── 常量 ──────────────────────────────────────────────────────────────────────
# 统一题型映射表（前后端必须完全一致）
TYPE_MAP = {
    "A1型题": "单选题", "A2型题": "单选题", "A3型题": "单选题", "A4型题": "单选题",
    "A3/A4型题": "单选题",
    "B型题": "单选题", "B1型题": "单选题",
    "X型题": "多选题",
    "单选题": "单选题", "多选题": "多选题", "判断题": "判断题",
    "填空题": "填空题", "简答题": "简答题",
    "名词解释": "名词解释", "论述题": "简答题",
}

# 允许的题型过滤值
VALID_QUESTION_TYPES = {"单选题", "多选题", "判断题", "填空题", "简答题", "名词解释"}

# 允许的搜索模式
VALID_SEARCH_MODES = {"keyword", "semantic", "hybrid"}

# ── 搜索引擎 ──────────────────────────────────────────────────────────────────
search_engine = None


def init_engine():
    """初始化搜索引擎（仅执行一次）"""
    global search_engine
    if search_engine is not None:
        return

    try:
        print(f"📂 知识库路径: {config.KB_PATH}", flush=True)
        search_engine = get_engine(config.KB_PATH)
        search_engine.load()
        print("✅ 搜索引擎初始化成功", flush=True)

        status = search_engine.status()
        if status.get('optimized'):
            print(f"📊 知识库统计: {status.get('total_chunks', 0)} 个分块", flush=True)
        else:
            print("⚠️  知识库未优化，请先运行 rebuild_index", flush=True)
    except Exception as e:
        print(f"❌ 搜索引擎初始化失败: {e}", flush=True)
        search_engine = None


# 模块加载时自动初始化（Gunicorn preload_app 时执行）。本地 v2 开发默认关闭 v1。
if config.LEGACY_SEARCH_ENABLED:
    init_engine()
else:
    print("ℹ️  v1 旧搜索已禁用，v2 SQLite/FTS5 接口仍可用", flush=True)


# ── 搜索缓存（线程安全的 LRU） ───────────────────────────────────────────────
class LRUCache:
    """简单的 LRU 缓存"""

    def __init__(self, maxsize=1000):
        self._cache = OrderedDict()
        self._maxsize = maxsize

    def get(self, key):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()


_search_cache = LRUCache(maxsize=1000)


def extract_question_type(text):
    """从题目文本中提取题型"""
    m = re.search(r"\[([^\]]+)\]", text)
    if m:
        raw_type = m.group(1)
        return TYPE_MAP.get(raw_type, raw_type)
    return "未知题型"


def do_keyword_search(query, top_k, question_type):
    """执行关键词搜索（带缓存 + 题型过滤）"""
    cache_key = f"kw|{query}|{top_k}|{question_type}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    fetch_k = top_k * 3 if question_type else top_k
    results = search_engine.keyword_search(query, top_k=fetch_k, expand_context=False)

    if question_type:
        results = [r for r in results if extract_question_type(r.get('text', '')) == question_type]

    results = results[:top_k]
    _search_cache.put(cache_key, results)
    return results


def do_semantic_search(query, top_k, question_type):
    """执行语义搜索（带缓存 + 题型过滤）"""
    cache_key = f"sem|{query}|{top_k}|{question_type}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    # 调用嵌入模型获取查询向量
    try:
        vecs = search_engine.encode([query])
        if not vecs or len(vecs) == 0:
            raise RuntimeError("向量编码返回空结果，请检查嵌入模型服务")
        query_vec = vecs[0]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"向量编码失败: {e}")

    fetch_k = top_k * 3 if question_type else top_k
    results = search_engine.search(
        query_vec, top_k=fetch_k,
        threshold=config.DEFAULT_THRESHOLD,
        expand_context=False
    )

    if question_type:
        results = [r for r in results if extract_question_type(r.get('text', '')) == question_type]

    results = results[:top_k]
    _search_cache.put(cache_key, results)
    return results


def do_hybrid_search(query, top_k, question_type):
    """执行混合搜索（关键词 + 语义融合）"""
    cache_key = f"hyb|{query}|{top_k}|{question_type}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    # 获取查询向量
    try:
        vecs = search_engine.encode([query])
        if not vecs or len(vecs) == 0:
            # 回退到纯关键词搜索
            return do_keyword_search(query, top_k, question_type)
        query_vec = vecs[0]
    except Exception:
        # 编码失败，回退到关键词搜索
        return do_keyword_search(query, top_k, question_type)

    fetch_k = top_k * 3 if question_type else top_k
    results = search_engine.hybrid_search(
        query, query_vec, top_k=fetch_k,
        threshold=config.DEFAULT_THRESHOLD,
        keyword_weight=0.4, semantic_weight=0.6,
        expand_context=False
    )

    if question_type:
        results = [r for r in results if extract_question_type(r.get('text', '')) == question_type]

    results = results[:top_k]
    _search_cache.put(cache_key, results)
    return results


def format_result(r, search_mode='keyword'):
    """格式化单条搜索结果"""
    text = r.get('text', '')
    raw_score = r.get('score', 0)

    # 根据搜索模式归一化分数
    if search_mode == 'semantic':
        normalized_score = max(0.0, min(float(raw_score), 1.0))
    elif search_mode == 'hybrid':
        # RRF 分数通常在 0~0.02 范围，用对数映射到 0~1
        if raw_score > 0:
            normalized_score = min(1.0 - 1.0 / (1.0 + math.log1p(raw_score * 100) / 5.0), 1.0)
        else:
            normalized_score = 0.0
    else:
        if raw_score > 0:
            normalized_score = min(1.0 - 1.0 / (1.0 + math.log1p(raw_score) / 5.0), 1.0)
        else:
            normalized_score = 0.0

    return {
        'content': text,
        'score': round(normalized_score, 4),
        'source': r.get('fileName', ''),
        'question_type': extract_question_type(text),
        'context_before': r.get('context_before', ''),
        'context_after': r.get('context_after', ''),
    }


# ── API 路由 ──────────────────────────────────────────────────────────────────

@app.route('/api/search', methods=['POST'])
def search():
    """搜索题目（支持 keyword / semantic / hybrid 三种模式）"""
    start = time.time()

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '请求体必须为 JSON 格式'}), 400

    query = str(data.get('query', '')).strip()
    top_k = data.get('top_k', config.DEFAULT_TOP_K)
    question_type = str(data.get('question_type', '')).strip()
    search_mode = str(data.get('search_mode', 'keyword')).strip().lower()

    # 输入校验
    if not query:
        return jsonify({'error': '查询内容不能为空'}), 400

    if len(query) > config.MAX_QUERY_LENGTH:
        return jsonify({'error': f'查询内容过长，最多 {config.MAX_QUERY_LENGTH} 字'}), 400

    try:
        top_k = int(top_k)
        top_k = max(1, min(top_k, config.MAX_TOP_K))
    except (ValueError, TypeError):
        top_k = config.DEFAULT_TOP_K

    if question_type and question_type not in VALID_QUESTION_TYPES:
        question_type = ''

    if search_mode not in VALID_SEARCH_MODES:
        search_mode = 'keyword'

    if not search_engine:
        return jsonify({'error': '搜索引擎未初始化，请稍后重试'}), 503

    try:
        if search_mode == 'semantic':
            results = do_semantic_search(query, top_k, question_type)
        elif search_mode == 'hybrid':
            results = do_hybrid_search(query, top_k, question_type)
        else:
            results = do_keyword_search(query, top_k, question_type)

        formatted = [format_result(r, search_mode) for r in results]

        elapsed = int((time.time() - start) * 1000)
        status = search_engine.status()

        return jsonify({
            'results': formatted,
            'search_mode': search_mode,
            'stats': {
                'time_ms': elapsed,
                'total_questions': status.get('total_chunks', 0),
                'found': len(formatted),
            }
        })

    except RuntimeError as e:
        return jsonify({'error': f'搜索失败: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'搜索失败: {str(e)}'}), 500


@app.route('/api/random', methods=['GET'])
def random_questions():
    """随机练习"""
    if not search_engine:
        return jsonify({'error': '搜索引擎未初始化'}), 503

    try:
        count = int(request.args.get('count', 5))
        count = max(1, min(count, 20))
    except (ValueError, TypeError):
        count = 5
    question_type = request.args.get('question_type', '').strip()

    try:
        if not search_engine.chunks:
            search_engine.load()

        total = len(search_engine.chunks)
        if total == 0:
            return jsonify({'results': []})

        if question_type and question_type in VALID_QUESTION_TYPES:
            candidates = [
                i for i in range(total)
                if extract_question_type(search_engine.chunks[i].get('text', '')) == question_type
            ]
            if not candidates:
                return jsonify({'results': []})
            indices = random.sample(candidates, min(count, len(candidates)))
        else:
            indices = random.sample(range(total), min(count, total))

        results = []
        for idx in indices:
            chunk = search_engine.chunks[idx]
            text = chunk.get('text', '')
            results.append({
                'content': text,
                'score': 1.0,
                'source': chunk.get('fileName', ''),
                'question_type': extract_question_type(text),
            })

        return jsonify({'results': results})

    except Exception as e:
        return jsonify({'error': f'获取随机题目失败: {str(e)}'}), 500


@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    """AI 辅助对话（流式 SSE 代理）"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': '请求体必须为 JSON 格式'}), 400

    messages = data.get('messages', [])
    if not messages or not isinstance(messages, list):
        return jsonify({'error': '消息不能为空'}), 400

    # 校验消息格式（防止注入和格式错误）
    valid_roles = {'user', 'assistant', 'system'}
    for m in messages:
        if not isinstance(m, dict):
            return jsonify({'error': '消息格式错误：每条消息必须是对象'}), 400
        if m.get('role') not in valid_roles:
            return jsonify({'error': f'无效的消息角色: {m.get("role")}'}), 400
        if not isinstance(m.get('content'), str):
            return jsonify({'error': '消息内容必须为字符串'}), 400

    # 校验消息总长度（防止滥用）
    total_length = sum(len(m.get('content', '')) for m in messages)
    if total_length > 50000:
        return jsonify({'error': '消息内容过长，请缩短对话或开启新对话'}), 400

    # 前端可选参数
    temperature = data.get('temperature', 0.7)
    max_tokens = data.get('max_tokens', 4096)
    # 安全限制
    if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
        temperature = 0.7
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 8192:
        max_tokens = 4096

    # 构建转发请求
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AI_API_KEY}",
    }

    def generate():
        try:
            resp = http_requests.post(
                AI_API_URL,
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            )

            if resp.status_code != 200:
                error_text = resp.text[:500]
                yield f"data: {json.dumps({'error': f'AI 服务返回错误 ({resp.status_code}): {error_text}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            resp.encoding = 'utf-8'
            for line in resp.iter_lines(decode_unicode=True):
                if line:
                    # 确保每个 SSE 事件以 data: 开头且正确换行
                    if not line.startswith("data:"):
                        continue
                    yield line + "\n\n"

        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'error': 'AI 服务请求超时，请重试'})}\n\n"
            yield "data: [DONE]\n\n"
        except http_requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'error': '无法连接 AI 服务，请检查网络'})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI 服务异常: {str(e)}'})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream; charset=utf-8',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/health', methods=['GET'])
def health():
    """Core v2 health with optional v1 compatibility status."""
    try:
        v2_status = Catalog(v2_catalog_path).health()
    except (CatalogUnavailable, OSError, ValueError) as error:
        return jsonify({
            'status': 'unhealthy',
            'engine': 'sqlite-fts5',
            'message': str(error),
        }), 503
    if v2_status['status'] != 'healthy':
        return jsonify(v2_status), 503

    legacy = {'enabled': config.LEGACY_SEARCH_ENABLED, 'status': 'disabled'}
    if config.LEGACY_SEARCH_ENABLED:
        legacy['status'] = 'unavailable'
        if search_engine:
            try:
                legacy_engine_status = search_engine.status()
                legacy = {
                    'enabled': True,
                    'status': 'healthy',
                    'optimized': legacy_engine_status.get('optimized', False),
                    'chunks': legacy_engine_status.get('total_chunks', 0),
                }
            except Exception as error:
                legacy['message'] = str(error)

    return jsonify({**v2_status, 'legacy': legacy})


@app.route('/api/stats', methods=['GET'])
def stats():
    """题库统计"""
    if not search_engine:
        return jsonify({'error': '搜索引擎未初始化'}), 503

    try:
        status = search_engine.status()
        return jsonify({
            'total_files': status.get('total_files', 0),
            'total_questions': status.get('total_chunks', 0),
            'optimized': status.get('optimized', False),
            'vector_dim': status.get('vector_dim', 0),
        })
    except Exception as e:
        return jsonify({'error': f'获取统计失败: {str(e)}'}), 500


@app.route('/api/status', methods=['GET'])
def kb_status():
    """知识库状态"""
    if not search_engine:
        return jsonify({'error': '搜索引擎未初始化'}), 503

    try:
        status = search_engine.status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': f'获取状态失败: {str(e)}'}), 500


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """清除搜索缓存"""
    _search_cache.clear()
    return jsonify({'message': '缓存已清除'})


# ── 前端静态文件服务 ──────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(frontend_dir), "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(str(frontend_dir), path)

# ── 启动入口 ──────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 60, flush=True)
    print("🏥 医学题库后端服务器 v2", flush=True)
    print("=" * 60, flush=True)
    print(f"🌍 环境: {os.getenv('FLASK_ENV', 'production')}", flush=True)
    print(f"🔧 调试模式: {config.DEBUG}", flush=True)
    print(f"🚀 服务器启动: http://{config.HOST}:{config.PORT}", flush=True)
    print(f"📡 API 端点:", flush=True)
    print(f"   GET  /api/health       - 健康检查", flush=True)
    print(f"   POST /api/v2/search    - v2 结构化搜题", flush=True)
    print(f"   POST /api/v2/practice/sessions - v2 创建练习", flush=True)
    print(f"   POST /api/search       - 搜索题目", flush=True)
    print(f"   GET  /api/random       - 随机练习", flush=True)
    print(f"   POST /api/ai/chat      - AI 辅助对话", flush=True)
    print(f"   GET  /api/stats        - 题库统计", flush=True)
    print(f"   GET  /api/status       - 知识库状态", flush=True)
    print(f"   POST /api/cache/clear  - 清除缓存", flush=True)
    print("=" * 60, flush=True)

    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, use_reloader=False)
