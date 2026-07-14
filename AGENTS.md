# AGENTS.md

本文件适用于 `D:\10_Project\medical-web-server` 整个仓库，是后续开发、测试、发布和部署的强制约束。

## 工作区与发布源

- 唯一开发工作区：`D:\10_Project\medical-web-server`
- 唯一发布分支：`main`
- GitHub：`https://github.com/Kiyori-CN/medical-web-server.git`
- 云服务器仓库 `/srv/projects/medical-web-server/repo` 只是部署镜像，不是开发工作区。
- 生产运行目录 `/srv/projects/medical-web-server/app/current` 不是 Git 工作区，禁止直接编辑。

开始工作前必须先执行：

```powershell
git status --short --branch
git fetch origin --prune
git merge --ff-only origin/main
```

如果工作区存在用户改动，必须保留并避开；不得使用 `git reset --hard`、`git clean -fd` 或其他破坏性清理命令。

## 标准开发流程

1. 在本地 `main` 上同步远端并重新检查工作区。
2. 在本地完成代码、文档、数据导入器和测试修改。
3. 使用本地样例或脱敏测试数据验证；核心功能不得依赖第三方登录或外部数据接口。
4. 检查差异、测试结果和数据边界。
5. 只有用户明确要求时才提交和推送 GitHub。
6. 只有用户明确要求部署时，才让服务器部署镜像快进到已推送的精确提交并生成发布包。
7. 生产部署只能使用 `projectsctl update medical-web-server <artifact.tar.gz>`。

不得在服务器仓库中进行常规代码编辑，也不得把服务器临时修改作为新的发布源。

## 数据与隐私边界

以下内容永远不得提交 Git：

- `data/` 下的原始 Excel、Word 和其他题库文件
- `knowledge-base/`、`state/`、SQLite 数据库、向量、FTS 索引和构建产物
- `.env`、API 密钥、证书、日志、缓存、备份和用户刷题记录
- 生产环境文件 `/etc/medical-exam.env`
- 生产持久化目录 `/srv/projects/medical-web-server/knowledge-base` 与 `/srv/projects/medical-web-server/state`

原始题库在本地只作为只读输入。导入程序必须输出版本化 manifest、文件哈希、错误报告和冲突报告，不得原地修改源文件。

项目目标是完全本地的数据栈：核心搜题、刷题、题库筛选和用户进度不得依赖第三方登录、远程数据库或外部业务数据接口。语义检索必须允许关闭，并优先采用本地可重建索引；任何可选外部模型集成都不能成为核心功能的启动条件。

## 目标数据架构

- SQLite 是规范化题库和本地状态的首选存储；题库目录与用户状态必须分库。
- FTS5 负责精确、关键词和结构化过滤；向量索引只负责语义召回，不是事实源。
- 一道规范化题目是基本实体和搜题向量单位；文件只表示来源、题集和顺序。
- 同一题可以属于多个题集；不得因“考点顺序/乱序、年份/系统/学科刷题”重复存储题目正文。
- A3/A4 等病例串题必须保留共享题干、子题和顺序关系。
- 知识解析切分不得跨题；长解析只能在同一道题内部按段落或句子切分。
- 所有索引必须能从原始快照和规范数据库重新构建，并通过 build manifest 原子切换。
- 旧 `/srv/projects/medical-web-server/knowledge-base/西医题库` 在 v2 验证完成前保持只读并作为回滚点。

详细设计见 `docs/data-architecture.md`。

## 修改规范

- 优先做小而完整、可验证的改动；不要把数据迁移、搜索重构和 UI 重写混在一个提交中。
- 数据库 schema 变更必须带迁移或可重复构建路径。
- API 返回结构必须稳定、机器可读；题目、选项、答案、解析和来源不得继续只作为一段文本返回。
- 刷题答案在提交前不能由练习接口直接暴露。
- 新增脚本默认幂等；重复运行不得制造重复题目、重复向量或覆盖源数据。
- 新增文档时同步更新根 `README.md` 的文档索引；架构或部署约束变化时同步更新 `PROJECT.md` 和项目 skill。

## 最低验证

每次提交或部署前至少运行：

```powershell
git status --short --branch
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
python -m compileall -q app\backend\medical_v2 scripts
node --check public\app.js
python -m pytest
```

如果改动涉及测试、导入器、数据库或前端，还必须运行对应的完整测试和最小端到端流程，并在最终报告中列出实际命令和结果。不得用“已测试”代替具体证据。

## 生产验证

部署后必须依次验证：

```bash
projectsctl health medical-web-server
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS -X POST http://127.0.0.1:8080/api/v2/search \
  -H 'Content-Type: application/json' \
  --data '{"query":"2026N1A 属于神经-体液调节的是","limit":3,"include_solution":false}'
```

还应验证公网 `https://medical.newapi.com.cn/`、题库统计、一次搜题和一次刷题闭环。部署不得删除或替换持久化题库、用户状态和回滚版本。
