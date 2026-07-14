# 后端说明

本目录包含当前 Flask 后端和 v1 搜索兼容层。

## 当前文件

| 文件 | 作用 | v2 方向 |
| --- | --- | --- |
| `main.py` | Flask 入口、v2 blueprint 和 v1 兼容路由 |
| `config.py` | 本地/生产题库、状态库和兼容层配置 |
| `medical_v2/` | 结构化导入、SQLite/FTS、题集、搜题与刷题 |
| `ikbs_pysearch.py` | 旧文本块与向量搜索兼容层 |
| `incremental_index.py` / `update_index.py` | 旧 v1 索引维护入口 |
| `gunicorn_config.py` | 生产 WSGI 配置 |

## 边界

- 后端不得直接修改原始 Excel/Word。
- 核心启动不得要求第三方登录、远程数据库或外部业务数据接口。
- 题库是只读发布物；用户刷题状态使用独立数据库。
- API 不得把题目重新压成不可解析的大段文本。
- 练习出题响应不得提前包含答案和解析。

## 本地环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\backend\requirements.txt
```

```powershell
.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_practice.py
$env:FLASK_ENV = "development"
$env:LEGACY_SEARCH_ENABLED = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

只有显式启用 `LEGACY_SEARCH_ENABLED=true` 时，旧搜索启动才需要旧知识库路径。

## 验证

```powershell
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
git diff --check
```

完整约束见仓库根 `AGENTS.md`，数据设计见 `docs/data-architecture.md`。
