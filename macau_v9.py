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

DB_FILE = "macau_v9.db"
HTML_FILE = "dashboard.html"

# =========================
# 波色
# =========================

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

# =========================
# 数据库
# =========================

def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        n1 INT,
        n2 INT,
        n3 INT,
        n4 INT,
        n5 INT,
        n6 INT,
        special INT,
        created_at TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ai_params(
        k TEXT PRIMARY KEY,
        v REAL
    )
    """)

    conn.commit()
    return conn

# =========================
# 获取真实新澳门六合彩
# =========================

def fetch_data():
    url = "https://marksix6.net/index.php?api=1"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent":"Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))

    target = None

    for item in data.get("lottery_data", []):
        if "新澳门" in item.get("name", ""):
            target = item
            break

    if not target:
        raise Exception("未找到新澳门六合彩")

    rows = []

    # 最新
    latest_nums = [
        int(x)
        for x in target["openCode"].split(",")
    ]

    rows.append({
        "issue": str(target["expect"]),
        "numbers": latest_nums[:6],
        "special": latest_nums[6]
    })

    # 历史
    for row in target.get("history", []):

        if "期：" not in row:
            continue

        left, right = row.split("期：")

        issue = left.strip()

        nums = [
            int(x)
            for x in right.split(",")
        ]

        if len(nums) >= 7:
            rows.append({
                "issue": issue,
                "numbers": nums[:6],
                "special": nums[6]
            })

    uniq = {}

    for r in rows:
        uniq[r["issue"]] = r

    rows = list(uniq.values())

    rows.sort(key=lambda x:x["issue"])

    return rows

# =========================
# 保存数据
# =========================

def save_data(conn, rows):

    new_count = 0

    for r in rows:

        issue = r["issue"]
        nums = r["numbers"]
        special = r["special"]

        cur = conn.execute(
            "SELECT issue FROM draws WHERE issue=?",
            (issue,)
        ).fetchone()

        if not cur:
            new_count += 1

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?,?,?,?,?,?)
        """,(
            issue,
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            special,
            datetime.now().isoformat()
        ))

    conn.commit()

    return new_count

# =========================
# 加载历史
# =========================

def load_draws(conn):

    rows = conn.execute("""
    SELECT * FROM draws
    ORDER BY issue
    """).fetchall()

    result = []

    for r in rows:
        result.append({
            "issue": r[0],
            "numbers": [r[1],r[2],r[3],r[4],r[5],r[6]],
            "special": r[7]
        })

    return result

# =========================
# 波色
# =========================

def get_wave(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

# =========================
# AI参数
# =========================

DEFAULT_PARAMS = {
    "hot_weight": 1.2,
    "cold_weight": 0.8,
    "wave_weight": 1.5,
    "cycle_weight": 1.3,
    "bayes_weight": 1.1
}

def load_ai_params(conn):

    params = DEFAULT_PARAMS.copy()

    rows = conn.execute("""
    SELECT k,v FROM ai_params
    """).fetchall()

    for k,v in rows:
        params[k] = v

    return params

def save_ai_param(conn, k, v):

    conn.execute("""
    INSERT OR REPLACE INTO ai_params
    VALUES(?,?)
    """,(k,v))

    conn.commit()

# =========================
# 热号
# =========================

def hot_numbers(draws):

    freq = Counter()

    for d in draws[-50:]:
        for n in d["numbers"]:
            freq[n] += 1

    return freq

# =========================
# 冷号
# =========================

def cold_numbers(draws):

    freq = hot_numbers(draws)

    arr = []

    for n in range(1,50):
        arr.append((n, freq[n]))

    arr.sort(key=lambda x:x[1])

    return [x[0] for x in arr[:10]]

# =========================
# 波色统计
# =========================

def wave_score(draws):

    score = {
        "红":0,
        "蓝":0,
        "绿":0
    }

    weight = 1

    for d in draws[-30:]:

        w = get_wave(d["special"])

        score[w] += weight

        weight += 1

    return score

# =========================
# 周期识别
# =========================

def detect_cycle(draws):

    recent = [
        get_wave(x["special"])
        for x in draws[-12:]
    ]

    c = Counter(recent)

    return c.most_common(1)[0][0]

# =========================
# 贝叶斯更新
# =========================

def bayes_score(draws):

    freq = Counter()

    for d in draws[-100:]:
        freq[d["special"]] += 1

    total = sum(freq.values())

    result = {}

    for n in range(1,50):

        result[n] = (
            (freq[n] + 1) /
            (total + 49)
        )

    return result

# =========================
# KMeans冷热聚类(简化版)
# =========================

def cluster_analysis(draws):

    freq = hot_numbers(draws)

    arr = []

    for n in range(1,50):
        arr.append((n, freq[n]))

    arr.sort(key=lambda x:x[1], reverse=True)

    hot = [x[0] for x in arr[:15]]
    cold = [x[0] for x in arr[-15:]]

    return hot, cold

# =========================
# 庄家行为识别
# =========================

def dealer_behavior(draws):

    recent = draws[-20:]

    hot = set(cluster_analysis(draws)[0])

    fail = 0

    for d in recent:

        if len(set(d["numbers"]) & hot) <= 1:
            fail += 1

    if fail >= 10:
        return "反热策略"

    return "正常"

# =========================
# AI综合评分
# =========================

def score_numbers(draws, params):

    scores = defaultdict(float)

    hot_freq = hot_numbers(draws)

    cold = cold_numbers(draws)

    bayes = bayes_score(draws)

    wave = wave_score(draws)

    cycle_wave = detect_cycle(draws)

    for n in range(1,50):

        scores[n] += hot_freq[n] * params["hot_weight"]

        if n in cold:
            scores[n] += params["cold_weight"]

        scores[n] += bayes[n] * 100 * params["bayes_weight"]

        if get_wave(n) == cycle_wave:
            scores[n] += params["cycle_weight"]

        scores[n] += random.random()

    return scores

# =========================
# 生成预测
# =========================

def generate_prediction(draws, conn):

    params = load_ai_params(conn)

    scores = score_numbers(draws, params)

    arr = sorted(
        scores.items(),
        key=lambda x:x[1],
        reverse=True
    )

    nums = []

    for n,s in arr:

        if n not in nums:
            nums.append(n)

        if len(nums) == 6:
            break

    special = arr[6][0]

    return nums, special

# =========================
# Walk Forward
# =========================

def walk_forward(draws):

    hits = []

    for i in range(100, len(draws)-1):

        train = draws[:i]

        target = draws[i]

        fake_conn = connect_db()

        nums, sp = generate_prediction(train, fake_conn)

        hit = len(
            set(nums) &
            set(target["numbers"])
        )

        hits.append(hit)

        fake_conn.close()

    if not hits:
        return 0

    return round(sum(hits)/len(hits),4)

# =========================
# MonteCarlo
# =========================

def monte_carlo():

    hits = []

    for _ in range(5000):

        a = set(random.sample(range(1,50),6))
        b = set(random.sample(range(1,50),6))

        hits.append(len(a & b))

    return round(statistics.mean(hits),4)

# =========================
# AI学习
# =========================

def ai_learn(conn, score):

    params = load_ai_params(conn)

    if score < 0.6:

        params["wave_weight"] *= 0.98
        params["hot_weight"] *= 1.01

    else:

        params["wave_weight"] *= 1.01
        params["cycle_weight"] *= 1.01

    for k,v in params.items():
        save_ai_param(conn, k, v)

# =========================
# HTML控制台
# =========================

def generate_html(draws, nums, special, wf):

    latest = draws[-1]

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <title>V9 AI控制台</title>
    </head>
    <body>

    <h1>新澳门六合彩 V9 AI系统</h1>

    <h2>最新开奖</h2>

    <p>{latest['issue']}</p>

    <p>
    {' '.join(map(str, latest['numbers']))}
    +
    {latest['special']}
    </p>

    <h2>AI预测</h2>

    <p>
    {' '.join(map(str, nums))}
    +
    {special}
    </p>

    <h2>WalkForward</h2>

    <p>{wf}</p>

    </body>
    </html>
    """

    with open(HTML_FILE,"w",encoding="utf-8") as f:
        f.write(html)

# =========================
# 主流程
# =========================

def main():

    print("正在获取真实新澳门六合彩数据...")

    conn = connect_db()

    rows = fetch_data()

    print(f"获取成功: {len(rows)} 条")

    new_count = save_data(conn, rows)

    print(f"新增数据: {new_count}")

    draws = load_draws(conn)

    latest = draws[-1]

    print("="*70)

    print(f"最新开奖: {latest['issue']}")

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

    nums, special = generate_prediction(draws, conn)

    print()

    print("🎯 V9 AI终极预测")

    print(
        "号码:",
        " ".join(
            str(x).zfill(2)
            for x in nums
        ),
        "+",
        str(special).zfill(2)
    )

    print()

    print("🎨 波色统计")

    ws = wave_score(draws)

    for k,v in ws.items():
        print(f"{k}: {v}")

    print()

    print("🏦 庄家行为")

    print(dealer_behavior(draws))

    print()

    print("📈 WalkForward")

    wf = walk_forward(draws)

    print("平均命中:", wf)

    print()

    print("🎲 MonteCarlo")

    mc = monte_carlo()

    print("随机基准:", mc)

    ai_learn(conn, wf)

    print()

    print("🧠 AI参数已学习更新")

    generate_html(draws, nums, special, wf)

    print()

    print(f"HTML控制台已生成: {HTML_FILE}")

    print("="*70)

    conn.close()

if __name__ == "__main__":
    main()