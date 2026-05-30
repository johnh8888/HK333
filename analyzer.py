#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import json
import pandas as pd
import numpy as np
from collections import Counter, defaultdict

from sklearn.linear_model import LogisticRegression

DB = "new_macau.db"
N_WINDOW = 20

conn = sqlite3.connect(DB)

df = pd.read_sql(
    "SELECT * FROM draws ORDER BY issue_no ASC",
    conn
)

df["numbers"] = df["numbers_json"].apply(json.loads)

# ==========================
# 特征准备
# ==========================
all_nums = list(range(1, 50))

counter = Counter()
last_seen = {}
transition = defaultdict(Counter)

X = []
y = []

# ==========================
# 构造训练数据（简化版）
# ==========================
for i in range(1, len(df)):
    prev = df["numbers"].iloc[i - 1]
    curr = df["numbers"].iloc[i]

    prev_set = set(prev)

    for n in all_nums:
        feature = [
            n in prev_set,                    # 是否在上一期出现
            counter[n],                      # 历史频率
            i - last_seen.get(n, 999),       # 遗漏值
        ]

        label = 1 if n in curr else 0

        X.append(feature)
        y.append(label)

    counter.update(curr)
    for n in curr:
        last_seen[n] = i

# ==========================
# 训练模型
# ==========================
model = LogisticRegression(max_iter=1000)
model.fit(X, y)

# ==========================
# 预测下一期
# ==========================
latest = df["numbers"].iloc[-1]
latest_set = set(latest)

scores = []

for n in all_nums:
    feature = [
        n in latest_set,
        counter[n],
        len(df) - last_seen.get(n, 999)
    ]

    prob = model.predict_proba([feature])[0][1]
    scores.append((n, prob))

scores.sort(key=lambda x: x[1], reverse=True)

top6 = [n for n, _ in scores[:6]]
top10 = [n for n, _ in scores[:10]]

# ==========================
# 冷热 & 趋势
# ==========================
hot = [n for n, _ in counter.most_common(6)]
cold = [n for n in all_nums if counter[n] == 0][:6]

trend_counter = Counter()
for nums in df["numbers"].tail(10):
    trend_counter.update(nums)

trend = [n for n, _ in trend_counter.most_common(6)]

# ==========================
# 马尔可夫（简化）
# ==========================
markov = Counter()
for a, targets in transition.items():
    for b, c in targets.items():
        markov[b] += c

markov_top = [n for n, _ in markov.most_common(6)]

# ==========================
# 输出
# ==========================
result = {
    "top6": top6,
    "top10": top10,
    "hot": hot,
    "cold": cold,
    "trend": trend,
    "markov": markov_top,
    "confidence": round(float(scores[0][1]), 4)
}

with open("prediction.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(json.dumps(result, indent=2, ensure_ascii=False))

conn.close()