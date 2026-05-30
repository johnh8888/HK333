#!/usr/bin/env python3
# -- coding: utf-8 --

import sqlite3
import json
import pandas as pd
from collections import Counter

# ==========================
# 配置
# ==========================
DB = "new_macau.db"
N_WINDOW = 20  # 滑动窗口大小

# ==========================
# 连接数据库
# ==========================
conn = sqlite3.connect(DB)

# ==========================
# 读取历史开奖
# ==========================
df = pd.read_sql("SELECT * FROM draws ORDER BY issue_no ASC", conn)
df['numbers'] = df['numbers_json'].apply(json.loads)

# ==========================
# 统计出现次数和连号
# ==========================
counter = Counter()
pair_counter = Counter()
triple_counter = Counter()

for nums in df['numbers'][-N_WINDOW:]:
    counter.update(nums)
    
    nums_sorted = sorted(nums)
    
    # 连号对
    for i in range(len(nums_sorted)-1):
        if nums_sorted[i+1] - nums_sorted[i] == 1:
            pair_counter[(nums_sorted[i], nums_sorted[i+1])] += 1
    
    # 连号三连号
    for i in range(len(nums_sorted)-2):
        if nums_sorted[i+2] - nums_sorted[i] == 2 and nums_sorted[i+1] - nums_sorted[i] == 1:
            triple_counter[(nums_sorted[i], nums_sorted[i+1], nums_sorted[i+2])] += 1

# ==========================
# 输出 top 预测
# ==========================
top_numbers = [num for num, _ in counter.most_common(6)]
top_pairs = pair_counter.most_common(5)
top_triples = triple_counter.most_common(5)

print("="*50)
print("=== Top Numbers ===")
print(top_numbers)
print("=== Top Pairs ===")
print(top_pairs)
print("=== Top Triples ===")
print(top_triples)
print("="*50)

# ==========================
# 可选：保存预测结果到数据库
# 先注释掉，避免 NOT NULL 报错
# ==========================
# pool_numbers_json = json.dumps(top_numbers)
# conn.execute(
#     "INSERT INTO prediction_pools (run_id, pool_size, numbers_json, created_at) VALUES (NULL, ?, ?, datetime('now'))",
#     (len(top_numbers), pool_numbers_json)
# )
# conn.commit()

# ==========================
# 关闭数据库
# ==========================
conn.close()