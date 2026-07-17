# 设置与语义搜索

## 基本原则

关键词搜题、题库浏览、刷题、账号、收藏和错题完全本地运行。语义搜索是可关闭的增强层；外部模型不可用时，keyword 和全部学习功能继续工作。

## 权限与存储

- 网站与模型设置：`state/settings.sqlite3`；
- API 密钥加密 key：`state/settings.key`；
- 管理写接口：管理员登录会话 + CSRF；
- 登录前公共站点接口：`GET /api/site` 只返回站点外观、登录背景版本/尺寸和注册开关，不返回模型配置或密钥掩码；
- 登录后设置接口：`GET /api/settings` 返回无秘密的站点、检索、模型配置状态与掩码；
- 普通用户：不能看到管理入口，调用管理 API 返回 403；
- 管理员入口：只在管理员的“我的 → 常用功能”首位显示，普通用户不渲染；顶栏不再放置管理入口或头像菜单，而是依次提供持久化日间/夜间主题按钮和信封消息按钮；账号与安全集中在“我的 → 设置”，退出登录固定在“我的”页底部；首次管理员创建并进入应用后立即按响应中的 `admin` 角色同步入口；
- 消息中心：系统通知、私信对话、回复与@、收到赞同使用一行四项标签切换，下方只渲染当前分类内容；
- 个人中心：修改用户名使用当前账号的唯一约束，头像可选择统一预设或上传裁剪图片，密码修改必须验证当前密码并撤销既有会话；常用功能与其他功能统一使用四列同风格图标网格。
- 个人中心：预设头像使用 `public/avatars/preset-*.svg` 统一风格资源；本地图片在浏览器中拖动/缩放裁剪为正方形 WebP 后上传，服务端再次校验并转码为 512×512 WebP，存入 `state/app.sqlite3.user_avatars`。错题、收藏、笔记、评论和点赞入口只保留在题库顶部。
- 本地 state 不上传生产，生产独立创建数据库和 key。

AI 学习助手使用同一个 `state/app.sqlite3` 保存账号隔离的 `ai_conversations` 与 `ai_messages`。会话可用 `context_occurrence_id/context_collection_id` 绑定一条原题；答案解析解锁后，刷题页在原题上方打开桌面右侧/手机底部抽屉，同一 occurrence 优先恢复最近绑定会话，不改变 hash、题号、作答草稿或背景滚动，且打开本身不发起模型请求。发送消息时服务端从加密设置库读取已保存的 LLM 配置，检索最多 6 条题库材料后请求 OpenAI-compatible 流式聊天端点。`POST /api/ai/conversations/<id>/messages/stream` 与 `POST /api/ai/conversations/<id>/regenerate/stream` 使用 `start/delta/complete/error` SSE，并返回 `private, no-store`、`X-Accel-Buffering: no` 与 `Vary: Cookie`；若上游忽略 `stream=true` 返回普通 JSON，则把完整回答作为一个 delta 兼容。

流式 delta 在完成前只存在于当前请求和浏览器临时气泡中。正常结束且会话未发生并发更新时，服务端才原子保存一对 user/assistant；停止、断连、上游失败、空回答或并发冲突不会写入部分消息。重新生成保留原 assistant ID，并要求它仍是最后消息且旧内容/引用一致。浏览器停止后必须取消 reader、释放读取锁、删除临时气泡并恢复完整问题草稿。未配置或上游失败返回安全的 `ai_unavailable`，不会返回密钥或供应商原始响应。会话列表、新建、重命名、删除和消息读写分别受登录会话与 CSRF 保护。

API URL 不允许包含用户名、密码、query 密钥或 fragment。供应商错误正文不得原样透传。聊天、截图或日志中出现过的密钥必须先在供应商后台轮换，再通过 HTTPS 设置页录入。

## 管理页面

管理员可以配置：

- 站点名称、品牌短字、副标题和网页说明；
- 登录页眉题、主标题、介绍、三条能力文案和背景图片；
- 是否开放注册；
- 默认搜索结果数；
- 是否启用语义搜索；
- 默认 keyword / semantic / hybrid；
- 嵌入基础网址、模型、维度和密钥；
- 大模型基础网址、模型和密钥；
- 用户创建、筛选、分页、单项/批量启停、角色状态和永久删除；
- 用户角色调整、密码重置和既有会话撤销；
- 全站评论管理（筛选、分页、查看来源、单项/批量永久删除）；
- 论坛管理（筛选、分页、单项/批量隐藏、恢复、软删除、永久删除和不可变审核记录）；
- catalog、账号、练习和向量运行概览。

管理页不重复显示“系统管理 / 管理控制台”等宣传性页头，只保留返回、当前栏目、刷新、分组导航和当前任务所需内容。进入时仅加载当前栏目，其他栏目首次切换时按需读取并缓存；刷新失败保留上次成功数据，显示 stale/error 与重试入口，请求 generation/token 防止旧响应覆盖新页面。账号、评论和论坛列表换筛选条件、翻页或切换栏目时会清空已选目标，删除当前页最后一项后自动回到最后有效页。桌面导航按运行配置、内容用户、智能能力分组，手机端多列换行，不使用横向滚动栏。

## 通用站点外观

新状态库使用面向通用题库的中性默认文案。`app_settings` 保存纯文本字段，`site_assets` 保存登录背景；管理员输入不作为 HTML 渲染。文本编辑器按品牌、登录页内容和站点行为分区，提供字符计数、右侧实时预览、清晰的未保存状态和“回填默认文案后再次保存”流程。浏览器标题、meta description、登录品牌、登录文案和站内顶栏品牌始终使用同一配置。

登录背景管理端点为 `POST/DELETE /api/admin/settings/login-background`，要求管理员与 CSRF。上传只读取 multipart 的 `background` 文件，不接受任意 URL；允许 JPG、PNG、WebP，最大 8 MB、单张静态图片、至少 640×320 且最多 3200 万像素。服务端执行 EXIF 方向校正、去元数据、最长边缩放到 2560，并以 WebP 重新编码后写入 `state/settings.sqlite3.site_assets`。公开图片端点使用内容 SHA-256 作为 ETag/版本并可长期缓存；删除 BLOB 即恢复默认渐变。SVG、动态图、不可识别内容和超限图片全部拒绝。

## 批量管理边界

- `PATCH /api/admin/users/bulk`：1–100 个唯一 `user_ids`，动作仅限 `enable`、`disable`、`set_role`。当前管理员不能出现在批次中；一次事务模拟全部结果并保证仍有至少一位 `active admin`，停用账号同时撤销其会话。密码重置只保留单项操作。
- `DELETE /api/admin/users/<id>` / `DELETE /api/admin/users/bulk`：单项确认必须精确匹配目标用户名，批量要求 `DELETE_SELECTED_USERS`；二者都排除当前管理员并保护最后一位有效管理员。一个 `BEGIN IMMEDIATE` 事务按依赖顺序删除会话/头像、练习会话和进度、收藏/错题/笔记、题目评论/反应、关注/遗留好友关系、私信/通知、AI 会话、论坛主题/回复/反应、反馈，最后删除 `users`。用户创建的主题会连同回复清除，用户在其他主题的回复只清除自身并解除子回复引用；审核日志中的该管理员身份改为“已注销管理员”。
- `DELETE /api/admin/comments/<id>`：要求 `DELETE_COMMENT`；与批量接口一样执行物理删除并清理反应、通知和父引用。
- `DELETE /api/admin/comments/bulk`：1–100 个唯一 `comment_ids`，必须传 `confirmation=DELETE_SELECTED_COMMENTS`。全部目标存在后，事务内删除反应/通知、清空子回复父引用并删除评论；并发变化或缺失目标使整批回滚。
- `PATCH /api/admin/forum/content/bulk`：1–100 个唯一 `{target_type,target_id}`，共享动作与 2–500 字原因；删除还要求 `DELETE_SELECTED_FORUM_CONTENT`。恢复时先处理主题再处理回复，删除时先处理回复；每项复用单项状态机并写审核记录，任一非法状态使内容变化和日志全部回滚。
- `DELETE /api/admin/forum/content/<type>/<id>` / `DELETE /api/admin/forum/content/bulk`：要求 2–500 字原因以及 `PURGE_FORUM_CONTENT` / `PURGE_SELECTED_FORUM_CONTENT`。主题清除会删除其全部回复、主题/回复反应和关联通知；单回复清除会解除子回复父引用。物理删除后写 `action=purge/new_status=purged` 的无正文审计元数据；旧审核表在初始化时事务迁移以兼容 `purge` 动作。

## 默认嵌入配置

```text
基础网址: https://api.siliconflow.cn/v1
实际嵌入端点: https://api.siliconflow.cn/v1/embeddings
模型: Qwen/Qwen3-Embedding-0.6B
维度: 1024
单位: 一道规范题
文本: 题型 + 题干 + 有序选项
排除: 答案 + 考点 + 解析
```

A3/A4 子题各自生成向量，保留完整病例题干，不跨题拼接。文件路径和原题号只用于来源追溯，不重复构建向量。

## 构建流程

1. 管理员在设置页保存已轮换的嵌入配置；
2. 执行连接测试，确认返回 1024 维有限向量；
3. 在服务器部署镜像中构建并激活索引：

```bash
cd /srv/projects/medical-web-server/repo
PYTHONPATH=app/backend OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python3 scripts/build_vectors.py \
  --knowledge-base-root /srv/projects/medical-web-server/knowledge-base \
  --settings-db /srv/projects/medical-web-server/state/settings.sqlite3 \
  --batch-size 32 --timeout 120 --max-retries 8 --activate
```

管理员也可以在“模型管理”页点击“构建向量索引”。请求只创建后台任务，不占用当前网页请求；任务状态写入 `state/vector-operation.json`，进度可跨页面恢复。任务默认支持续建和增量更新：同 API URL、模型、维度和文本版本下，按 `question_id` 复用旧 manifest 的行，只为新增或变化题目调用嵌入接口。删除按钮必须经过确认，只删除 `vector-current.json` 与 `vector-indexes/`，不会删除 catalog、原始数据、账号、评论或笔记。

产物：

```text
knowledge-base/
  vector-indexes/<vector_id>/
    embeddings.npy
    question_ids.json
    build-state.json
    manifest.json
  vector-current.json
```

构建中断后可从 `next_index` 继续。激活前检查题目 ID、矩阵形状、有限值、单位范数和文件哈希。

## 检索模式

- `keyword`：题号、精确片段、FTS5 与结构化过滤；
- `semantic`：查询嵌入与 mmap 矩阵的归一化点积；
- `hybrid`：关键词与语义候选的 RRF 融合，精确题号保持优先。

题型筛选使用 catalog 的实际来源题型标签。前端标签、`question_types` 请求数组、关键词过滤和 semantic/hybrid 候选收敛都使用同一规范化 raw type；例如只选择 `单选题` 不会返回 `A1型题`、`A2型题` 等其他标签，若需要这些细分题型可分别多选。空数组表示不限制题型。

搜索题型、搜索范围和显示内容三组的第一个标签固定为“全部”。默认只显示“全部”选中，所有未勾选的具体标签都使用白色中性背景；从“全部”状态点击某个具体标签会切换为仅该标签，之后可继续多选；重新选择“全部”恢复该组全部选项的全选视觉状态。未选中的搜索模式也保持白色中性背景，只有当前选中标签使用对应彩色。搜索设置工具栏提供“重置”和“收起/展开”两个操作，重置恢复关键词模式、三个“全部”状态和展开状态；“全部”只负责前端状态，不会作为题型或字段值发送，题型全选时请求使用空数组表示不限制。默认值会写回浏览器偏好，当前已有查询会按新设置重新检索。

搜索结果的选项、考点还原和标准解析正文统一使用黑色字色。keyword 模式使用经过 HTML 转义和正则转义的查询词，在题干、选项、答案、考点还原和标准解析中以珊瑚红高亮；semantic / hybrid 不进行关键词高亮。

搜索标签只更新筛选偏好和已有查询结果，不主动调用搜索框 `focus()`；手机端只有用户直接点击输入框时才打开输入法。

模型管理中的嵌入模型和大语言模型使用两个独立表单，各自拥有基础网址、派生端点预览、模型下拉、测试连接和保存配置按钮。基础网址拒绝内嵌用户名/密码、query 和 fragment；只输入域名时补 `/v1`，输入已有 `/models`、`/embeddings` 或 `/chat/completions` 完整端点时先规范化回基础网址，再派生模型列表、嵌入和聊天实际地址。数据库和向量 manifest 继续保存最终实际请求 URL，确保既有向量兼容校验不含糊。API 密钥输入框在停顿约 700ms 后调用管理员临时模型发现接口；临时请求可以使用尚未保存的密钥，服务端不将该密钥写入设置数据库、响应、日志或 manifest。模型列表通过派生的 `<base>/models` 获取，并按 embedding/rerank 能力标记分别过滤。

语义索引必须匹配当前 catalog ID、题目 ID 哈希、API URL、模型和维度。任一不匹配都返回明确的 unavailable 状态，不自动使用错误索引。

每次进入搜题页都会重新读取公共设置与向量状态；索引完成原子激活后，用户无需重新登录即可看到可用的语义和混合模式。

## 4 核 4G 资源预算

- 64,078 × 1024 × float32 约 250 MiB；
- NumPy mmap 加载；
- 3 个同步 Gunicorn worker；
- preload；
- 每 worker 单线程 BLAS；
- 查询向量使用小型进程内 LRU；
- 不部署 Milvus、Qdrant、Elasticsearch 等独立服务。

## 验收

真实向量完成后核对：

- 64,078 行、1024 维、float32；
- 所有值有限且行向量单位范数；
- catalog ID、API URL、模型和维度完全匹配；
- keyword、semantic、hybrid 代表查询；
- 零结果与语义关闭边界；
- 查询延迟、Gunicorn RSS、并发查询和日志；
- 管理读取始终只显示密钥掩码。

当前聊天中出现过的 SiliconFlow 密钥不得使用。真实构建必须等待用户轮换后从设置页录入；构建时只读取加密设置库，不在命令、日志或 manifest 中输出密钥。
