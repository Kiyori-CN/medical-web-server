# 发布与部署

## 权威链路

```text
本地 main
  -> 完整测试与浏览器验收
  -> GitHub main 精确提交
  -> 服务器 repo 快进到精确 SHA
  -> 发布包
  -> projectsctl
  -> 生产健康与公网验收
```

服务器源码不是开发源，运行目录不能直接编辑。所有 SSH、传输、服务和项目操作从 Windows 通过：

```text
C:\Users\admin\.codex\skills\tencent-cloud\scripts\tencent-cloud.ps1
```

## 正式目录

```text
/srv/projects/medical-web-server/
  repo/                  GitHub 部署镜像
  app/backend/           生产运行代码
  knowledge-base/        catalog 与向量
  state/                 账号、练习、设置与密钥
```

固定接口：

- systemd：`medical-exam.service`；
- systemd drop-in：`/etc/systemd/system/medical-exam.service.d/timeout.conf`，固定设置 `TimeoutStopSec=45s`；
- 端口：`8080`；
- 直接健康：`http://127.0.0.1:8080/api/health`；
- 公网：`https://medical.newapi.com.cn/`；
- 环境文件：`/etc/medical-exam.env`，权限 `600`。

Nginx 直接从 `/srv/projects/medical-web-server/public` 提供首页和静态资源，不能依赖 Flask 的 `after_request` 为这些响应补安全头。`medical.newapi.com.cn` 的 HTTPS server 必须统一设置：

- `Strict-Transport-Security: max-age=31536000`；
- `X-Content-Type-Options: nosniff`；
- `X-Frame-Options: DENY`；
- `Referrer-Policy: strict-origin-when-cross-origin`；
- 与应用一致的 `Content-Security-Policy`。

修改站点配置前先执行环境配置备份和 `nginx -t`，修改后只在全局 Nginx 配置可安全热重载时 reload，并从公网逐项核对响应头。若其他站点的活动 `proxy_cache` 共享区路径发生漂移，必须先按对应项目规范完成缓存路径迁移，不能用重建旧目录绕过问题。

## 本次全新重建

用户已明确授权删除 `/srv/projects/medical-web-server` 内的旧代码、旧数据库、旧题库、旧向量和旧状态，并重新部署当前项目；不保留旧项目回滚数据。域名、正式根目录、Nginx 入口和项目名保持不变。

执行顺序：

1. 本地完成自动化、真实数据和浏览器验收；
2. 精确提交并推送 GitHub `main`，记录完整 SHA；
3. 使用 `ProjectContext` 读取服务器项目、服务、Nginx、systemd、projectsctl 和磁盘事实；
4. 证明待删除路径只在正式项目范围内，且没有未知 mount、容器、cron 或其他项目引用；
5. 停止 `medical-exam.service`；
6. 清空旧项目内容并重建 `repo`、`app`、`knowledge-base`、`state`；
7. 从 GitHub 克隆/快进 `repo` 到精确 SHA；
8. 安装经过校验的 catalog 制品；
9. 在生产本地创建全新的 `app.sqlite3`、`settings.sqlite3` 和 `settings.key`；
10. 从精确提交生成发布包并执行 `projectsctl update`；
11. 验证直接健康、域名、catalog、账号初始化和刷题；
12. 用户通过 HTTPS 一次性初始化正式管理员；
13. 用户轮换模型密钥并通过管理设置页录入；
14. 向量完成后单独安装并验收 semantic/hybrid。

聊天中出现过的管理员密码或模型密钥不得写入命令、Git、部署包、环境文件或日志。

## 发布前检查

```powershell
git status --short --branch
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\main.py
python -m compileall -q app\backend\medical_app scripts
node --check public\app.js
python -m pytest
git rev-parse HEAD
```

确认 `data/`、`knowledge-base/`、`state/`、日志和 `.env` 未进入提交。

## 生产环境

建议环境变量：

```text
FLASK_ENV=production
KNOWLEDGE_BASE_ROOT=/srv/projects/medical-web-server/knowledge-base
APP_DB=/srv/projects/medical-web-server/state/app.sqlite3
SETTINGS_DB=/srv/projects/medical-web-server/state/settings.sqlite3
SETTINGS_KEY_PATH=/srv/projects/medical-web-server/state/settings.key
SECURE_COOKIES=true
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

API 密钥不放在环境文件中，由管理员设置页写入加密设置数据库。

## 发布包

发布包必须来自服务器部署镜像的精确提交，至少包含：

```text
app/backend/main.py
app/backend/medical_app/
public/index.html
public/style.css
public/app.js
```

必须排除：

```text
data/
knowledge-base/
state/
.env*
.git/
日志、数据库、索引、缓存、证书和备份
```

检查归档内容后执行：

```bash
projectsctl update medical-web-server <artifact.tar.gz>
```

## Catalog 安装

只传输已验证 build 的：

- `catalog.sqlite3`；
- `manifest.json`；
- `import-report.json`；
- `conflict-report.json`。

不得上传原始 `data/`。安装后核对文件 SHA-256、SQLite integrity、外键、FTS 和真实统计，再写入 `knowledge-base/current.json`。

统计基线：2,127 collections、123,564 occurrences、64,078 questions/FTS、67,650 content versions、1,443 groups、3,732 members、4 conflicts、完整性 `ok`、外键 0。

## 状态初始化

生产不得接收本地 state。运行：

```bash
python scripts/init_app.py \
  --knowledge-base-root /srv/projects/medical-web-server/knowledge-base \
  --app-db /srv/projects/medical-web-server/state/app.sqlite3 \
  --settings-db /srv/projects/medical-web-server/state/settings.sqlite3 \
  --settings-key-path /srv/projects/medical-web-server/state/settings.key
```

确认 state 归属正确，`settings.key` 权限 `600`。`settings.sqlite3` 同时持久化管理员配置的站点文案和安全重编码登录背景；常规升级不得替换、清空或从本地上传该数据库。全新 state 的 `/api/auth/setup` 应显示 `setup_required=true`，正式管理员只能通过 HTTPS 初始化页创建。

部署站点外观功能后，非交互健康检查还应确认 `/api/site` 在未登录状态返回 200 且不包含 provider、密钥或掩码字段；若生产已配置背景，只核对固定图片路由状态、`image/webp`、ETag 和缓存头，不下载或覆盖 `state/settings.sqlite3`。功能内容、预览、管理栏目按需加载和批量操作由用户在浏览器中验收。账号、评论和论坛永久删除不得以生产既有数据做烟测；只能由用户在明确创建的隔离临时账号/内容上验收。

## 向量安装

向量是独立数据制品，不随代码包或 catalog 包发布。等待用户轮换密钥并完成真实构建后：

1. 核对 64,078 × 1024、float32、有限值和单位范数；
2. 核对 vector manifest 与 catalog、API URL、模型、维度；
3. 上传 `vector-indexes/<vector_id>`；
4. 服务器复核哈希和矩阵；
5. 原子写入 `vector-current.json`；
6. 验证 keyword、semantic、hybrid、延迟、RSS 和日志。

## 部署后验收

```bash
projectsctl health medical-web-server
curl -fsS http://127.0.0.1:8080/api/health
```

登录后验证：

```bash
curl -fsS -X POST http://127.0.0.1:8080/api/search \
  -H 'Content-Type: application/json' \
  --data '{"query":"2026N1A 属于神经-体液调节的是","limit":3,"include_solution":false}'
```

最终检查：

- 公网 HTTPS 正常；
- 公网首页和静态资源返回 HSTS、nosniff、DENY、Referrer-Policy 与 CSP；
- 首次管理员初始化只有一次；
- 普通用户无管理权限；
- 真实统计与本地一致；
- 精确查询和零结果；
- 2026 文件 165 题及四个排序按钮自身重复点击：题型标准/逆标准、题号正倒、年份新旧、随机每点一次立即显示新的 165 题完整排列；创建随机会话时服务端接受并严格校验当前 `occurrence_order`，实际冻结顺序必须逐项等于屏幕预览。所选顺序贯穿会话恢复、前后切题、滑动和题号抽屉，原题号与当前进度分开显示，指定原题号开始准确；
- 练习、快刷、测试、背题四种模式；练习整卷交卷会提交所有未单独提交的浏览器草稿，快刷/测试填空题可手动保存，背题可明确完成；
- 测试答完最后一题仍保持 active，显式交卷前无答案泄露；整卷 `answers` 任一无效时服务端整体回滚；
- AI 会话按账号隔离，旧状态库兼容新增 occurrence/collection 会话绑定；未配置 LLM 时返回安全的 unavailable 状态，配置后从公网执行一次代表性 SSE 问答并确认多个 `delta` 分段按时到达、引用可用、最终刷新后只新增完整 user/assistant。公网流式响应必须是 `text/event-stream`，包含 `Cache-Control: private, no-store` 与 `X-Accel-Buffering: no`，不能被 Nginx 聚合到结束后一次返回；供应商普通 JSON 回退仍可完成。答案解析解锁后在原题上打开桌面右侧/手机底部 AI 抽屉，同题恢复绑定会话、异题隔离，打开不调用模型，关闭按钮/遮罩/Escape/手机下拉均保留题号、作答草稿、hash 和滚动位置；活动测试交卷前不显示入口。流式期间停止一次并确认服务端消息数不变、未保存半截回答、原问题草稿完整恢复；最后回答流式重新生成后 assistant ID 与消息数量不变。所有端点继续通过 CSRF、账号隔离、最后消息与旧内容/引用并发保护验收；
- 论坛分区、主题列表/搜索、发帖、编辑、楼中楼回复、`@用户名` 提及、赞同/反对及作者软删除权限在公网主路径可用；详情显示回复对象，已删除父回复不可回复，作者入口可进入公开主页，主题/回复和获赞进入个人动态/统计，论坛回复、提及与赞同进入既有消息中心且相同收件人不重复通知；管理员论坛管理可按关键词/类型/状态筛选，隐藏/恢复/软删除要求原因并写入审计，普通用户被拒绝。永久删除主题/回复只在隔离临时 state 验证固定确认、2–500 字原因、回复级联/父引用解除、反应/通知清理、批量回滚与 `purge` 审计，生产真实论坛内容不用于破坏性烟测；
- 系统管理首屏只请求当前栏目，切换后按需加载并缓存；刷新错误保留旧数据并提供重试，删除后保持筛选/页码并在空页时回退。手机端管理导航、工具栏、批量栏和危险确认弹窗不得横向溢出。账号单项/批量永久删除只在隔离临时 state 验证：当前管理员与最后有效管理员保护、用户名/`DELETE_SELECTED_USERS` 确认、全账号关联表清理、遗留 `friendships`、其他账号隔离和整批回滚；评论永久删除同样只用临时内容验证 `DELETE_COMMENT` / `DELETE_SELECTED_COMMENTS`、反应/通知/父引用清理；
- 系统管理题库导出仅对管理员可用，JSONL/CSV 响应禁止缓存与代理缓冲且不包含用户 state；普通用户调用返回 403；
- 登录用户“下载数据”调用 `/api/study/export?format=jsonl|csv` 时只返回自己的收藏、错题、笔记、进度、练习会话和逐题作答记录，响应禁止缓存与代理缓冲，CSV 公式前缀已中和；切换账号后不得看到上一账号记录；
- `study_goals` 兼容迁移完成且不改变既有练习记录；`GET/PUT /api/study/goals` 账号隔离、CSRF 和范围校验可用，今日统计以 `Asia/Shanghai` 日界返回目标进度、连续学习天数与近 7 日趋势；个人导出包含当前账号的 `study_goal` 记录；
- `practice_sessions.archived_at` 从旧状态库兼容新增且默认空值；`GET /api/study/sessions` 未登录返回 401，登录后只返回当前账号的分页练习历史，全部/进行中/已完成/已归档、`has_more`、恢复答题和复盘可用。`PUT /api/study/sessions/<id>/archive` 必须通过 CSRF、账号隔离与布尔校验；归档前后会话总数、状态、答案和进度不变，归档活动测试不返回成绩且拒绝提交/交卷。单个与批量永久删除只在隔离临时账号/临时 state 验证，覆盖 CSRF、账号隔离、未归档 409、固定批量确认和逐题级联；严禁拿生产既有练习记录做删除烟测。常规部署不得自行减少既有用户记录；
- 模型管理只输入基础网址并实时预览派生的 `/models`、`/embeddings`、`/chat/completions`；域名、`/v1` 和旧完整端点均规范化一致，凭据/query/fragment 被拒绝，生产已保存的实际嵌入 URL 与向量 manifest 继续兼容；
- 题库导入预检的 XLSX/CSV/JSONL/JSON 上传、字段映射、错误摘要和暂存删除可用；XLSX 多工作表行定位正确，损坏、加密或异常膨胀工作簿被拒绝；未登录/普通用户被拒绝，超限返回 JSON 413，预检前后 catalog ID、64,078 题与向量 manifest 不变；
- 待交卷草稿提示、未答交卷、计时、刷新恢复；
- 收藏和错题路径与删除；
- 管理设置密钥掩码；
- `390×844` 和桌面布局；
- 服务日志无新增错误；
- 停止旧服务时完成优雅退出，日志没有 `stop-sigterm timed out`、`status=9/KILL` 或 `Failed with result 'timeout'`；
- 3 worker、preload、单线程 BLAS、内存与延迟符合预算。

## 后续常规部署

全新重建完成后，常规代码部署不得删除 `knowledge-base` 或 `state`。每次部署前记录服务、健康、磁盘和当前提交；只能快进部署镜像、生成排除持久化数据的发布包并通过 `projectsctl` 更新。

用户反馈发布验收：生产状态库只新增或保留 `user_feedback` 表及其索引，不得重建或清理 `state/`；公网“我的 → 意见反馈”提交后，管理员“系统管理 → 用户反馈”可读到同一条记录并完成状态切换。停用账号不影响反馈，只有显式账号永久删除才在同一事务清除该账号反馈。部署前后保留 catalog、向量、账号和既有论坛/学习数据不变；论坛审核表兼容 `purge` 的迁移必须保留既有日志行。
