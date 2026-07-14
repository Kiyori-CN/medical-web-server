# 设置与语义搜索

## 运行原则

关键词搜题、题库浏览和刷题只依赖本地 SQLite/FTS5。语义搜索是可关闭的增强层：只有管理员启用语义搜索、配置嵌入 API，且激活索引与当前 catalog 的 build、API URL、模型和维度全部一致时才可用。

4 核 4G 服务器不运行 Milvus、Qdrant、Elasticsearch 等独立服务。当前 64,078 道规范题按题目建立 `float32` 归一化矩阵；使用 1024 维时约 250 MiB，由 NumPy mmap 加载并通过操作系统页缓存跨 Gunicorn worker 共享。查询执行精确余弦扫描，BLAS 每 worker 限 1 线程。

## 设置存储与鉴权

- 普通浏览器偏好仅写入浏览器 localStorage，不包含口令或 API 密钥。
- 服务端设置写入 `state/settings.sqlite3`。
- API 密钥使用 Fernet 加密；独立 key 写入 `state/settings.key`，生产权限必须为 `600`。
- `GET /api/v2/settings` 只返回是否已配置和掩码，不返回明文。
- 管理端点要求 `X-Admin-Token`；生产 `ADMIN_TOKEN` 使用独立的高强度随机值。
- 管理员口令和密钥不写入浏览器存储；模型供应商错误响应也不得原样回显。
- API URL 不允许内嵌用户名、密码、query 参数或 URL fragment，密钥只能放在密钥字段。

聊天中、截图中或日志中出现过的密钥都视为已泄露，必须先在供应商控制台轮换，再通过设置页录入。不得把真实密钥写入命令历史、Git、文档、测试或构建 manifest。

## 默认嵌入配置

```text
API URL:   https://api.siliconflow.cn/v1/embeddings
模型:      Qwen/Qwen3-Embedding-0.6B
维度:      1024
切分单位:  一道规范题一个向量
嵌入文本:  题型 + 题干 + 有序选项
排除内容:  答案 + 考点解析
```

A3/A4 子题仍一题一个向量；题干保留原始完整病例信息，不把多道子题拼成跨题 chunk。文件路径和原题号是刷题 provenance，不作为新的向量单位。

## 本地构建

1. 启动网站，在设置页用开发管理员口令解锁。
2. 填入已轮换的嵌入 API 密钥、URL、模型和维度并保存，先执行连接测试。
3. 从当前 catalog 构建并激活可恢复索引：

```powershell
.\.venv\Scripts\python.exe scripts\build_vectors.py --activate
```

构建产物：

```text
knowledge-base/v2/
  vector-indexes/<vec_build_id>/
    embeddings.npy
    question_ids.json
    build-state.json
    manifest.json
  vector-current.json
```

每批写入矩阵后都会 flush 并原子更新进度；中断后重复命令从 `next_index` 继续。完整构建会检查题目 ID、矩阵形状、有限值和单位范数，只有 `--activate` 才原子切换指针。

## 搜索模式

- `keyword`：精确题干、原题号、FTS5，始终可用。
- `semantic`：调用已配置 API 生成查询向量，扫描 mmap 矩阵并返回余弦排序。
- `hybrid`：对关键词和语义候选做 RRF 融合，精确题干和题号结果保持强优先级。

查询向量在进程内使用小型 LRU 缓存。模型 API 故障、索引缺失或配置不兼容时，语义接口明确返回 `semantic_unavailable`，关键词功能不受影响。

## 大模型设置

第三方大模型 URL、模型和密钥使用同一套加密设置。现有 `/api/ai/chat` 优先读取该设置，仅在迁移期间回退到旧环境变量；未配置密钥时返回 503。上游错误正文不得透传给浏览器。

## 生产迁移

向量索引是独立数据制品，不能随代码包或 Git 发布。生产迁移前后必须核对：

- `vec_build_id`、catalog build ID、模型、API URL、维度和题量；
- `embeddings.npy` 与 `question_ids.json` 哈希及 manifest 校验；
- `settings.sqlite3`、`settings.key` 权限与备份；
- `vector-current.json` 原指针和回滚指针；
- keyword、semantic、hybrid 的代表查询、零结果和延迟；
- 进程 RSS、磁盘空间、并发查询以及服务日志。

旧 v1 向量库和 catalog 旧 build 在回滚窗口内保持不变。
