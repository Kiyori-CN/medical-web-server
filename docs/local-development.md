# 本地开发工作流

## 原则

`D:\10_Project\medical-web-server` 是唯一开发工作区。所有源码、文档、测试和迁移工具都在本地完成；GitHub `main` 是唯一发布源；服务器只做部署、运行和生产验收。

这一模式适合本项目，因为题库和数据库都是本地文件，不依赖第三方登录或远程业务数据接口。它也让 Windows 上的 Excel/Word 数据处理和离线测试更直接。

## 开始任务

```powershell
cd D:\10_Project\medical-web-server
git status --short --branch
git fetch origin --prune
git merge --ff-only origin/main
```

如果存在未提交修改，先确认归属并保留。`data/` 是用户本地题库，不得清理、覆盖或提交。

## Python 环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

本地配置放在被忽略的 `.env.local` 或环境变量中，不要修改 `.env.example` 来保存个人路径或秘密。

开发环境的设置页管理员口令默认为 `local-admin`，只用于本机。供应商密钥必须先在控制台轮换，再通过设置页录入；不得粘贴到 PowerShell 命令、测试、文档或 Git 文件。

## 改动边界

一次改动应只承担一个可验证目标，例如：

- 一个导入器或 schema 版本；
- 一组数据质量规则；
- 一类搜索接口；
- 一个刷题闭环；
- 一次前端交互改进。

不要在同一个提交中同时重写数据模型、搜索算法、前端和部署脚本。

## 本地验证

构建并启动 v2：

```powershell
.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_practice.py
$env:FLASK_ENV = "development"
$env:LEGACY_SEARCH_ENABLED = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

`build_catalog.py` 只读扫描 `data/`，输出 manifest、导入报告和冲突报告；只有构建数据库通过完整性和外键检查后，`--activate` 才更新 `current.json`。

schema v4 把每个具体 Excel/Word 文件作为独立 collection。`init_practice.py` 会把旧 practice schema v1 原位迁移到 v2；运行前应停止本地服务，并使用 SQLite backup API 备份状态库，不能只复制一个仍在写入的数据库文件。

需要语义搜索时，启动网站后在设置页保存并测试嵌入配置，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_vectors.py --activate
```

该命令从加密设置库读取密钥，按题目批量构建可恢复 mmap 索引，不在 stdout、manifest 或 Git 中写入密钥。中断后重复运行会继续未完成批次；改变 catalog、API URL、模型或维度会产生新的 vector build。

最低检查：

```powershell
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
python -m compileall -q app\backend\medical_v2 scripts
node --check public\app.js
python -m pytest
```

功能实现后应补充：

- 单元测试：规范化、题型、答案和选项处理；
- 数据测试：幂等导入、去重、冲突、来源关系和统计对账；
- API 测试：搜索、题集、题目详情、刷题提交和答案隐藏；
- 安全测试：管理员鉴权、密钥加密、公共/错误响应不泄密；
- 向量测试：恢复、复用、维度、catalog 兼容和 semantic/hybrid；
- 前端验收：桌面和移动端的目录、指定题号、提交、刷新恢复、设置和搜题完整流程。

测试只能使用小型脱敏 fixture。不得把真实题库复制到 `tests/` 或提交 GitHub。

真实数据验收至少核对：2,127 个 source/collection、123,564 occurrences、64,078 questions/FTS、67,650 versions、1,443 groups、3,732 members、4 conflicts、`integrity_check=ok`、外键 0。再抽查 `专硕考研（306临床医学综合能力）/历年真题/2026/2026.xlsx` 的 165 个题号、从指定题号出题和 occurrence 原版本判分。

## 提交与推送

只有用户明确要求时才提交和推送：

```powershell
git status --short --branch
git diff --check
git diff --stat
git add <明确文件>
git commit -m "<明确说明>"
git push origin main
```

不要使用 `git add .` 无差别加入本地文件。推送后记录完整提交哈希，部署必须绑定该哈希。

## 服务器边界

服务器上的 `/srv/projects/medical-web-server/repo` 只是部署镜像。部署时可以快进、归档和验证，但不能在那里进行常规开发。生产题库、状态、环境文件和日志不随 Git 更新。

部署流程见 `docs/deployment.md`。
