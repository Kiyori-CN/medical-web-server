# medical-web-server

面向本地题库的医学搜题与刷题服务。v2 把 Excel/Word 原始文件构建为只读 SQLite catalog，以 FTS5 提供默认检索，以具体源文件和原题号顺序组织刷题，并把练习进度、网站设置和可选语义索引放在独立持久化目录。

核心搜题、题库浏览和刷题不依赖第三方登录、远程业务数据库或外部数据接口。大模型和嵌入 API 是管理员可配置的可选能力，停用或故障时关键词搜索与刷题仍可工作。

## 当前真实数据

2026-07-14 已完成 catalog schema v4 构建与本地验收：

- 2,127 个源文件和 2,127 个精确文件题集；
- 123,564 条来源记录；
- 64,078 道规范题、67,650 个内容版本；
- 1,443 个 A3/A4 病例题组、3,732 个题组成员；
- 4 个答案冲突，均保留在冲突报告中；
- 64,078 条 FTS 记录，SQLite 完整性 `ok`，外键错误 0。

当前激活 build 为 `v4_dffb09373b9e0b7fca56`。真实数据、数据库、状态、日志、API 密钥和索引均被 Git 忽略；旧 v1 知识库和旧 build 保持只读并作为独立回滚点。

## 功能

- 精确题干、原题号、关键词和结构化过滤；
- 可选 semantic / hybrid 搜题；
- 按原文件夹 → 具体文件 → 原题号目录浏览；
- 从指定题号顺序刷题或随机抽取 20 题；
- 正确、错误、未做、主观题待核对状态及活动会话恢复；
- 提交前隐藏答案和解析，提交后才返回当前 occurrence 的原文件答案；
- 设置页管理浏览器偏好、嵌入模型和第三方大模型；
- 390px 手机单栏和桌面题目/题号双栏响应式布局。

## 权威工作流

```text
D:\10_Project\medical-web-server 本地 main
  -> 构建 / 测试 / 浏览器验收
  -> GitHub main 精确提交
  -> 云服务器部署镜像快进
  -> catalog / vector / state 独立受控迁移
  -> projectsctl 发布代码
  -> 生产健康、搜题、刷题、设置和语义验收
```

- GitHub：`https://github.com/Kiyori-CN/medical-web-server`
- 生产站点：`https://medical.newapi.com.cn/`
- 服务：`medical-exam.service`，端口 `8080`
- 持久化题库：`/srv/projects/medical-web-server/knowledge-base`
- 持久化状态：`/srv/projects/medical-web-server/state`

服务器仓库只是部署镜像，不能作为开发源；生产 runtime 不能直接编辑。

## 仓库结构

```text
app/backend/medical_v2/  catalog、搜题、精确文件刷题、设置和向量内核
public/                  响应式单页前端
scripts/                 catalog、practice、vector 幂等构建工具
tests/                   脱敏 fixture 与数据/API/安全/迁移测试
docs/                    开发、数据、设置语义和部署规范
data/                    本地原始题库；只读输入，已忽略
knowledge-base/          catalog 和向量 build；已忽略
state/                   practice、settings、key、日志和备份；已忽略
```

## 本地启动

```powershell
cd D:\10_Project\medical-web-server
git status --short --branch
git fetch origin --prune
git merge --ff-only origin/main

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_practice.py

$env:FLASK_ENV = "development"
$env:LEGACY_SEARCH_ENABLED = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

访问 `http://127.0.0.1:5001/`。开发环境默认管理员口令仅为 `local-admin`；生产必须通过 `ADMIN_TOKEN` 设置独立高强度随机值。

如需语义搜索，先轮换并在设置页录入嵌入密钥，确认连接后执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_vectors.py --activate
```

默认嵌入配置为 SiliconFlow OpenAI-compatible endpoint、`Qwen/Qwen3-Embedding-0.6B`、1024 维。64,078 × 1024 × float32 矩阵约 250 MiB；4 核 4G 生产环境采用 mmap、3 个同步 Gunicorn worker、每 worker 1 个 BLAS 线程，不运行独立向量数据库服务。

## v2 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | catalog、旧兼容层和向量状态 |
| `GET` | `/api/v2/stats` | 题目、来源、题组、文件和冲突统计 |
| `POST` | `/api/v2/search` | keyword / semantic / hybrid 结构化搜题 |
| `GET` | `/api/v2/library?parent=...` | 原目录分层浏览 |
| `GET` | `/api/v2/collections/<id>` | 精确源文件题集信息 |
| `GET` | `/api/v2/practice/collections/<id>/outline` | 原题号目录、进度和活动会话 |
| `POST` | `/api/v2/practice/sessions` | 从题号开始或随机创建练习 |
| `GET` | `/api/v2/practice/sessions/<id>` | 恢复会话，未提交题仍隐藏答案 |
| `POST` | `/api/v2/practice/sessions/<id>/answers` | 按 occurrence 原版本判分并返回解析 |
| `GET` | `/api/v2/practice/wrong` | 规范题级本地错题记录 |
| `GET` | `/api/v2/settings` | 无秘密的公共设置与索引状态 |
| `GET/PUT` | `/api/v2/admin/settings` | 管理员模型设置 |
| `POST` | `/api/v2/admin/settings/test` | 管理员供应商连接测试 |

搜题示例：

```json
{
  "query": "2026N1A 属于神经-体液调节的是",
  "mode": "keyword",
  "limit": 10,
  "filters": {"exam": "专硕考研(306临床医学综合能力)", "year": 2026},
  "include_solution": false
}
```

## 验证

```powershell
git diff --check
.\.venv\Scripts\python.exe -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
.\.venv\Scripts\python.exe -m compileall -q app\backend\medical_v2 scripts
node --check public\app.js
.\.venv\Scripts\python.exe -m pytest
```

数据库或前端改动还必须验证真实 build 统计、答案隐藏、指定题号、刷新恢复、桌面/手机布局以及日志。

## 文档

- [本地开发工作流](docs/local-development.md)
- [数据与索引架构](docs/data-architecture.md)
- [设置与语义搜索](docs/settings-and-semantic-search.md)
- [发布与部署](docs/deployment.md)
- [项目固定事实](PROJECT.md)
- [Agent 约束](AGENTS.md)
