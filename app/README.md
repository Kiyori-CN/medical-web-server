# 应用目录

`app/` 保存 medical-web-server 的前后端源码。它是 Git 源码的一部分，不保存原始题库、数据库、索引、用户状态、日志或秘密。

## 目录

```text
app/
  backend/       Flask API、配置、v1 兼容层和 v2 内核
```

仓库根 `public/` 是唯一前端源码和发布静态入口。Flask 本地运行与生产发布都从该目录提供静态文件，不再维护第二套 `app/frontend/` 副本。

## 当前实现

当前主界面和 `/api/v2/*` 已使用结构化 SQLite/FTS5：

- `medical_v2/` 负责只读导入、规范化、去重、病例题组、搜索和练习；
- `public/` 直接消费结构化题目，不再解析大段文本；
- 答案和解析在练习提交前不会返回；
- 本地状态存放在独立 `practice.sqlite3`。

迁移期仍兼容 v1：

- `ikbs_pysearch.py` 读取旧 `.ikbs_cache`；
- `/api/search` 支持 keyword、semantic 和 hybrid；
- `/api/random` 从旧文本块中随机取题；
- 前端解析文本块后显示题目。

这些路由只用于现网兼容和回滚，不得继续扩展为 v2 数据模型。

架构和开发规范见：

- `../docs/data-architecture.md`
- `../docs/local-development.md`
- `../AGENTS.md`

## 最低验证

```powershell
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
```

新增功能必须同时补充对应 API、数据和前端测试，不能只依赖生产环境手工验证。
