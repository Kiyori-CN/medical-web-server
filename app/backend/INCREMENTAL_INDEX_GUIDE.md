# 西医题库增量索引实现方案

## 📊 系统现状分析

### 当前索引结构
- **题库规模**: 100个文件，66,096个分块
- **原始数据**: 70M txt文件
- **优化索引**: 259M vectors.npy + 74M optimized_chunks.json
- **原始索引**: 807M vectors/ + 74M chunks/ (分片存储)
- **向量维度**: 1024 (BAAI/bge-m3)

### 核心问题
当前 `optimize()` 方法每次都全量重建，即使只修改1个文件也要重新处理全部66,096个分块，耗时约5分钟。

---

## 🎯 增量索引方案

### 方案架构

```
┌─ 文件变更检测 ─────────────────────────────────┐
│ 1. 读取 fingerprints.json（旧指纹）            │
│ 2. 扫描当前文件生成新指纹                      │
│    格式: 文件名|大小|修改时间戳                │
│ 3. 对比差异: added / modified / deleted        │
└────────────────────────────────────────────────┘
                    ↓
┌─ 索引增量更新 ─────────────────────────────────┐
│ 1. 加载现有 vectors.npy + optimized_chunks.json│
│ 2. 移除删除/修改文件的旧分块                   │
│ 3. 从 vectors/*.vec 加载新增/修改文件的向量    │
│ 4. 合并向量: kept_vectors + new_vectors        │
│ 5. 归一化并保存                                │
│ 6. 更新 fingerprints.json                      │
└────────────────────────────────────────────────┘
```

### 性能对比

| 对比项     | 全量重建      | 增量更新        |
|-----------|--------------|----------------|
| 处理文件   | 100个        | 仅变更文件      |
| 处理分块   | 66,096个     | 约660个/文件    |
| 耗时       | ~5分钟       | ~10秒/文件      |
| 向量计算   | 全部重算     | 仅新增/修改     |
| 内存占用   | 峰值1.5GB    | 峰值500MB       |

---

## 💻 使用方法

### 方法1: 独立脚本（推荐）

```bash
# 增量更新（默认）
cd /srv/projects/medical-web-server/app/backend
python3 update_index.py

# 强制全量重建
python3 update_index.py --full
```

### 方法2: Python代码调用

```python
from incremental_index import IncrementalIndexer
from ikbs_pysearch import get_engine

kb_path = '/srv/projects/medical-web-server/knowledge-base/西医题库'

# 增量更新
indexer = IncrementalIndexer(kb_path)
engine = get_engine(kb_path)
result = indexer.incremental_optimize(engine)

print(result['message'])
print(f"保留: {result['stats']['kept_chunks']}")
print(f"新增: {result['stats']['new_chunks']}")
print(f"总计: {result['stats']['total_chunks']}")
```

### 方法3: 集成到现有流程

修改 `ikbs_pysearch.py` 的 `main()` 函数，添加增量更新命令：

```python
if cmd == "optimize":
    incremental = sys.argv[3].lower() == 'true' if len(sys.argv) > 3 else False
    count = engine.optimize(incremental=incremental)
    result = {"success": True, "message": f"成功优化 {count} 个分块。"}
```

然后调用：

```bash
# 增量更新
python3 ikbs_pysearch.py optimize /srv/projects/medical-web-server/knowledge-base/西医题库 true

# 全量重建
python3 ikbs_pysearch.py optimize /srv/projects/medical-web-server/knowledge-base/西医题库 false
```

---

## 🔧 实现细节

### 文件指纹格式

```json
{
  "西医题库_1.txt": "西医题库_1.txt|507276|1770917040000",
  "西医题库_2.txt": "西医题库_2.txt|487635|1770899280000"
}
```

格式: `文件名|文件大小(字节)|修改时间(毫秒时间戳)`

### 变更检测逻辑

```python
def detect_changes():
    old_fingerprints = load_from_json('fingerprints.json')
    current_files = scan_current_files()

    added = [f for f in current_files if f not in old_fingerprints]
    deleted = [f for f in old_fingerprints if f not in current_files]
    modified = [
        f for f in current_files
        if f in old_fingerprints and current_files[f] != old_fingerprints[f]
    ]

    return {'added': added, 'deleted': deleted, 'modified': modified}
```

### 索引合并流程

```python
def incremental_optimize():
    # 1. 检测变更
    changes = detect_changes()

    # 2. 加载现有索引
    old_vectors = np.load('vectors.npy')
    old_chunks = load_json('optimized_chunks.json')

    # 3. 移除旧数据
    removed_files = set(changes['deleted'] + changes['modified'])
    keep_indices = [i for i, c in enumerate(old_chunks)
                    if c['fileName'] not in removed_files]
    kept_vectors = old_vectors[keep_indices]
    kept_chunks = [old_chunks[i] for i in keep_indices]

    # 4. 加载新数据
    new_vectors, new_chunks = load_new_files(
        changes['added'] + changes['modified']
    )

    # 5. 合并
    merged_vectors = np.vstack([kept_vectors, new_vectors])
    merged_chunks = kept_chunks + new_chunks

    # 6. 归一化
    norms = np.linalg.norm(merged_vectors, axis=1, keepdims=True)
    merged_vectors = merged_vectors / np.where(norms > 1e-10, norms, 1.0)

    # 7. 保存
    np.save('vectors.npy', merged_vectors)
    save_json('optimized_chunks.json', merged_chunks)
    save_json('fingerprints.json', changes['current_fingerprints'])

    return len(merged_chunks)
```

---

## 📝 使用场景

### 场景1: 新增题库文件

```bash
# 1. 添加新文件到题库目录
cp 西医题库_101.txt /srv/projects/medical-web-server/knowledge-base/西医题库/

# 2. 使用 ikbs_search 构建新文件的向量索引
# (这一步会生成 vectors/西医题库_101.vec 和 chunks/西医题库_101.chk)

# 3. 执行增量更新
cd /srv/projects/medical-web-server/app/backend
python3 update_index.py

# 输出示例:
# [*] 检测到变更: 新增1个, 修改0个, 删除0个
# [*] 保留 66096 个未变更分块
# [*] 新增 661 个分块
# [+] 增量更新完成: 保留66096个, 新增661个, 总计66757个分块
```

### 场景2: 修改现有文件

```bash
# 1. 修改题库文件
vim /srv/projects/medical-web-server/knowledge-base/西医题库/西医题库_1.txt

# 2. 重新构建该文件的向量索引
# (覆盖 vectors/西医题库_1.vec 和 chunks/西医题库_1.chk)

# 3. 执行增量更新
python3 update_index.py

# 输出示例:
# [*] 检测到变更: 新增0个, 修改1个, 删除0个
# [*] 保留 65437 个未变更分块
# [*] 新增 659 个分块
# [+] 增量更新完成: 保留65437个, 新增659个, 总计66096个分块
```

### 场景3: 删除题库文件

```bash
# 1. 删除题库文件
rm /srv/projects/medical-web-server/knowledge-base/西医题库/西医题库_100.txt

# 2. 执行增量更新
python3 update_index.py

# 输出示例:
# [*] 检测到变更: 新增0个, 修改0个, 删除1个
# [*] 保留 65435 个未变更分块
# [*] 新增 0 个分块
# [+] 增量更新完成: 保留65435个, 新增0个, 总计65435个分块
```

### 场景4: 无变更检测

```bash
# 执行增量更新（文件未变更）
python3 update_index.py

# 输出示例:
# [*] 索引已是最新，无需更新
```

---

## ⚠️ 注意事项

### 1. 前置条件

增量更新依赖原始索引文件（vectors/*.vec 和 chunks/*.chk），必须先使用 `ikbs_search` 工具包构建这些文件。

```bash
# 如果原始索引缺失，需要先全量构建
python3 update_index.py --full
```

### 2. 文件指纹机制

指纹基于 **文件名 + 大小 + 修改时间**，如果仅修改文件内容但未更新修改时间，变更可能无法检测。

解决方法：
```bash
# 强制更新修改时间
touch /srv/projects/medical-web-server/knowledge-base/西医题库/西医题库_1.txt
```

### 3. 向量维度一致性

增量更新要求新旧向量维度一致（当前为1024维）。如果更换嵌入模型，必须全量重建。

### 4. 内存占用

增量更新会同时加载旧索引和新向量到内存，峰值内存约500MB。如果服务器内存不足，建议使用全量重建。

### 5. 并发安全

增量更新不支持并发执行，多个进程同时更新会导致索引损坏。建议使用文件锁或任务队列。

---

## 🔍 故障排查

### 问题1: 提示"未找到现有索引"

**原因**: `vectors.npy` 或 `optimized_chunks.json` 不存在

**解决**:
```bash
# 执行全量重建
python3 update_index.py --full
```

### 问题2: 提示"跳过缺失索引文件"

**原因**: 新增/修改的文件缺少 `.vec` 或 `.chk` 文件

**解决**:
```bash
# 使用 ikbs_search 重新构建该文件的向量索引
# 或者从题库目录中移除该文件
```

### 问题3: 向量维度不匹配

**错误**: `ValueError: all the input array dimensions except for the concatenation axis must match exactly`

**原因**: 新旧向量维度不一致（可能更换了嵌入模型）

**解决**:
```bash
# 强制全量重建
python3 update_index.py --full
```

### 问题4: 内存不足

**错误**: `MemoryError` 或 `numpy.core._exceptions._ArrayMemoryError`

**解决**:
```bash
# 方法1: 增加交换空间
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 方法2: 使用全量重建（内存占用更低）
python3 update_index.py --full
```

---

## 📈 性能优化建议

### 1. 定期增量更新

建议每次修改题库后立即执行增量更新，避免积累大量变更。

```bash
# 添加到 crontab（每小时检查一次）
0 * * * * cd /srv/projects/medical-web-server/app/backend && python3 update_index.py >> /var/log/medical_exam/index_update.log 2>&1
```

### 2. 批量更新策略

如果需要批量修改多个文件，建议：
1. 先修改所有文件
2. 重新构建所有变更文件的向量索引
3. 执行一次增量更新

避免每修改一个文件就更新一次索引。

### 3. 监控索引状态

```bash
# 查看索引统计
curl http://127.0.0.1:8080/api/status

# 输出示例:
# {
#   "optimized": true,
#   "total_chunks": 66096,
#   "vector_dim": 1024,
#   "total_files": 100
# }
```

---

## 🚀 未来优化方向

### 1. 自动化触发

监听题库目录的文件变更事件，自动触发增量更新。

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class IndexUpdateHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith('.txt'):
            trigger_incremental_update()
```

### 2. 并发安全

使用文件锁或分布式锁确保增量更新的原子性。

```python
import fcntl

with open('/tmp/index_update.lock', 'w') as lock_file:
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    incremental_optimize()
    fcntl.flock(lock_file, fcntl.LOCK_UN)
```

### 3. 增量构建向量

当前增量更新依赖预先构建的 `.vec` 文件。未来可以集成向量计算，实现端到端的增量更新。

```python
def incremental_optimize_with_embedding():
    changes = detect_changes()
    new_files = changes['added'] + changes['modified']

    # 直接计算新文件的向量
    new_vectors = []
    for file in new_files:
        text = read_file(file)
        chunks = split_text(text)
        vectors = engine.encode(chunks)
        new_vectors.extend(vectors)

    # 合并索引
    merge_and_save(new_vectors)
```

### 4. 版本控制

为每次索引更新生成版本号，支持回滚到历史版本。

```json
{
  "version": "v1.2.3",
  "timestamp": "2026-03-04T10:30:00Z",
  "changes": {
    "added": ["西医题库_101.txt"],
    "modified": [],
    "deleted": []
  }
}
```

---

## 📞 技术支持

- **项目路径**: `/srv/projects/medical-web-server/app/backend/`
- **题库路径**: `/srv/projects/medical-web-server/knowledge-base/西医题库/`
- **日志路径**: `/var/log/medical_exam/`
- **服务器**: 106.54.201.246 (OpenCloudOS 9)

---

**最后更新**: 2026-03-04
**版本**: v1.0
