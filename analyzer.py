#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import json
import pandas as pd
from collections import Counter

# ==========================
# 配置
# ==========================
DB = "new_macau.db"
N_WINDOW = 20

# ==========================
# 连接数据库
# ==========================
conn = sqlite3.connect(DB)

df = pd.read_sql(
    "SELECT * FROM draws ORDER BY issue_no ASC",
    conn
)

df["numbers"] = df["numbers_json"].apply(json.loads)

# ==========================
# 统计
# ==========================
counter = Counter()
pair_counter = Counter()
triple_counter = Counter()

odd = 0
even = 0
small = 0
large = 0

for nums in df["numbers"].tail(N_WINDOW):
    counter.update(nums)

    for n in nums:
        if n % 2 == 0:
            even += 1
        else:
            odd += 1

        if n <= 24:
            small += 1
        else:
            large += 1

    nums = sorted(nums)

    # 连号
    for i in range(len(nums) - 1):
        if nums[i+1] - nums[i] == 1:
            pair_counter[(nums[i], nums[i+1])] += 1

    # 三连号
    for i in range(len(nums) - 2):
        if nums[i+1] - nums[i] == 1 and nums[i+2] - nums[i] == 2:
            triple_counter[(nums[i], nums[i+1], nums[i+2])] += 1


top_numbers = [n for n, _ in counter.most_common(6)]
top_pairs = pair_counter.most_common(5)
top_triples = triple_counter.most_common(5)

result = {
    "top6": top_numbers,
    "top10": [n for n, _ in counter.most_common(10)],
    "top20": [n for n, _ in counter.most_common(20)],
    "pairs": [list(k) + [v] for k, v in top_pairs],
    "triples": [list(k) + [v] for k, v in top_triples],
    "odd_even": f"{odd}:{even}",
    "small_large": f"{small}:{large}"
}

print("=" * 60)
print(json.dumps(result, indent=2, ensure_ascii=False))
print("=" * 60)

with open("prediction.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

conn.close()