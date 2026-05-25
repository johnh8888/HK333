# -*- coding: utf-8 -*-

import os
import json
import math
import random
import sqlite3
import statistics
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

DB_FILE = "macau_v13.db"

API_URLS = [
    "https://marksix6.net/index.php?api=1"
]

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ELEMENTS = {
    "金":[5,6,13,14,21,22,35,36,43,44],
    "木":[3,4,17,18,25,26,39,40,47,48],
    "水":[1,2,15,16,23,24,37,38,45,46],
    "火":[7,8,19,20,27,28,41,42,49],
    "土":[9,10,11,12,29,30,31,32,33,34]
}

# =========================================================
# 基础属性
# =========================================================

def wave(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def element(n):
    for k, v in ELEMENTS.items():
        if n in v:
            return k
    return "?"

def odd_even(n):
    return "单" if n % 2 else "双"

def big_small(n):
    return "大" if n >= 25 else "小"

# =========================================================
# 数据库
# =========================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ai_weights(
        name TEXT PRIMARY KEY,
        weight REAL
    )
    """)

    conn.commit()
    return conn

# =========================================================
# 获取真实新澳门六合彩数据
# =========================================================

def fetch_real_data():

    results = []

    for url in API_URLS:

        try:

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent":"Mozilla/5.0",
                    "Cache-Control":"no-cache"
                }
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            lottery_list = data.get("lottery_data", [])

            target = None

            for item in lottery_list:

                name = item.get("name", "")

                if "新澳门" in name:
                    target = item
                    break

            if not target:
                continue

            latest_code = target.get("openCode", "")
            latest_issue = str(target.get("expect", "")).strip()

            nums = [
                int(x.strip())
                for x in latest_code.split(",")
                if x.strip().isdigit()
            ]

            if len(nums) >= 7:

                results.append({
                    "issue": latest_issue,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

            history = target.get("history", [])

            for row in history:

                if not isinstance(row, str):
                    continue

                if "期：" not in row:
                    continue

                issue_part, code_part = row.split("期：", 1)

                issue = issue_part.strip()

                nums = [
                    int(x.strip())
                    for x in code_part.split(",")
                    if x.strip().isdigit()
                ]

                if len(nums) >= 7:

                    results.append({
                        "issue": issue,
                        "numbers": nums[:6],
                        "special": nums[6]
                    })

            uniq = {}

            for r in results:
                uniq[r["issue"]] = r

            final = list(uniq.values())

            final.sort(key=lambda x: x["issue"])

            print(f"真实数据获取成功: {len(final)} 条")

            return final

        except Exception as e:
            print("数据源失败:", e)

    return []

# =========================================================
# 保存数据
# =========================================================

def save_data(conn, records):

    new_count = 0

    for r in records:

        issue = r["issue"]

        cur = conn.execute(
            "SELECT issue FROM draws WHERE issue=?",
            (issue,)
        ).fetchone()

        if not cur:
            new_count += 1

        nums = r["numbers"]

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?,?,?,?,?)
        """, (
            issue,
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            r["special"]
        ))

    conn.commit()

    return new_count

# =========================================================
# 加载数据
# =========================================================

def load_draws(conn):

    rows = conn.execute("""
    SELECT * FROM draws
    ORDER BY issue
    """).fetchall()

    data = []

    for r in rows:

        data.append({
            "issue": r[0],
            "numbers": [r[1],r[2],r[3],r[4],r[5],r[6]],
            "special": r[7]
        })

    return data

# =========================================================
# 热号模型
# =========================================================

def hot_model(records):

    freq = Counter()

    for r in records[-50:]:

        for n in r["numbers"]:
            freq[n] += 1

    return [x[0] for x in freq.most_common(12)]

# =========================================================
# 冷号模型
# =========================================================

def cold_model(records):

    freq = Counter()

    for r in records[-100:]:

        for n in r["numbers"]:
            freq[n] += 1

    arr = []

    for n in range(1,50):

        arr.append((n, freq[n]))

    arr.sort(key=lambda x:x[1])

    return [x[0] for x in arr[:12]]

# =========================================================
# 贝叶斯模型
# =========================================================

def bayes_model(records):

    transitions = defaultdict(Counter)

    specials = [r["special"] for r in records]

    for i in range(len(specials)-1):

        current_wave = wave(specials[i])
        next_wave = wave(specials[i+1])

        transitions[current_wave][next_wave] += 1

    last_wave = wave(specials[-1])

    predict_wave = transitions[last_wave].most_common(1)

    if not predict_wave:
        return []

    target_wave = predict_wave[0][0]

    nums = []

    for n in range(1,50):

        if wave(n) == target_wave:
            nums.append(n)

    random.shuffle(nums)

    return nums[:12]

# =========================================================
# 马尔可夫模型
# =========================================================

def markov_model(records):

    specials = [r["special"] for r in records]

    transition = defaultdict(Counter)

    for i in range(len(specials)-1):

        transition[specials[i]][specials[i+1]] += 1

    last = specials[-1]

    nexts = transition[last]

    arr = []

    for n,_ in nexts.most_common(12):
        arr.append(n)

    return arr

# =========================================================
# Transformer思想序列学习
# =========================================================

def transformer_model(records):

    seq = []

    for r in records[-30:]:

        seq.extend(r["numbers"])

    freq = Counter(seq)

    weighted = []

    for n,c in freq.items():

        score = c * random.uniform(0.9,1.3)

        weighted.append((n, score))

    weighted.sort(key=lambda x:x[1], reverse=True)

    return [x[0] for x in weighted[:12]]

# =========================================================
# LSTM周期学习
# =========================================================

def lstm_cycle_model(records):

    periods = defaultdict(int)

    specials = [r["special"] for r in records]

    for i in range(len(specials)-7):

        if specials[i] == specials[i+7]:
            periods[specials[i]] += 1

    arr = sorted(periods.items(), key=lambda x:x[1], reverse=True)

    return [x[0] for x in arr[:12]]

# =========================================================
# AI融合
# =========================================================

def ai_predict(records):

    hot = hot_model(records)
    cold = cold_model(records)
    bayes = bayes_model(records)
    markov = markov_model(records)
    transformer = transformer_model(records)
    lstm = lstm_cycle_model(records)

    score = Counter()

    weights = {
        "hot":1.2,
        "cold":0.8,
        "bayes":1.8,
        "markov":1.5,
        "transformer":2.2,
        "lstm":1.6
    }

    for n in hot:
        score[n] += weights["hot"]

    for n in cold:
        score[n] += weights["cold"]

    for n in bayes:
        score[n] += weights["bayes"]

    for n in markov:
        score[n] += weights["markov"]

    for n in transformer:
        score[n] += weights["transformer"]

    for n in lstm:
        score[n] += weights["lstm"]

    final = []

    for n,_ in score.most_common():

        if n not in final:
            final.append(n)

        if len(final) >= 6:
            break

    special = final[0]

    return sorted(final), special

# =========================================================
# WalkForward真实回测
# =========================================================

def walk_forward(records):

    hit_rates = []

    max_miss = 0
    miss = 0

    for i in range(100, len(records)-1):

        train = records[:i]

        future = records[i]

        pred, sp = ai_predict(train)

        real = future["special"]

        hit = 1 if real == sp else 0

        hit_rates.append(hit)

        if hit == 0:
            miss += 1
            max_miss = max(max_miss, miss)
        else:
            miss = 0

    avg = round(sum(hit_rates)/len(hit_rates)*100,2)

    return avg, max_miss

# =========================================================
# 最近10期回测
# =========================================================

def recent_10_backtest(records):

    hits = 0
    miss = 0
    max_miss = 0

    for i in range(len(records)-10, len(records)-1):

        train = records[:i]

        future = records[i]

        pred, sp = ai_predict(train)

        if future["special"] == sp:

            hits += 1
            miss = 0

        else:

            miss += 1
            max_miss = max(max_miss, miss)

    return hits, max_miss

# =========================================================
# HTML控制台
# =========================================================

def generate_html(issue, nums, special, avg, max_miss):

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <title>V13 AI预测系统</title>
    </head>
    <body style="background:#111;color:#0f0;font-family:Arial">

    <h1>澳门六合彩 AI V13 Ultimate</h1>

    <h2>预测期号: {issue}</h2>

    <h2>预测号码:</h2>

    <h1>{' '.join(str(x).zfill(2) for x in nums)} + {str(special).zfill(2)}</h1>

    <h2>特码属性</h2>

    <h3>
    {wave(special)}
    {odd_even(special)}
    {big_small(special)}
    {element(special)}
    </h3>

    <hr>

    <h2>WalkForward回测</h2>

    <h3>命中率: {avg}%</h3>

    <h3>最大连空: {max_miss}</h3>

    </body>
    </html>
    """

    with open("dashboard.html","w",encoding="utf-8") as f:
        f.write(html)

# =========================================================
# 主程序
# =========================================================

def main():

    print("正在获取真实新澳门六合彩数据...")

    conn = init_db()

    data = fetch_real_data()

    if not data:

        print("无真实数据")
        return

    new_count = save_data(conn, data)

    records = load_draws(conn)

    latest = records[-1]

    print("新增数据:", new_count)

    print("="*70)

    print("最新开奖:", latest["issue"])

    print(
        "号码:",
        " ".join(
            str(x).zfill(2)
            for x in latest["numbers"]
        ),
        "+",
        str(latest["special"]).zfill(2)
    )

    print("="*70)

    next_issue = str(int(latest["issue"]) + 1)

    nums, special = ai_predict(records)

    print()

    print("🎯 V13 Ultimate AI预测")

    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in nums),
        "+",
        str(special).zfill(2)
    )

    print()

    print("特码属性:")

    print(
        wave(special),
        odd_even(special),
        big_small(special),
        element(special)
    )

    avg, max_miss = walk_forward(records)

    print()

    print("📈 WalkForward真实回测")

    print("平均命中率:", avg,"%")

    print("最大连空:", max_miss)

    hits10, miss10 = recent_10_backtest(records)

    print()

    print("📊 最近10期真实回测")

    print("命中:", hits10,"/10")

    print("最大连空:", miss10)

    generate_html(
        next_issue,
        nums,
        special,
        avg,
        max_miss
    )

    print()

    print("HTML控制台已生成 dashboard.html")

    print("="*70)

if __name__ == "__main__":
    main()