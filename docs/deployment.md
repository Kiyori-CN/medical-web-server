# 发布与部署

## 权威链路

```text
本地 main -> GitHub main -> 服务器部署镜像 -> 发布包 -> projectsctl -> 生产
```

只有已推送到 GitHub 的精确提交可以部署。生产运行目录和持久化数据不能成为源码来源。

## 发布前

本地执行完整验证并记录提交哈希：

```powershell
git status --short --branch
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
git rev-parse HEAD
```

确认 `data/`、数据库、向量、状态、日志和 `.env` 没有进入提交。

若本次包含设置或语义功能，还要确认生产环境准备了：

- 长随机 `ADMIN_TOKEN`；
- `SETTINGS_DB=/srv/projects/medical-web-server/state/settings.sqlite3`；
- `SETTINGS_KEY_PATH=/srv/projects/medical-web-server/state/settings.key`；
- `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`NUMEXPR_NUM_THREADS=1`。

真实供应商密钥不得进入 `/etc/medical-exam.env` 变更补丁、命令输出、发布包或 Git；代码上线后由管理员通过 HTTPS 设置页录入。

## 服务器预检

所有服务器操作使用：

```powershell
C:\Users\admin\.codex\skills\tencent-cloud\scripts\tencent-cloud.ps1
```

部署前记录：

- 服务器部署镜像分支、HEAD、上游和工作区状态；
- `medical-exam.service` 状态和当前 runtime；
- `/api/health` 结果；
- 磁盘空间；
- `/srv/projects/medical-web-server/knowledge-base` 与 `state` 存在且未被发布包包含；
- 当前可回滚 release。

部署镜像必须干净，只能执行 fetch 和 fast-forward 到指定 GitHub 提交。若服务器存在未知改动，停止部署并报告，不得覆盖。

## 发布包

从服务器部署镜像的精确提交生成独立 tar.gz。发布包至少必须包含：

```text
app/backend/main.py
public/index.html
```

发布包不得包含：

```text
data/
knowledge-base/
state/
.env*
.git/
日志、数据库、索引、缓存、证书和备份
```

检查归档内容后只能通过以下命令部署：

```bash
projectsctl update medical-web-server <artifact.tar.gz>
```

不得直接覆盖 `app/current`，不得手工复制单个生产文件。

## 部署后验收

```bash
projectsctl health medical-web-server
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS -X POST http://127.0.0.1:8080/api/v2/search \
  -H 'Content-Type: application/json' \
  --data '{"query":"2026N1A 属于神经-体液调节的是","limit":3,"include_solution":false}'
```

随后验证：

- `https://medical.newapi.com.cn/` 可访问；
- 题库统计与部署前一致；
- 至少一次精确搜题和一次无结果搜索；
- 至少一次随机/指定题集刷题、提交答案和结果展示；
- 至少一次按目录进入具体文件、从指定原题号开始和刷新恢复；
- 答题前响应未泄露答案；
- 设置公共接口不含明文密钥、无 token 的管理接口返回 401；
- 服务日志没有新增错误。

## 数据发布

题库快照和 build 不是普通代码发布的一部分。数据迁移需要单独授权、校验、备份和回滚方案，并写入 `/srv/projects/medical-web-server/knowledge-base/v2` 的新版本目录。不得通过 GitHub 发布真实题库。

### v2 数据迁移顺序

1. 在本地从只读原始数据构建并激活目标 build，记录 snapshot、build ID、文件 SHA-256、统计、冲突报告和完整性结果。schema v4 应为 2,127 collections、64,078 questions/FTS、123,564 occurrences、67,650 versions、1,443 groups、3,732 members、4 conflicts、完整性 `ok`、外键 0。
2. 只打包目标 build 的 `catalog.sqlite3`、`manifest.json`、`import-report.json` 和 `conflict-report.json`；不得打包原始 `data/`。
3. 在服务器记录磁盘空间、旧 v1 知识库、现有 `knowledge-base/v2/current.json`、`state` 和当前 release；若存在状态库先做一致性备份。
4. 通过 Tencent Cloud wrapper 上传到临时路径，核对归档和数据库哈希后，安装到新的 `knowledge-base/v2/builds/<build_id>`；不得覆盖同名未知目录。
5. 停止可能写入状态库的服务，先用 SQLite online backup 备份 `/srv/projects/medical-web-server/state/practice.sqlite3`，再初始化或升级到 practice schema v2；保持状态库与题库分库并核对会话/答题计数。
6. 对服务器上的新数据库执行完整性、外键、题量、题组、FTS、代表性搜索和答案隐藏检查。
7. 原子写入 `knowledge-base/v2/current.json`，再部署读取该 build 的精确代码提交并重启服务。
8. 完成直接端口和公网的搜题/刷题验收；在回滚窗口内保留旧 build、v1 知识库和状态备份。

数据包约 500 MB，必须先确认服务器空间足够容纳临时上传、解包、新 build、旧 build 和代码 release。数据包只进入持久化题库目录，不得进入 Git 仓库或代码发布包。

### 设置初始化

代码首次启动会在持久化 `state` 下创建 `settings.sqlite3` 和 `settings.key`。部署前备份已存在的 settings DB/key；部署后核对目录归属以及 key 权限 `600`。生产管理员通过 HTTPS 设置页录入已轮换的嵌入/大模型密钥，公共接口只能显示配置状态和掩码。

不得把本地 `state/settings.sqlite3` 或 `settings.key` 上传到生产：这会混淆环境、复制秘密并破坏独立加密边界。

### 向量索引迁移

向量数据是独立迁移，不随 catalog 包或代码包发布：

1. 本地使用目标 catalog 和最终 API URL/模型/维度执行 `scripts/build_vectors.py --activate`。
2. 核对 vector manifest、题目 ID 哈希、64,078 行矩阵、1024 维、单位范数和约 250 MiB 文件大小。
3. 记录服务器旧 `vector-current.json` 并确认磁盘；只上传目标 `vector-indexes/<vec_build_id>` 的 manifest、ID 和矩阵，不上传 raw data、settings 或密钥。
4. 在服务器临时目录复核每个文件哈希、matrix shape、有限值和单位范数，再安装到新的不可变 vector build 目录。
5. 生产设置页配置与索引完全相同的 API URL、`Qwen/Qwen3-Embedding-0.6B` 和 1024 维，然后原子切换 `vector-current.json`。
6. 验证 `keyword`、`semantic`、`hybrid`、零结果、配置不兼容拒绝、延迟、RSS 和日志。

4 核 4G 生产使用 3 个同步 Gunicorn worker、`preload_app`、mmap 和单线程 BLAS；不得额外部署独立向量数据库服务。若内存或延迟不达标，先关闭语义开关并保留 keyword/刷题，再切回旧 vector 指针。

## 回滚

代码回滚使用 `projectsctl` 已记录的上一 release。数据回滚通过 `current.json` 切回已验证 build；用户状态数据库必须先备份且不得随代码回滚覆盖。

如果 v2 首次上线失败，优先回滚代码 release，并恢复迁移前的 catalog `current.json` 与 vector `vector-current.json`；旧 `/knowledge-base/西医题库` 和 v1 索引保持原状，因此无需反向转换数据。若只发生代码故障，不回滚或覆盖 `practice.sqlite3`、`settings.sqlite3` 或 `settings.key`。
