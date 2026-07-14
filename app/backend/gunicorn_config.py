#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gunicorn 生产环境配置
"""
import os

# ── 环境变量（在 worker fork 前设置） ────────────────────
os.environ.setdefault('FLASK_ENV', 'production')
os.environ.setdefault('KNOWLEDGE_BASE_ROOT', '/srv/projects/medical-web-server/knowledge-base')
os.environ.setdefault('APP_DB', '/srv/projects/medical-web-server/state/app.sqlite3')
os.environ.setdefault('SETTINGS_DB', '/srv/projects/medical-web-server/state/settings.sqlite3')
os.environ.setdefault('SETTINGS_KEY_PATH', '/srv/projects/medical-web-server/state/settings.key')
os.environ.setdefault('SECURE_COOKIES', 'true')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')

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
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(L)s'

# ── Worker 生命周期 ──────────────────────────────────────
max_requests = 5000
max_requests_jitter = 500

# ── 进程名 ───────────────────────────────────────────────
proc_name = 'medical_exam'
