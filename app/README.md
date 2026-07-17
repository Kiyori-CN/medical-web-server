# 应用源码

`app/` 只保存后端源码，不保存前端副本、原始题库、数据库、向量、用户状态、日志或秘密。

```text
app/
  backend/
    main.py              Flask 入口与静态文件服务
    config.py            本地/生产配置
    gunicorn_config.py   4 核 4G 生产配置
    medical_app/         账号、题库、流式导出、搜索、刷题、设置和向量模块
```

唯一前端源码位于仓库根 `public/`，由 Flask 直接提供。

应用边界：

- 只读消费 `knowledge-base` 中激活的 catalog；
- 允许管理员把激活 catalog 按 occurrence 流式导出为 JSONL/CSV，但不修改 catalog 或读取用户 state；
- 允许管理员把 CSV/JSONL/JSON 放入 `state/import-staging/` 进行隔离预检和字段映射；该流程不构建或激活 catalog；
- 将账号、会话、可独立重置的文件刷题状态、可切换收藏、可删除错题与原题评论写入 `state/app.sqlite3`；管理员永久删除账号/评论/论坛内容时只在该状态库内按依赖执行原子清理，不触碰 catalog、向量或原始题库；
- 将网站和模型设置写入 `state/settings.sqlite3`；
- 通过独立 `state/settings.key` 加密 API 密钥；
- 所有 API 使用 `/api/*`；
- 不包含旧 TXT 搜索或兼容路由。

完整规范见根 [AGENTS.md](../AGENTS.md) 与 [数据架构](../docs/data-architecture.md)。
