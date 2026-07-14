# medical-web-server

本地化医学搜题与刷题服务。v2 将 Excel/Word 原始题库构建为只读 SQLite 目录，以 FTS5 提供默认检索，并把刷题进度写入独立状态库。核心功能不依赖第三方登录、远程数据库或外部数据接口。

## v2 状态

2026-07-14 本地真实快照已完成 catalog schema v3 构建与验收：

- 2,127 个源文件、123,564 条来源记录；
- 64,078 道规范题、67,650 个内容版本；
- 2,126 个题集、1,443 个 A3/A4 病例题组；
- 4 个答案冲突，均保留在冲突报告中；
- SQLite 完整性检查通过，外键错误为 0。

旧 `ikbs_pysearch` 接口在迁移期保留为兼容层，旧生产知识库保持只读并作为回滚点。v2 的事实源是规范化 SQLite；向量检索不是核心启动条件，也不替代题库事实。

## 标准工作流

```text
本地 Windows 工作区
  -> 构建/测试/浏览器验收
  -> GitHub main
  -> 服务器部署镜像快进到精确提交
  -> 持久化 v2 数据迁移（独立审批与回滚）
  -> projectsctl 发布代码
  -> 生产健康、搜题和刷题验收
```

- 本地工作区：`D:\10_Project\medical-web-server`
- GitHub：`https://github.com/Kiyori-CN/medical-web-server`
- 生产站点：`https://medical.newapi.com.cn/`
- 服务：`medical-exam.service`，端口 `8080`
- 持久化题库：`/srv/projects/medical-web-server/knowledge-base`
- 持久化状态：`/srv/projects/medical-web-server/state`

## 仓库结构

```text
app/backend/medical_v2/  导入、规范化、SQLite/FTS、搜题与刷题内核
public/                  唯一前端源码和发布静态入口
scripts/                 幂等构建与状态库初始化命令
tests/                   脱敏 fixture、数据/API/练习/入口测试
docs/                    开发、数据和部署规范
data/                    本地原始题库；已忽略
knowledge-base/          版本化构建；已忽略
state/                   本地刷题状态；已忽略
```

## 本地开发

```powershell
cd D:\10_Project\medical-web-server
git status --short --branch
git fetch origin --prune
git merge --ff-only origin/main

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 从 data/ 生成独立 build，通过校验后切换 current.json
.\.venv\Scripts\python.exe scripts\build_catalog.py --activate

# 创建或升级独立刷题状态库
.\.venv\Scripts\python.exe scripts\init_practice.py

$env:FLASK_ENV = "development"
$env:LEGACY_SEARCH_ENABLED = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

访问 `http://127.0.0.1:5001/`。重复执行构建命令会按文件路径、大小和 SHA-256 复用同一 snapshot，不会重复导入或修改源文件。`--force` 只用于明确重建同一 build。

## v2 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | v2 核心健康及 v1 兼容层状态 |
| `GET` | `/api/v2/stats` | 题目、题集、来源、版本和冲突统计 |
| `POST` | `/api/v2/search` | FTS/精确搜题及结构化过滤 |
| `GET` | `/api/v2/questions/<id>` | 题目详情 |
| `GET` | `/api/v2/collections` | 题集列表 |
| `GET` | `/api/v2/collections/<id>/questions` | 题集题目 |
| `POST` | `/api/v2/practice/sessions` | 创建练习，响应隐藏答案与解析 |
| `POST` | `/api/v2/practice/sessions/<id>/answers` | 提交并返回判定/解析 |
| `GET` | `/api/v2/practice/wrong` | 本地错题记录 |

搜索请求示例：

```json
{
  "query": "2026N1A 属于神经-体液调节的是",
  "limit": 10,
  "filters": {"exam": "专硕考研(306临床医学综合能力)", "year": 2026},
  "include_solution": true
}
```

## 验证

```powershell
git diff --check
node --check public\app.js
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
.\.venv\Scripts\python.exe -m compileall -q app\backend\medical_v2 scripts
```

真实数据、SQLite、状态、日志、秘密和发布包永远不进入 Git。

## 文档

- [本地开发工作流](docs/local-development.md)
- [数据与索引架构](docs/data-architecture.md)
- [发布与部署](docs/deployment.md)
- [项目固定事实](PROJECT.md)
- [Agent 约束](AGENTS.md)
