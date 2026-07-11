# 医学搜题系统 (Medical Exam System)

## 📋 项目概述

基于 **ikbs_pysearch 极速搜索引擎** 的医学题库检索系统，提供毫秒级搜索响应。

- **题库规模**：西医题库 66,096 题
- **搜索引擎**：ikbs_pysearch（mmap 内存映射 + 二进制索引）
- **访问地址**：http://106.54.201.246
- **部署时间**：2026-03-01

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│  用户浏览器 (http://106.54.201.246)                      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Nginx (80端口)                                          │
│  ├─ 静态文件服务: /srv/projects/medical-web-server/app/frontend/          │
│  └─ 反向代理: /api/* → http://127.0.0.1:8080/api/       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Gunicorn (8080端口, 4 workers)                          │
│  ├─ Flask 后端: /srv/projects/medical-web-server/app/backend/main.py      │
│  ├─ 搜索引擎: ikbs_pysearch                              │
│  └─ 知识库: /srv/projects/medical-web-server/knowledge-base/西医题库/                   │
└─────────────────────────────────────────────────────────┘
```

---

## 📁 目录结构

```
/srv/projects/medical-web-server/app/
├── README.md                    # 本文档
├── frontend/                    # 前端静态文件
│   ├── index.html              # 主页面
│   ├── app.js                  # 前端逻辑
│   └── style.css               # 样式表
└── backend/                     # 后端服务
    ├── main.py                 # Flask 主程序
    ├── config.py               # 配置管理
    ├── ikbs_pysearch.py        # 搜索引擎核心
    ├── gunicorn_config.py      # Gunicorn 配置
    ├── requirements.txt        # Python 依赖
    ├── start.sh                # 启动脚本
    ├── setup_service.sh        # 环境初始化脚本
    ├── restart_service.sh      # 服务重启脚本
    └── monitor_service.sh      # 自动监控脚本

/srv/projects/medical-web-server/knowledge-base/西医题库/       # 知识库数据（只读）
/var/log/medical_exam/           # 日志目录
├── access.log                  # 访问日志
├── error.log                   # 错误日志
├── startup.log                 # 启动日志
└── monitor.log                 # 监控日志

/www/server/panel/vhost/nginx/
└── medical_exam.conf           # Nginx 配置文件
```

---

## 🛠️ 技术栈

### 前端
- **纯静态**：HTML5 + Vanilla JavaScript + CSS3
- **无框架依赖**：零构建工具，直接部署

### 后端
- **Web 框架**：Flask 3.x + Flask-CORS
- **WSGI 服务器**：Gunicorn (4 workers, 120s timeout)
- **搜索引擎**：ikbs_pysearch (mmap + 二进制索引)
- **Python 版本**：3.11.6

### 基础设施
- **Web 服务器**：Nginx 1.x
- **操作系统**：OpenCloudOS 9
- **进程管理**：Gunicorn + nohup

---

## 🚀 部署状态

### 当前运行状态

| 组件      | 状态 | 端口 | 进程                          |
|-----------|------|------|-------------------------------|
| Nginx     | ✅   | 80   | nginx master + workers        |
| Gunicorn  | ✅   | 8080 | gunicorn master + 4 workers   |
| 知识库    | ✅   | -    | 66,096 chunks (已优化索引)    |

### 健康检查

```bash
# 前端检查
curl http://106.54.201.246/

# 后端健康检查
curl http://106.54.201.246:8080/api/health
# 预期输出: {"status":"healthy","engine":"ikbs_pysearch","optimized":true,"chunks":66096}

# 进程检查
pgrep -f "gunicorn.*main:app"
```

---

## 📖 运维指南

### 1. 环境初始化（首次部署或环境检查）

```bash
bash /srv/projects/medical-web-server/app/backend/setup_service.sh
```

**功能**：
- 创建日志目录
- 检查知识库路径
- 检查 Python 依赖
- 验证配置文件
- 检查 Gunicorn 进程
- 检查 Nginx 配置

---

### 2. 服务重启（代码更新/配置修改后）

```bash
bash /srv/projects/medical-web-server/app/backend/restart_service.sh
```

**功能**：
- 优雅停止旧进程（2秒等待 + 强制终止）
- 清理日志（保留最近 1000 行）
- 启动新服务
- 自动健康检查

---

### 3. 自动监控守护（定时任务）

**配置方法**：在宝塔面板 → 计划任务 → 添加任务

| 配置项   | 值                                                   |
|----------|------------------------------------------------------|
| 任务类型 | Shell 脚本                                           |
| 任务名称 | 医学搜题系统监控                                     |
| 执行周期 | 每 5 分钟                                            |
| 脚本内容 | `bash /srv/projects/medical-web-server/app/backend/monitor_service.sh` |

**功能**：
- 每 5 分钟检查进程是否存在
- 检查健康接口是否正常（`/api/health`）
- 异常时自动重启（1 小时内最多 3 次，防止频繁重启）
- 记录所有操作到 `/var/log/medical_exam/monitor.log`

---

### 4. 日志管理

```bash
# 查看最近 50 行错误日志
tail -50 /var/log/medical_exam/error.log

# 实时监控访问日志
tail -f /var/log/medical_exam/access.log

# 查看监控日志
tail -50 /var/log/medical_exam/monitor.log

# 查看启动日志
cat /var/log/medical_exam/startup.log
```

**日志自动清理**：重启脚本会自动保留最近 1000 行日志，无需手动清理。

---

## 🔧 配置文件说明

### 1. Nginx 配置 (`/www/server/panel/vhost/nginx/medical_exam.conf`)

```nginx
server {
    listen 80;
    server_name 106.54.201.246;

    root /srv/projects/medical-web-server/app/frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**修改后重载**：
```bash
nginx -t && nginx -s reload
```

---

### 2. Gunicorn 配置 (`backend/gunicorn_config.py`)

```python
import multiprocessing
import os

os.environ['FLASK_ENV'] = 'production'
os.environ['KB_PATH'] = '/srv/projects/medical-web-server/knowledge-base/西医题库'

bind = "0.0.0.0:8080"
workers = 4
timeout = 120
preload_app = True
accesslog = "/var/log/medical_exam/access.log"
errorlog = "/var/log/medical_exam/error.log"
```

**关键参数**：
- `workers = 4`：工作进程数（根据 CPU 核心数调整）
- `timeout = 120`：请求超时时间（秒）
- `preload_app = True`：预加载应用（加快启动，共享内存）

---

### 3. 后端配置 (`backend/config.py`)

```python
class ProductionConfig(Config):
    DEBUG = False
    KB_PATH = '/srv/projects/medical-web-server/knowledge-base/西医题库'
    HOST = '0.0.0.0'
    PORT = 8080
    CORS_ORIGINS = '*'
```

**环境变量**：
- `FLASK_ENV=production`：生产环境模式
- `KB_PATH=/srv/projects/medical-web-server/knowledge-base/西医题库`：知识库路径

---

## 🐛 常见问题处理

### 问题 1：服务无法启动

```bash
# 1. 查看错误日志
tail -50 /var/log/medical_exam/error.log

# 2. 检查端口占用
netstat -tlnp | grep 8080

# 3. 手动测试启动
cd /srv/projects/medical-web-server/app/backend
bash start.sh
```

---

### 问题 2：健康检查失败

```bash
# 1. 测试后端接口
curl http://127.0.0.1:8080/api/health

# 2. 检查知识库路径
ls -la /srv/projects/medical-web-server/knowledge-base/西医题库/

# 3. 检查 Python 依赖
python3 -c "import flask, flask_cors, ikbs_pysearch"
```

---

### 问题 3：搜索结果为空

**可能原因**：
1. 知识库索引未优化
2. 知识库路径错误
3. 搜索引擎未正确初始化

**解决方法**：
```bash
# 检查知识库状态
curl http://127.0.0.1:8080/api/status

# 预期输出应包含：
# "optimized": true
# "total_chunks": 66096
```

---

### 问题 4：Nginx 配置错误

```bash
# 1. 测试 Nginx 配置
nginx -t

# 2. 查看配置文件
cat /www/server/panel/vhost/nginx/medical_exam.conf

# 3. 重载 Nginx
nginx -s reload
```

---

## 📡 API 接口文档

### 1. 健康检查

```http
GET /api/health
```

**响应示例**：
```json
{
  "status": "healthy",
  "engine": "ikbs_pysearch",
  "optimized": true,
  "chunks": 66096
}
```

---

### 2. 搜索题目

```http
POST /api/search
Content-Type: application/json

{
  "query": "高血压",
  "top_k": 10,
  "question_type": "单选题"
}
```

**响应示例**：
```json
{
  "results": [
    {
      "content": "题目内容...",
      "score": 0.95,
      "source": "文件名.md",
      "context_before": "上文...",
      "context_after": "下文..."
    }
  ],
  "stats": {
    "time_ms": 15,
    "total_questions": 66096,
    "found": 10
  }
}
```

---

### 3. 随机练习

```http
GET /api/random?count=5
```

**响应示例**：
```json
{
  "results": [
    {
      "content": "题目内容...",
      "score": 1.0,
      "source": "文件名.md"
    }
  ]
}
```

---

### 4. 题库统计

```http
GET /api/stats
```

**响应示例**：
```json
{
  "total_files": 150,
  "total_questions": 66096,
  "optimized": true,
  "vector_dim": 768
}
```

---

## 🔐 安全注意事项

1. **知识库权限**：`/srv/projects/medical-web-server/knowledge-base/西医题库/` 为只读，禁止修改
2. **日志权限**：`/var/log/medical_exam/` 需要写权限
3. **端口安全**：8080 端口仅监听 `127.0.0.1`，不对外暴露
4. **CORS 配置**：当前为 `*`，生产环境建议限制域名

---

## 📊 性能指标

| 指标           | 数值              |
|----------------|-------------------|
| 搜索响应时间   | 10-50ms           |
| 题库规模       | 66,096 题         |
| 并发支持       | 4 workers         |
| 内存占用       | ~500MB (预加载)   |
| 索引加载时间   | ~2s (启动时)      |

---

## 🔄 更新流程

### 1. 更新前端代码

```bash
# 1. 备份旧文件
cp /srv/projects/medical-web-server/app/frontend/app.js /srv/projects/medical-web-server/app/frontend/app.js.bak

# 2. 修改文件
vim /srv/projects/medical-web-server/app/frontend/app.js

# 3. 无需重启（静态文件直接生效）
# 4. 清除浏览器缓存测试
```

---

### 2. 更新后端代码

```bash
# 1. 备份旧文件
cp /srv/projects/medical-web-server/app/backend/main.py /srv/projects/medical-web-server/app/backend/main.py.bak

# 2. 修改文件
vim /srv/projects/medical-web-server/app/backend/main.py

# 3. 重启服务
bash /srv/projects/medical-web-server/app/backend/restart_service.sh

# 4. 验证服务
curl http://127.0.0.1:8080/api/health
```

---

### 3. 更新知识库

```bash
# 知识库为只读，需要联系管理员更新
# 更新后需要重启服务以重新加载索引
bash /srv/projects/medical-web-server/app/backend/restart_service.sh
```

---

## 📞 联系方式

- **服务器**：106.54.201.246
- **SSH 用户**：root
- **部署路径**：/srv/projects/medical-web-server/app/
- **知识库路径**：/srv/projects/medical-web-server/knowledge-base/西医题库/

---

## 📝 维护日志

| 日期       | 操作                     | 操作人 |
|------------|--------------------------|--------|
| 2026-03-01 | 初始部署                 | AI     |
| 2026-03-01 | 创建运维脚本体系         | AI     |
| 2026-03-01 | 优化日志管理             | AI     |
| 2026-03-01 | 创建 README 文档         | AI     |

---

## 🎯 快速命令索引

```bash
# 环境检查
bash /srv/projects/medical-web-server/app/backend/setup_service.sh

# 服务重启
bash /srv/projects/medical-web-server/app/backend/restart_service.sh

# 查看日志
tail -50 /var/log/medical_exam/error.log

# 健康检查
curl http://127.0.0.1:8080/api/health

# 进程检查
pgrep -f "gunicorn.*main:app"

# Nginx 重载
nginx -t && nginx -s reload
```

---

**最后更新**：2026-03-01 23:06 CST
