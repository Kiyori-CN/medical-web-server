# 设置与语义搜索

## 基本原则

关键词搜题、题库浏览、刷题、账号、收藏和错题完全本地运行。语义搜索是可关闭的增强层；外部模型不可用时，keyword 和全部学习功能继续工作。

## 权限与存储

- 网站与模型设置：`state/settings.sqlite3`；
- API 密钥加密 key：`state/settings.key`；
- 管理写接口：管理员登录会话 + CSRF；
- 公共设置接口：只返回非秘密配置、配置状态与掩码；
- 普通用户：不能看到管理入口，调用管理 API 返回 403；
- 管理员入口：桌面显示在顶栏，手机固定在头像左侧；头像菜单不重复显示管理入口，固定排列个人中心、我的消息、退出登录；首次管理员创建并进入应用后立即按响应中的 `admin` 角色同步入口；
- 个人中心：修改用户名使用当前账号的唯一约束，头像只能选择系统预设标识，密码修改必须验证当前密码并撤销既有会话；菜单点击外部区域或 Escape 关闭。
- 本地 state 不上传生产，生产独立创建数据库和 key。

API URL 不允许包含用户名、密码、query 密钥或 fragment。供应商错误正文不得原样透传。聊天、截图或日志中出现过的密钥必须先在供应商后台轮换，再通过 HTTPS 设置页录入。

## 管理页面

管理员可以配置：

- 站点名称；
- 是否开放注册；
- 默认搜索结果数；
- 是否启用语义搜索；
- 默认 keyword / semantic / hybrid；
- 嵌入 API URL、模型、维度和密钥；
- 大模型 API URL、模型和密钥；
- 用户创建、启停和角色状态；
- 用户角色调整、密码重置和既有会话撤销；
- catalog、账号、练习和向量运行概览。

## 默认嵌入配置

```text
API URL: https://api.siliconflow.cn/v1/embeddings
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

语义索引必须匹配当前 catalog ID、题目 ID 哈希、API URL、模型和维度。任一不匹配都返回明确的 unavailable 状态，不自动使用错误索引。

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
