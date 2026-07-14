# medical-web-server

## 固定事实

- 本地开发工作区：`D:\10_Project\medical-web-server`
- 发布分支：`main`
- GitHub：`https://github.com/Kiyori-CN/medical-web-server.git`
- 公网入口：`https://medical.newapi.com.cn/`
- 云服务器正式根目录：`/srv/projects/medical-web-server`
- 部署镜像：`/srv/projects/medical-web-server/repo`
- 运行时：`/srv/projects/medical-web-server/app/current`
- 服务：`medical-exam.service`
- 端口：`8080`
- 环境文件：`/etc/medical-exam.env`，权限 `600`
- 持久化题库：`/srv/projects/medical-web-server/knowledge-base`
- 持久化用户状态：`/srv/projects/medical-web-server/state`
- 部署命令：`projectsctl update medical-web-server <artifact.tar.gz>`

## 开发与部署权威

本地 Windows 工作区是唯一开发工作区，GitHub `main` 是唯一发布源。服务器仓库只允许快进到已推送的精确提交并生成发布包；禁止把服务器临时修改反向作为发布源。

部署必须使用明确的发布包和 `projectsctl`。不得直接编辑 `app/current`，不得建立旧项目根、兼容别名或第二套生产工作树。

## 数据权威

原始文件是审计和重建输入，规范化题目数据库是应用事实源，FTS/向量索引是可重建派生物，用户刷题状态是独立持久化数据。

每个具体源文件是一个 collection；文件相对路径和原顺序决定刷题范围。规范 `question_id` 用于去重、搜题向量和跨文件错题统计，`occurrence_id` 用于原文件展示、题号目录、会话与判分。

目标 v2 根目录为：

```text
/srv/projects/medical-web-server/knowledge-base/v2
```

旧 `/srv/projects/medical-web-server/knowledge-base/西医题库` 在 v2 完成迁移、对账和回滚演练前不得删除或原地改造。

## 当前实现

- 当前代码：v2 Flask + 结构化 SQLite/FTS5 + 精确文件刷题 + 加密设置 + 可选 mmap 语义索引。
- 迁移兼容：v1 `ikbs_pysearch` 路由和旧 TXT/向量索引仍可通过环境变量启用。
- 当前本地 build：catalog schema v4 `v4_dffb09373b9e0b7fca56`，2,127 个精确文件题集、64,078 道规范题、1,443 个病例题组。
- 搜索：keyword 始终本地可用；semantic/hybrid 按规范题 ID 使用版本化 NumPy mmap，不引入独立向量数据库服务。
- 默认嵌入：`Qwen/Qwen3-Embedding-0.6B`、1024 维；供应商 URL、模型和加密密钥由管理员设置页管理。
- 资源目标：4 核 4G 使用 3 个同步 Gunicorn worker、preload、mmap 和每 worker 1 个 BLAS 线程。
- 核心约束：无第三方登录、无远程业务数据库、无外部数据接口依赖。

## 设置与秘密

- 服务器设置：`state/settings.sqlite3`；Fernet key：`state/settings.key`。
- 管理接口：`X-Admin-Token`；生产 `ADMIN_TOKEN` 必须为独立高强度随机值。
- 公共接口、错误响应、日志、构建产物和 Git 永不返回或保存明文 API 密钥。
- 聊天或截图中暴露的密钥必须轮换后再通过设置页录入。
