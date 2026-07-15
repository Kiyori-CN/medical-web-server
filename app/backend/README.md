# 后端

## 入口与模块

| 路径 | 作用 |
| --- | --- |
| `main.py` | Flask 应用、健康检查、blueprint 与静态文件 |
| `config.py` | catalog、状态、设置、Cookie 和端口配置 |
| `gunicorn_config.py` | 生产 worker、preload、超时和日志 |
| `medical_app/auth.py` | 本地账号、密码修改/重置、会话、角色与 CSRF |
| `medical_app/catalog.py` | 只读 SQLite/FTS 访问 |
| `medical_app/catalog_export.py` | 管理员 JSONL/CSV occurrence 流式导出 |
| `medical_app/catalog_import.py` | 管理员 CSV/JSONL/JSON 隔离导入预检与字段映射 |
| `medical_app/forum.py` | 本地论坛主题/回复、权限、软删除与回复通知 |
| `medical_app/practice.py` | 会话、四种刷题模式、文件进度重置、收藏、错题和评论 |
| `medical_app/settings.py` | 网站/模型设置与秘密加密 |
| `medical_app/semantic.py` | keyword、semantic、hybrid 编排 |
| `medical_app/vector_builder.py` | 规范题向量构建 |
| `medical_app/ordering.py` | 固定医学目录排序 |

## 运行边界

- 不修改原始 Excel/Word；
- 不把题目压成不可解析的文本块；
- 题库只读，用户状态独立写入；
- 练习提交前不能返回答案；
- 测试交卷前不能返回对错、正确答案、解析或实时正确率；
- 核心启动不要求外部 API；
- `/api/search` 的 `search_fields` 按题干、答案、考点和解析限制关键词字段；semantic / hybrid 要求包含题干范围。
- `/api/search` 的 `question_types` 接受多个 catalog 实际来源题型标签（例如 `A1型题`、`A3/A4型题`、`X型题`）；后端按结果徽标使用的同一规范化 raw type 精确过滤，空数组表示不限制题型。
- 管理写接口要求管理员会话和 CSRF。
- 用户修改密码验证当前密码；任何密码修改都会撤销该用户的既有会话。
- 文件进度重置只清空当前账号和当前 collection 的题号状态投影，不删除历史试卷、收藏、错题或评论。
- 评论绑定 occurrence；登录用户可发表，作者本人和管理员可删除。
- `/api/admin/catalog/export` 只允许管理员，以只读连接逐行输出当前 catalog；CSV 单元格中和公式前缀，响应不缓存且不读取用户 state。
- `/api/admin/catalog/imports` 只允许管理员把最多 16 MB/50,000 行的 CSV/JSONL/JSON 放入 `state/import-staging/` 预检；分析与删除要求 CSRF，预检不调用 builder 或激活 catalog。

## 本地启动

从仓库根执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_app.py
$env:FLASK_ENV = "development"
$env:SECURE_COOKIES = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

## 验证

```powershell
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\main.py
python -m compileall -q app\backend\medical_app scripts
python -m pytest
git diff --check
```
