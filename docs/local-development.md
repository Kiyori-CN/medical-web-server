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
- 前端验收：桌面和移动端的搜题及刷题完整流程。

测试只能使用小型脱敏 fixture。不得把真实题库复制到 `tests/` 或提交 GitHub。

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
