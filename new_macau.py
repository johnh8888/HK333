#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen

# =========================================================
# 基础配置
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "new_macau.db"

API_URLS = [
    "https://marksix6.net/index.php?api=1",
]

ALL_NUMBERS = list(range(1, 50))

STRATEGIES = {
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
    numbers: List[int]
    special: int

# =========================================================
# 工具
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def get_color(n: int):
    if 1 <= n <= 16:
        return "红"
    elif 17 <= n <= 32:
        return "蓝"
    return "绿"

def get_size(n: int):
    return "大" if n >= 25 else "小"

def get_odd_even(n: int):
    return "单" if n % 2 else "双"

def special_attributes(n: int):
    tail = n % 10

    if tail in (1, 6):
        element = "水"
    elif tail in (2, 7):
        element = "火"
    elif tail in (3, 8):
        element = "木"
    elif tail in (4, 9):
        element = "金"
    else:
        element = "土"

    return {
        "单双": get_odd_even(n),
        "大小": get_size(n),
        "色波": get_color(n),
        "五行": element,
    }

# =========================================================
# 数据库
# =========================================================

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        date TEXT,
        numbers TEXT,
        special INTEGER,
        created_at TEXT,
        updated_at TEXT,
        source TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT,
        strategy TEXT,
        numbers TEXT,
        special INTEGER,
        hit INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    conn.commit()

# =========================================================
# 获取数据
# =========================================================

def fetch_latest():

    for url in API_URLS:

        try:

            req = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))

            lottery = next(
                x for x in data["lottery_data"]
                if x["name"] == "新澳门彩"
            )

            history = lottery["history"]

            result = []

            for row in history[:80]:

                issue, nums = row.split("期：")

                nums = [int(x.strip()) for x in nums.split(",")]

                result.append(
                    Draw(
                        issue=issue.strip(),
                        date=datetime.now().strftime("%Y-%m-%d"),
                        numbers=nums[:6],
                        special=nums[6]
                    )
                )

            return result

        except:
            pass

    raise RuntimeError("无法获取最新数据")

# =========================================================
# 保存数据
# =========================================================

def save_draws(conn, draws):

    now = utc_now()

    for d in draws:

        conn.execute("""
        INSERT OR REPLACE INTO draws(
            issue,
            date,
            numbers,
            special,
            created_at,
            updated_at,
            source
        )
        VALUES(?,?,?,?,?,?,?)
        """, (
            d.issue,
            d.date,
            json.dumps(d.numbers),
            d.special,
            now,
            now,
            "api"
        ))

    conn.commit()

# =========================================================
# 数据分析
# =========================================================

def normalize(score_map):

    vals = list(score_map.values())

    mn = min(vals)
    mx = max(vals)

    if mx == mn:
        return {k: 0 for k in score_map}

    return {
        k: (v - mn) / (mx - mn)
        for k, v in score_map.items()
    }

def freq_map(draws):

    score = {n: 0 for n in ALL_NUMBERS}

    for d in draws:
        for n in d:
            score[n] += 1

    return score

# 修复 omission
def omission_map(draws):

    score = {}

    for n in ALL_NUMBERS:

        miss = 0

        found = False

        for d in draws:

            if n in d:
                found = True
                break

            miss += 1

        if not found:
            miss = len(draws)

        score[n] = miss

    return score

def momentum_map(draws):

    score = {n: 0 for n in ALL_NUMBERS}

    for idx, d in enumerate(draws):

        w = 1 / (idx + 1)

        for n in d:
            score[n] += w

    return score

# =========================================================
# 选号
# =========================================================

def pick_top(scores, count=6):

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    result = []

    for n, _ in ranked:

        if n not in result:
            result.append(n)

        if len(result) >= count:
            break

    return result

# =========================================================
# 策略
# =========================================================

def strategy_scores(draws, mode):

    freq = normalize(freq_map(draws))
    omit = normalize(omission_map(draws))
    mom = normalize(momentum_map(draws))

    scores = {}

    for n in ALL_NUMBERS:

        if mode == "hot":
            s = freq[n] * 0.7 + mom[n] * 0.3

        elif mode == "cold":
            s = omit[n] * 0.7 + mom[n] * 0.3

        elif mode == "momentum":
            s = mom[n]

        elif mode == "pattern":
            s = (
                freq[n] * 0.4 +
                omit[n] * 0.3 +
                mom[n] * 0.3
            )

        elif mode == "ensemble":
            s = (
                freq[n] * 0.33 +
                omit[n] * 0.33 +
                mom[n] * 0.34
            )

        else:
            s = (
                freq[n] * 0.4 +
                omit[n] * 0.3 +
                mom[n] * 0.3
            )

        scores[n] = s

    return scores

# =========================================================
# 波色预测（修复版）
# =========================================================

def predict_color(specials):

    recent = specials[-10:]

    hot = Counter(get_color(x) for x in recent)

    omission = {}

    for color in ["红", "蓝", "绿"]:

        miss = 0

        for x in reversed(recent):

            if get_color(x) == color:
                break

            miss += 1

        omission[color] = miss

    score = {}

    for color in ["红", "蓝", "绿"]:

        heat = hot[color] / 10

        omit = omission[color] / 10

        score[color] = heat * 0.7 + omit * 0.3

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[0][0], ranked[1][0]

# =========================================================
# 大小单双
# =========================================================

def predict_size(specials):

    recent = specials[-10:]

    big = sum(1 for x in recent if x >= 25)
    small = 10 - big

    return "大" if small > big else "小"

def predict_odd_even(specials):

    recent = specials[-10:]

    odd = sum(1 for x in recent if x % 2)

    even = 10 - odd

    return "单" if even > odd else "双"

# =========================================================
# 最近10期最大连空（修复版）
# =========================================================

def max_miss_streak(specials):

    colors = [get_color(x) for x in specials[-10:]]

    result = {}

    for target in ["红", "蓝", "绿"]:

        miss = 0
        max_miss = 0

        for c in colors:

            if c != target:
                miss += 1
                max_miss = max(max_miss, miss)
            else:
                miss = 0

        result[target] = max_miss

    return result

# =========================================================
# 保存预测
# =========================================================

def save_prediction(conn, issue, strategy, nums, special):

    conn.execute("""
    INSERT INTO predictions(
        issue,
        strategy,
        numbers,
        special,
        created_at
    )
    VALUES(?,?,?,?,?)
    """, (
        issue,
        strategy,
        json.dumps(nums),
        special,
        utc_now()
    ))

    conn.commit()

# =========================================================
# 回测
# =========================================================

def review_predictions(conn):

    rows = conn.execute("""
    SELECT *
    FROM predictions
    ORDER BY id DESC
    LIMIT 200
    """).fetchall()

    for r in rows:

        draw = conn.execute("""
        SELECT *
        FROM draws
        WHERE issue=?
        """, (r["issue"],)).fetchone()

        if not draw:
            continue

        actual = set(json.loads(draw["numbers"]))

        pred = set(json.loads(r["numbers"]))

        hit = len(actual & pred)

        conn.execute("""
        UPDATE predictions
        SET hit=?
        WHERE id=?
        """, (hit, r["id"]))

    conn.commit()

# =========================================================
# 收益计算
# =========================================================

def calc_profit(conn):

    rows = conn.execute("""
    SELECT *
    FROM predictions
    ORDER BY id DESC
    LIMIT 10
    """).fetchall()

    profit = 0

    for r in rows:

        hit = r["hit"]

        if hit >= 3:
            profit += 800
        else:
            profit -= 1000

    return profit

# =========================================================
# 投注方案（动态）
# =========================================================

def betting_plan(main_color, second_color, size, odd_even):

    bankroll = 1000

    color_main = 0.45
    color_second = 0.15
    size_ratio = 0.20
    odd_ratio = 0.20

    return {
        main_color: int(bankroll * color_main),
        second_color: int(bankroll * color_second),
        size: int(bankroll * size_ratio),
        odd_even: int(bankroll * odd_ratio),
    }

# =========================================================
# 展示
# =========================================================

def show_dashboard(conn):

    latest = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT 1
    """).fetchone()

    specials = [
        x["special"]
        for x in conn.execute("""
        SELECT special
        FROM draws
        ORDER BY issue
        """).fetchall()
    ]

    print(f"同步完成: {conn.execute('SELECT COUNT(*) FROM draws').fetchone()[0]} 条")

    print()

    print(f"累计收益: {calc_profit(conn):.2f}")

    print()

    print("最新开奖:")

    nums = json.loads(latest["numbers"])

    print(
        f"{latest['issue']} | "
        + " ".join(f"{x:02d}" for x in nums)
        + f" + {latest['special']:02d}"
    )

    print()

    next_issue = str(int(latest["issue"]) + 1)

    print(f"预测期号: {next_issue}")

    draws = [
        json.loads(x["numbers"])
        for x in conn.execute("""
        SELECT numbers
        FROM draws
        ORDER BY issue DESC
        LIMIT 80
        """).fetchall()
    ]

    for strategy, label in STRATEGIES.items():

        scores = strategy_scores(draws, strategy)

        nums = pick_top(scores)

        special = max(
            [x for x in ALL_NUMBERS if x not in nums],
            key=lambda x: scores[x]
        )

        save_prediction(
            conn,
            next_issue,
            strategy,
            nums,
            special
        )

        print(
            f"{label:<10}: "
            + " ".join(f"{x:02d}" for x in nums)
            + f" + {special:02d}"
        )

        attr = special_attributes(special)

        print(
            f"特码属性: "
            f"{attr['单双']}/"
            f"{attr['大小']} "
            f"{attr['色波']} "
            f"{attr['五行']}"
        )

    print()

    main_color, second_color = predict_color(specials)

    print("特码波色预测:")
    print(f"主强: {main_color} 次强: {second_color}")

    print()

    size = predict_size(specials)
    odd_even = predict_odd_even(specials)

    print("大小单双预测:")
    print(f"大小: {size}")
    print(f"单双: {odd_even}")

    print()

    print("最大连空:")

    streak = max_miss_streak(specials)

    for k, v in streak.items():
        print(f"{k}波: {v}期")

    print()

    print("推荐投注方案:")

    plan = betting_plan(
        main_color,
        second_color,
        size,
        odd_even
    )

    for k, v in plan.items():
        print(f"{k}: {v} 元")

    print()

    print("最近10期历史命中统计:")

    stats = conn.execute("""
    SELECT
        strategy,
        COUNT(*) cnt,
        AVG(hit) avg_hit
    FROM (
        SELECT *
        FROM predictions
        ORDER BY id DESC
        LIMIT 60
    )
    GROUP BY strategy
    """).fetchall()

    for s in stats:

        label = STRATEGIES.get(
            s["strategy"],
            s["strategy"]
        )

        print(
            f"{label:<10}: "
            f"期数={s['cnt']} "
            f"平均命中={round(s['avg_hit'],2)}"
        )

# =========================================================
# 主逻辑
# =========================================================

def sync():

    conn = connect_db()

    init_db(conn)

    draws = fetch_latest()

    save_draws(conn, draws)

    review_predictions(conn)

    latest = conn.execute("""
    SELECT issue
    FROM draws
    ORDER BY issue DESC
    LIMIT 1
    """).fetchone()

    next_issue = str(int(latest["issue"]) + 1)

    print(f"已生成 {next_issue} 期预测")

    show_dashboard(conn)

    conn.close()

# =========================================================
# CLI
# =========================================================

def main():

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "cmd",
        choices=["sync"]
    )

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()

if __name__ == "__main__":
    main()