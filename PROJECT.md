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

目标 v2 根目录为：

```text
/srv/projects/medical-web-server/knowledge-base/v2
```

旧 `/srv/projects/medical-web-server/knowledge-base/西医题库` 在 v2 完成迁移、对账和回滚演练前不得删除或原地改造。

## 当前实现

- 当前代码：v2 Flask + 结构化 SQLite + FTS5 + 独立刷题状态。
- 迁移兼容：v1 `ikbs_pysearch` 路由和旧 TXT/向量索引仍可通过环境变量启用。
- 当前本地 build：catalog schema v3，64,078 道规范题，1,443 个病例题组。
- 可选方向：按规范题 ID 建立本地语义索引；不引入独立向量数据库服务。
- 核心约束：无第三方登录、无远程业务数据库、无外部数据接口依赖。
