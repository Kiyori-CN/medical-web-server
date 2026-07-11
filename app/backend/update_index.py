#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西医题库增量索引更新脚本
用法: python3 update_index.py [--full]
"""
import sys
import os
sys.path.insert(0, '/srv/projects/medical-web-server/app/backend')

from incremental_index import IncrementalIndexer
from ikbs_pysearch import get_engine

def main():
    kb_path = '/srv/projects/medical-web-server/knowledge-base/西医题库'

    # 检查是否强制全量更新
    force_full = '--full' in sys.argv

    if force_full:
        print("[*] 执行全量重建...")
        engine = get_engine(kb_path)
        count = engine.optimize()
        print(f"[+] 全量重建完成，共 {count} 个分块")
    else:
        print("[*] 执行增量更新...")
        indexer = IncrementalIndexer(kb_path)
        engine = get_engine(kb_path)
        result = indexer.incremental_optimize(engine)

        if result['status'] == 'no_changes':
            print(f"[*] {result['message']}")
        else:
            print(f"[+] {result['message']}")
            stats = result['stats']
            print(f"    - 保留分块: {stats['kept_chunks']}")
            print(f"    - 新增分块: {stats['new_chunks']}")
            print(f"    - 总计分块: {stats['total_chunks']}")

            changes = stats['changes']
            if changes['added']:
                print(f"    - 新增文件: {', '.join(changes['added'])}")
            if changes['modified']:
                print(f"    - 修改文件: {', '.join(changes['modified'])}")
            if changes['deleted']:
                print(f"    - 删除文件: {', '.join(changes['deleted'])}")

if __name__ == '__main__':
    main()
