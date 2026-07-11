#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gunicorn 生产环境配置
"""
import os

# ── 环境变量（在 worker fork 前设置） ────────────────────
os.environ.setdefault('FLASK_ENV', 'production')
os.environ.setdefault('KB_PATH', '/srv/projects/medical-web-server/knowledge-base/西医题库')
os.environ.setdefault('EMBED_API_KEY', '')
os.environ.setdefault('EMBED_API_URL', 'https://api.siliconflow.cn/v1/embeddings')
os.environ.setdefault('EMBED_MODEL', 'BAAI/bge-m3')
os.environ.setdefault('LLM_API_KEY', '')
os.environ.setdefault('LLM_API_URL', 'https://api.siliconflow.cn/v1/chat/completions')

# ── 服务器绑定 ───────────────────────────────────────────
bind = '0.0.0.0:8080'

# ── Worker 配置 ──────────────────────────────────────────
workers = 3
worker_class = 'sync'
timeout = 120
graceful_timeout = 30
keepalive = 5

# ── 预加载 ───────────────────────────────────────────────
preload_app = True

# ── 日志 ─────────────────────────────────────────────────
accesslog = '/var/log/medical_exam/access.log'
errorlog = '/var/log/medical_exam/error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" %(L)s'

# ── Worker 生命周期 ──────────────────────────────────────
max_requests = 5000
max_requests_jitter = 500

# ── 进程名 ───────────────────────────────────────────────
proc_name = 'medical_exam'
