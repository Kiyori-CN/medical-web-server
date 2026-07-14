#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医学搜题系统配置管理
支持环境变量覆盖，优先级：环境变量 > 环境配置类 > 基础配置类
"""
import os
from pathlib import Path


class Config:
    """基础配置（所有环境共享的默认值）"""
    # ── 知识库路径 ──
    KB_PATH = os.getenv('KB_PATH', '/srv/projects/medical-web-server/knowledge-base/西医题库')
    KNOWLEDGE_BASE_ROOT = os.getenv(
        'KNOWLEDGE_BASE_ROOT',
        '/srv/projects/medical-web-server/knowledge-base/v2',
    )
    CATALOG_DB = os.getenv('CATALOG_DB', '')
    PRACTICE_DB = os.getenv(
        'PRACTICE_DB',
        '/srv/projects/medical-web-server/state/practice.sqlite3',
    )
    LEGACY_SEARCH_ENABLED = os.getenv('LEGACY_SEARCH_ENABLED', 'true').lower() in (
        'true', '1', 'yes'
    )

    # ── Flask 服务 ──
    HOST = os.getenv('FLASK_HOST', '0.0.0.0')
    PORT = int(os.getenv('FLASK_PORT', '8080'))
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*')

    # ── 搜索参数 ──
    DEFAULT_TOP_K = int(os.getenv('DEFAULT_TOP_K', '10'))
    DEFAULT_THRESHOLD = float(os.getenv('DEFAULT_THRESHOLD', '0.3'))
    MAX_WORKERS = int(os.getenv('MAX_WORKERS', '4'))
    TIMEOUT = int(os.getenv('TIMEOUT', '120'))
    MAX_QUERY_LENGTH = 200
    MAX_TOP_K = 50

    # ── 嵌入模型 API（硅基流动） ──
    EMBED_API_KEY = os.getenv(
        'EMBED_API_KEY',
        ''
    )
    EMBED_API_URL = os.getenv(
        'EMBED_API_URL',
        'https://api.siliconflow.cn/v1/embeddings'
    )
    EMBED_MODEL = os.getenv('EMBED_MODEL', 'BAAI/bge-m3')

    # ── LLM API（硅基流动，用于智能问答） ──
    LLM_API_KEY = os.getenv(
        'LLM_API_KEY',
        ''
    )
    LLM_API_URL = os.getenv(
        'LLM_API_URL',
        'https://api.siliconflow.cn/v1/chat/completions'
    )
    LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen2.5-7B-Instruct')

    # ── AI 对话配置（用于 /api/ai/chat 接口） ──
    AI_API_URL = os.getenv(
        'AI_API_URL',
        'https://newapi.com.cn/v1/chat/completions'
    )
    AI_API_KEY = os.getenv(
        'AI_API_KEY',
        ''
    )
    AI_MODEL = os.getenv('AI_MODEL', 'gpt-5.5')
    # ── 搜索模式默认值 ──
    DEFAULT_SEARCH_MODE = 'keyword'  # keyword / semantic / hybrid


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True
    PORT = 5001
    KB_PATH = os.getenv('KB_PATH', '/storage/emulated/0/Project/ikbs/西医题库')
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    KNOWLEDGE_BASE_ROOT = os.getenv(
        'KNOWLEDGE_BASE_ROOT',
        str(_REPO_ROOT / 'knowledge-base' / 'v2'),
    )
    CATALOG_DB = os.getenv('CATALOG_DB', '')
    PRACTICE_DB = os.getenv(
        'PRACTICE_DB',
        str(_REPO_ROOT / 'state' / 'practice.sqlite3'),
    )
    LEGACY_SEARCH_ENABLED = os.getenv('LEGACY_SEARCH_ENABLED', 'false').lower() in (
        'true', '1', 'yes'
    )


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    PORT = 8080
    KB_PATH = os.getenv('KB_PATH', '/srv/projects/medical-web-server/knowledge-base/西医题库')
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*')


_config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': ProductionConfig,
}


def get_config():
    """根据 FLASK_ENV 环境变量获取对应配置实例"""
    env = os.getenv('FLASK_ENV', 'production').lower()
    config_cls = _config_map.get(env, _config_map['default'])
    return config_cls()
