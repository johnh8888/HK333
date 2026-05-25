#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen

# =========================================================
# 新澳门六合彩 AI 自动预测系统
# =========================================================

DB_NAME = "new_macau.db"

API_URL = "https://marksix6.net/index.php?api=1"

# =========================================================
# 策略
# =========================================================

STRATEGIES = [
    "balanced",
    "hot",
    "cold",
    "momentum",
    "ensemble",
    "pattern",
]

STRATEGY_NAMES = {
    "balanced": "组合策略",
    "hot": "热号策略",
    "cold": "冷号回补",
    "momentum": "近期动量",
    "ensemble": "集成投票",
    "pattern": "规律挖掘",
}

# =========================================================
# 数据结构
# =========================================================

@dataclass
class Draw:
    issue: str
    date: str
    nums: list[int]
    special: int

# =========================================================
# 时间
# =========================================================

def utc_now():
    return datetime.utcnow().isoformat()

# =========================================================
# 数据库
# =========================================================

def connect_db():

    conn = sqlite3.connect(DB_NAME)

    conn.row_factory = sqlite3.Row

    return conn

def init_db(conn):

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS draws(

        issue_no TEXT PRIMARY KEY,

        draw_date TEXT,

        numbers_json TEXT,

        special_number INTEGER,

        source TEXT,

        created_at TEXT,

        updated_at TEXT

    );

    CREATE TABLE IF NOT EXISTS predictions(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        issue_no TEXT,

        strategy TEXT,

        numbers_json TEXT,

        special_number INTEGER,

        hit INTEGER DEFAULT 0,

        reviewed INTEGER DEFAULT 0,

        created_at TEXT

    );

    """)

    conn.commit()

# =========================================================
# 获取数据
# =========================================================

def fetch_draws():

    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(req, timeout=20) as r:

        payload = json.loads(
            r.read().decode("utf-8")
        )

    target = None

    for x in payload["lottery_data"]:

        if x["name"] == "新澳门彩":

            target = x

            break

    if not target:

        raise RuntimeError("无法找到新澳门彩数据")

    draws = []

    for item in target["history"]:

        parts = item.split("期：")

        issue = parts[0].strip()

        nums = [int(x) for x in parts[1].split(",")]

        draw = Draw(
            issue=issue,
            date=datetime.now().strftime("%Y-%m-%d"),
            nums=nums[:6],
            special=nums[6]
        )

        draws.append(draw)

    return draws

# =========================================================
# 保存数据
# =========================================================

def save_draws(conn, draws):

    for d in draws:

        conn.execute("""

        INSERT OR REPLACE INTO draws(

            issue_no,
            draw_date,
            numbers_json,
            special_number,
            source,
            created_at,
            updated_at

        )

        VALUES(?,?,?,?,?,?,?)

        """, (

            d.issue,
            d.date,
            json.dumps(d.nums),
            d.special,
            "marksix6",
            utc_now(),
            utc_now()

        ))

    conn.commit()

# =========================================================
# 归一化
# =========================================================

def normalize(counter):

    vals = list(counter.values())

    if not vals:

        return {
            i: 0
            for i in range(1, 50)
        }

    mn = min(vals)

    mx = max(vals)

    if mn == mx:

        return {
            k: 1
            for k in counter
        }

    return {
        k: (counter[k] - mn) / (mx - mn)
        for k in counter
    }

# =========================================================
# 核心评分
# =========================================================

def score_numbers(draws, strategy):

    freq = Counter()

    omission = {}

    momentum = Counter()

    for n in range(1, 50):

        freq[n] = 0

        omission[n] = 999

        momentum[n] = 0

    for idx, draw in enumerate(draws):

        weight = 1 / (idx + 1)

        for n in draw:

            freq[n] += 1

            momentum[n] += weight

            if omission[n] == 999:

                omission[n] = idx

    f = normalize(freq)

    o = normalize(omission)

    m = normalize(momentum)

    scores = {}

    for n in range(1, 50):

        if strategy == "balanced":

            s = (
                f[n] * 0.40 +
                o[n] * 0.35 +
                m[n] * 0.25
            )

        elif strategy == "hot":

            s = (
                f[n] * 0.75 +
                m[n] * 0.25
            )

        elif strategy == "cold":

            s = (
                o[n] * 0.75 +
                m[n] * 0.25
            )

        elif strategy == "momentum":

            s = (
                m[n] * 0.85 +
                f[n] * 0.15
            )

        elif strategy == "ensemble":

            s = (
                f[n] * 0.40 +
                o[n] * 0.30 +
                m[n] * 0.30
            ) * 1.15

        elif strategy == "pattern":

            zone = (n - 1) // 10

            zone_bonus = 1.2 if zone in [2, 3] else 1.0

            s = (
                f[n] * 0.35 +
                o[n] * 0.35 +
                m[n] * 0.30
            ) * zone_bonus

        else:

            s = 0

        scores[n] = s

    return scores

# =========================================================
# 生成预测
# =========================================================

def generate_predictions(conn):

    rows = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 80

    """).fetchall()

    draws = [
        json.loads(r["numbers_json"])
        for r in rows
    ]

    latest_issue = rows[0]["issue_no"]

    next_issue = str(int(latest_issue) + 1)

    conn.execute("""

    DELETE FROM predictions
    WHERE issue_no=?

    """, (next_issue,))

    for strategy in STRATEGIES:

        scores = score_numbers(draws, strategy)

        ranked = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        picks = [x[0] for x in ranked[:6]]

        special = ranked[6][0]

        conn.execute("""

        INSERT INTO predictions(

            issue_no,
            strategy,
            numbers_json,
            special_number,
            created_at

        )

        VALUES(?,?,?,?,?)

        """, (

            next_issue,
            strategy,
            json.dumps(picks),
            special,
            utc_now()

        ))

    conn.commit()

    return next_issue

# =========================================================
# 回测
# =========================================================

def review_predictions(conn):

    rows = conn.execute("""

    SELECT *
    FROM predictions
    WHERE reviewed=0

    """).fetchall()

    for row in rows:

        draw = conn.execute("""

        SELECT *
        FROM draws
        WHERE issue_no=?

        """, (row["issue_no"],)).fetchone()

        if not draw:

            continue

        actual = set(
            json.loads(draw["numbers_json"])
        )

        picks = set(
            json.loads(row["numbers_json"])
        )

        hit = len(actual & picks)

        conn.execute("""

        UPDATE predictions

        SET
        hit=?,
        reviewed=1

        WHERE id=?

        """, (

            hit,
            row["id"]

        ))

    conn.commit()

# =========================================================
# 波色
# =========================================================

def color_of(n):

    RED = {
        1,2,7,8,12,13,18,19,23,24,
        29,30,34,35,40,45,46
    }

    BLUE = {
        3,4,9,10,14,15,20,25,26,
        31,36,37,41,42,47,48
    }

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

# =========================================================
# 波色预测
# =========================================================

def predict_color(draws):

    recent = [d[-1] for d in draws[:10]]

    score = {
        "红":0,
        "蓝":0,
        "绿":0
    }

    for idx, n in enumerate(recent):

        c = color_of(n)

        score[c] += (10 - idx)

    ranked = sorted(
        score.items(),
        key=lambda x:x[1],
        reverse=True
    )

    return ranked[0][0], ranked[1][0]

# =========================================================
# 大小单双预测
# =========================================================

def predict_dxds(draws):

    recent = [d[-1] for d in draws[:10]]

    big = 0
    small = 0

    odd = 0
    even = 0

    for n in recent:

        if n >= 25:
            big += 1
        else:
            small += 1

        if n % 2:
            odd += 1
        else:
            even += 1

    size = "大" if big >= small else "小"

    parity = "单" if odd >= even else "双"

    return size, parity

# =========================================================
# 最大连空
# =========================================================

def max_miss(draws):

    recent = [d[-1] for d in draws]

    result = {}

    for color in ["红","蓝","绿"]:

        miss = 0

        maxmiss = 0

        for n in recent:

            if color_of(n) == color:

                miss = 0

            else:

                miss += 1

            maxmiss = max(maxmiss, miss)

        result[color] = maxmiss

    return result

# =========================================================
# 波色收益模拟
# =========================================================

def simulate_profit(draws):

    bankroll = 0

    recent = [d[-1] for d in draws]

    for i in range(10, len(recent)-1):

        history = recent[:i]

        fake_draws = [[x] for x in history[::-1]]

        main, second = predict_color(fake_draws)

        actual = color_of(recent[i])

        bankroll -= 1000

        if actual == "红":
            odds = 2.7
        else:
            odds = 2.8

        if actual == main:

            bankroll += 1000 * odds

    return round(bankroll, 2)

# =========================================================
# 特码属性
# =========================================================

def special_attr(n):

    dx = "大" if n >= 25 else "小"

    ds = "单" if n % 2 else "双"

    color = color_of(n)

    wx = ["土","水","火","木","金"][n % 5]

    return f"{ds}/{dx} {color} {wx}"

# =========================================================
# 最近10期统计
# =========================================================

def print_stats(conn):

    print("\n最近10期历史命中统计:")

    stats = conn.execute("""

    SELECT
    strategy,
    COUNT(*) c,
    ROUND(AVG(hit),2) avg_hit

    FROM predictions

    WHERE id IN (

        SELECT id
        FROM predictions
        WHERE reviewed=1
        ORDER BY id DESC
        LIMIT 10

    )

    GROUP BY strategy

    ORDER BY avg_hit DESC

    """).fetchall()

    for row in stats:

        label = STRATEGY_NAMES.get(
            row["strategy"],
            row["strategy"]
        )

        print(
            f"{label:10s}: "
            f"期数={row['c']} "
            f"平均命中={row['avg_hit']}"
        )

# =========================================================
# 控制台输出
# =========================================================

def dashboard(conn):

    rows = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 80

    """).fetchall()

    draws = [
        json.loads(r["numbers_json"]) + [r["special_number"]]
        for r in rows
    ]

    latest = rows[0]

    print(f"同步完成: {len(rows)} 条")

    profit = simulate_profit(draws)

    print(f"\n累计收益: {profit:.2f}")

    print("\n最新开奖:")

    nums = json.loads(latest["numbers_json"])

    print(
        f"{latest['issue_no']} | "
        + " ".join(f"{x:02d}" for x in nums)
        + f" + {latest['special_number']:02d}"
    )

    next_issue = str(int(latest["issue_no"]) + 1)

    print(f"\n预测期号: {next_issue}")

    preds = conn.execute("""

    SELECT *
    FROM predictions
    WHERE issue_no=?

    ORDER BY id

    """, (next_issue,)).fetchall()

    for p in preds:

        picks = json.loads(p["numbers_json"])

        special = p["special_number"]

        label = STRATEGY_NAMES.get(
            p["strategy"],
            p["strategy"]
        )

        print(
            f"{label:10s}: "
            + " ".join(f"{x:02d}" for x in picks)
            + f" + {special:02d}"
        )

        print(
            f"特码属性: {special_attr(special)}"
        )

    main, second = predict_color(draws)

    print("\n特码波色预测:")

    print(f"主强: {main} 次强: {second}")

    size, parity = predict_dxds(draws)

    print("\n大小单双预测:")

    print(f"大小: {size}")

    print(f"单双: {parity}")

    miss = max_miss(draws)

    print("\n最大连空:")

    for k,v in miss.items():

        print(f"{k}波: {v} 期")

    print_stats(conn)

# =========================================================
# 主同步流程
# =========================================================

def sync():

    conn = connect_db()

    init_db(conn)

    draws = fetch_draws()

    save_draws(conn, draws)

    review_predictions(conn)

    issue = generate_predictions(conn)

    print(f"已生成 {issue} 期预测")

    dashboard(conn)

    conn.close()

# =========================================================
# 主入口
# =========================================================

if __name__ == "__main__":

    sync()