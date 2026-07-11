#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量索引更新模块
为 ikbs_pysearch 提供增量更新能力
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

class IncrementalIndexer:
    """增量索引更新器"""

    def __init__(self, kb_path: str):
        self.kb_path = Path(kb_path).resolve()
        self.cache_dir = self.kb_path / '.ikbs_cache'
        self.fingerprints_file = self.cache_dir / 'fingerprints.json'
        self.vectors_dir = self.cache_dir / 'vectors'
        self.chunks_dir = self.cache_dir / 'chunks'
        self.optimized_vectors = self.cache_dir / 'vectors.npy'
        self.optimized_chunks = self.cache_dir / 'optimized_chunks.json'

    def detect_changes(self) -> Dict[str, List[str]]:
        """检测文件变更"""
        old_fingerprints = {}
        if self.fingerprints_file.exists():
            with open(self.fingerprints_file, 'r') as f:
                old_fingerprints = json.load(f)

        current_files = {}
        for file in self.kb_path.glob('*.txt'):
            stat = file.stat()
            fingerprint = f"{file.name}|{stat.st_size}|{int(stat.st_mtime * 1000)}"
            current_files[file.name] = fingerprint

        added = [f for f in current_files if f not in old_fingerprints]
        deleted = [f for f in old_fingerprints if f not in current_files]
        modified = [
            f for f in current_files
            if f in old_fingerprints and current_files[f] != old_fingerprints[f]
        ]
        unchanged = [
            f for f in current_files
            if f in old_fingerprints and current_files[f] == old_fingerprints[f]
        ]

        return {
            'added': added,
            'deleted': deleted,
            'modified': modified,
            'unchanged': unchanged,
            'current_fingerprints': current_files
        }

    def incremental_optimize(self, engine) -> Dict:
        """增量优化索引"""
        changes = self.detect_changes()

        if not changes['added'] and not changes['deleted'] and not changes['modified']:
            return {
                'status': 'no_changes',
                'message': '索引已是最新，无需更新',
                'stats': changes
            }

        print(f"[*] 检测到变更: 新增{len(changes['added'])}个, "
              f"修改{len(changes['modified'])}个, "
              f"删除{len(changes['deleted'])}个", flush=True)

        # 加载现有优化索引
        if self.optimized_vectors.exists() and self.optimized_chunks.exists():
            old_vectors = np.load(str(self.optimized_vectors), mmap_mode='r').copy()
            with open(self.optimized_chunks, 'r', encoding='utf-8') as f:
                old_chunks = json.load(f)
        else:
            old_vectors = np.array([], dtype=np.float32).reshape(0, 1024)
            old_chunks = []

        # 移除删除和修改文件的旧数据
        removed_files = set(changes['deleted'] + changes['modified'])
        keep_indices = [
            i for i, chunk in enumerate(old_chunks)
            if chunk.get('fileName') not in removed_files
        ]

        if keep_indices:
            kept_vectors = old_vectors[keep_indices]
            kept_chunks = [old_chunks[i] for i in keep_indices]
        else:
            kept_vectors = np.array([], dtype=np.float32).reshape(0, 1024)
            kept_chunks = []

        # 加载新增和修改文件的数据
        new_files = changes['added'] + changes['modified']
        new_vectors_list = []
        new_chunks_list = []

        for filename in new_files:
            safe_name = Path(filename).stem
            vec_file = self.vectors_dir / f"{safe_name}.vec"
            chk_file = self.chunks_dir / f"{safe_name}.chk"

            if vec_file.exists() and chk_file.exists():
                with open(vec_file, 'r') as f:
                    file_vecs = json.load(f)
                with open(chk_file, 'r', encoding='utf-8') as f:
                    file_chunks = json.load(f)

                for i, vec in enumerate(file_vecs):
                    if vec and isinstance(vec, list) and len(vec) > 0:
                        new_vectors_list.append(vec)
                        chunk_info = file_chunks[i] if i < len(file_chunks) else {}
                        if not chunk_info.get('fileName'):
                            chunk_info['fileName'] = filename
                        new_chunks_list.append(chunk_info)

        # 合并向量
        if new_vectors_list:
            new_vectors = np.array(new_vectors_list, dtype=np.float32)
            if kept_vectors.shape[0] > 0:
                merged_vectors = np.vstack([kept_vectors, new_vectors])
            else:
                merged_vectors = new_vectors
            merged_chunks = kept_chunks + new_chunks_list
        else:
            merged_vectors = kept_vectors
            merged_chunks = kept_chunks

        # 归一化
        norms = np.linalg.norm(merged_vectors, axis=1, keepdims=True)
        safe_norms = np.where(norms > 1e-10, norms, 1.0)
        merged_vectors = merged_vectors / safe_norms

        # 保存
        np.save(str(self.optimized_vectors), merged_vectors)
        with open(self.optimized_chunks, 'w', encoding='utf-8') as f:
            json.dump(merged_chunks, f, ensure_ascii=False)

        # 更新指纹
        with open(self.fingerprints_file, 'w', encoding='utf-8') as f:
            json.dump(changes['current_fingerprints'], f, ensure_ascii=False)

        return {
            'status': 'success',
            'message': f'增量更新完成: 保留{len(kept_chunks)}个分块, 新增{len(new_chunks_list)}个分块',
            'stats': {
                'kept_chunks': len(kept_chunks),
                'new_chunks': len(new_chunks_list),
                'total_chunks': len(merged_chunks),
                'changes': changes
            }
        }
