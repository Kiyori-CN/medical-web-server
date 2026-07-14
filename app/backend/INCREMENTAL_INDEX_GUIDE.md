# v1 增量索引兼容说明

> 本文件只描述当前生产 v1 的遗留边界，不是 v2 实施方案。新开发不得继续扩展文件级 `.ikbs_cache` 协议。

## 当前行为

`incremental_index.py` 以知识库根目录中的大 TXT 文件为变更单位，通过文件名、大小和修改时间判断新增、修改与删除，然后合并：

- `.ikbs_cache/vectors/*.vec`
- `.ikbs_cache/chunks/*.chk`
- `.ikbs_cache/vectors.npy`
- `.ikbs_cache/optimized_chunks.json`

旧生产知识库目前仍依赖这些文件，因此迁移期间只能保持兼容和修复阻断性问题。

## 已知限制

- 一个大 TXT 文件包含多道题，无法做到题目级增量更新；
- chunk 只保留通用分片名，目录、科目、章节和年份元数据不足；
- 数据与向量位置依赖数组顺序，缺少稳定 question ID；
- 指纹使用大小和 mtime，不是内容哈希；
- 代码存在固定向量维度假设；
- 优化文件原地重写，不适合作为可回滚的版本化发布物。

## v2 替代方案

v2 使用：

- 原始文件 SHA-256 和 snapshot manifest；
- 规范化 question ID 与 occurrence/collection 关系；
- 题目嵌入文本哈希缓存；
- `builds/<build_id>` 不可变构建目录；
- 完整校验后切换 `current.json`；
- SQLite FTS5 作为默认搜索，向量只作为可选语义召回。

详细规范见 `docs/data-architecture.md`。

## 操作约束

- 不得在旧生产 `.ikbs_cache` 上试验 v2 构建。
- 不得删除或覆盖 `/srv/projects/medical-web-server/knowledge-base/西医题库`。
- 任何旧索引维护都必须先备份、记录当前状态并验证 `/api/health`。
- v2 完成对账、搜索回归和回滚演练后，才能单独规划旧索引下线。
