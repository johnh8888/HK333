#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import random
import requests
from collections import Counter
from datetime import datetime

DB_FILE = "new_macau.db"

API_URLS = [
    "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "https://api2.marksix6.net/lottery_api.php?type=newMacau",
    "https://api1.marksix6.net/lottery_api.php?type=newMacau",
]

# =========================
# 新澳门彩真实波色
# =========================
RED = {
    1, 2, 7, 8, 12, 13, 18, 19, 23, 24,
    29, 30, 34, 35, 40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25, 26,
    31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27, 28,
    32, 33, 38, 39, 43, 44, 49
}

# =========================
# 五行
# =========================
def get_element(num):
    tail = num % 10

    if tail in [1, 6]:
        return "水"

    if tail in [2, 7]:
        return "火"

    if tail in [3, 8]:
        return "木"

    if tail in [4, 9]:
        return "金"

    return "土"

# =========================
# 波色
# =========================
def get_color(num):
    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"

# =========================
# 特码属性
# =========================
def special_attributes(num):

    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    total = sum(map(int, str(num)))

    total_odd_even = "单" if total % 2 else "双"

    total_big_small = "大" if total >= 7 else "小"

    tail_big_small = "大" if num % 10 >= 5 else "小"

    color = get_color(num)

    element = get_element(num)

    return (
        f"{odd_even}/{big_small} "
        f"合{total_odd_even}/{total_big_small} "
        f"尾{tail_big_small} "
        f"{color} {element}"
    )

# =========================
# 数据库
# =========================
def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        numbers TEXT,
        special INTEGER,
        created TEXT
    )
    """)

    conn.commit()

    return conn

# =========================
# 获取真实数据
# =========================
def fetch_data():

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for url in API_URLS:

        try:
            r = requests.get(url, headers=headers, timeout=15)

            data = r.json()

            if not data:
                continue

            rows = []

            for item in data:

                issue = str(item.get("expect", "")).strip()

                opencode = str(item.get("opencode", "")).strip()

                if not issue or not opencode:
                    continue

                nums = [int(x) for x in opencode.split(",")]

                if len(nums) != 7:
                    continue

                rows.append({
                    "issue": issue,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

            if rows:
                return rows

        except Exception:
            continue

    return []

# =========================
# 保存数据
# =========================
def save_records(conn, rows):

    count = 0

    for row in rows:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue=?",
            (row["issue"],)
        ).fetchone()

        if exists:
            continue

        conn.execute(
            "INSERT INTO draws(issue,numbers,special,created) VALUES(?,?,?,?)",
            (
                row["issue"],
                json.dumps(row["numbers"]),
                row["special"],
                datetime.now().isoformat()
            )
        )

        count += 1

    conn.commit()

    return count

# =========================
# 读取最近数据
# =========================
def get_recent_draws(conn, limit=10):

    rows = conn.execute("""
    SELECT * FROM draws
    ORDER BY issue DESC
    LIMIT ?
    """, (limit,)).fetchall()

    result = []

    for row in rows:

        result.append({
            "issue": row[0],
            "numbers": json.loads(row[1]),
            "special": row[2]
        })

    return result

# =========================
# 热号策略
# =========================
def hot_strategy(rows):

    counter = Counter()

    for r in rows:
        counter.update(r["numbers"])

    hot = [n for n, _ in counter.most_common(6)]

    special = rows[0]["special"]

    return hot, special

# =========================
# 冷号策略
# =========================
def cold_strategy(rows):

    counter = Counter()

    for r in rows:
        counter.update(r["numbers"])

    all_nums = set(range(1, 50))

    missing = []

    for n in all_nums:

        if n not in counter:
            missing.append(n)

    if len(missing) < 6:

        remain = sorted(counter.items(), key=lambda x: x[1])

        for n, _ in remain:

            if n not in missing:
                missing.append(n)

            if len(missing) >= 6:
                break

    special = random.choice(missing)

    return missing[:6], special

# =========================
# 组合策略
# =========================
def balanced_strategy(rows):

    hot, _ = hot_strategy(rows)

    cold, _ = cold_strategy(rows)

    picks = hot[:3] + cold[:3]

    special = random.choice(picks)

    return picks, special

# =========================
# 波色预测（二中一）
# =========================
def predict_colors(rows):

    specials = [r["special"] for r in rows]

    colors = [get_color(x) for x in specials]

    count = Counter(colors)

    ranked = sorted(
        count.items(),
        key=lambda x: x[1],
        reverse=True
    )

    if len(ranked) == 1:
        return ranked[0][0], ranked[0][0]

    return ranked[0][0], ranked[1][0]

# =========================
# 二中一真实回测
# 不偷看未来
# =========================
def backtest_color_hit(rows):

    if len(rows) < 10:
        return 0, 0

    hits = 0
    total = 0

    ordered = list(reversed(rows))

    for i in range(3, len(ordered)):

        history = ordered[:i]

        current = ordered[i]

        c1, c2 = predict_colors(history[-10:])

        actual = get_color(current["special"])

        if actual in [c1, c2]:
            hits += 1

        total += 1

    return hits, total

# =========================
# 真实最大连空
# =========================
def max_miss(rows):

    ordered = list(reversed(rows))

    miss = 0

    max_miss = 0

    for i in range(3, len(ordered)):

        history = ordered[:i]

        current = ordered[i]

        c1, c2 = predict_colors(history[-10:])

        actual = get_color(current["special"])

        if actual in [c1, c2]:

            if miss > max_miss:
                max_miss = miss

            miss = 0

        else:
            miss += 1

    if miss > max_miss:
        max_miss = miss

    return max_miss

# =========================
# 显示
# =========================
def show_result(conn):

    rows = get_recent_draws(conn, 10)

    if not rows:
        print("暂无数据")
        return

    latest = rows[0]

    print("\n最新开奖:")

    print(
        f"{latest['issue']} | "
        + " ".join(f"{x:02d}" for x in latest["numbers"])
        + f" + {latest['special']:02d}"
    )

    next_issue = str(int(latest["issue"]) + 1)

    print(f"\n预测期号: {next_issue}")

    # =====================
    # 策略
    # =====================

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
    }

    for name, func in strategies.items():

        nums, special = func(rows)

        print(
            f"{name:<12}: "
            + " ".join(f"{x:02d}" for x in nums)
            + f" + {special:02d}"
        )

        print(
            f"特码属性: {special_attributes(special)}"
        )

    # =====================
    # 波色预测
    # =====================

    c1, c2 = predict_colors(rows)

    print("\n特码波色预测（二中一）:")

    print(f"主强: {c1}")

    print(f"次强: {c2}")

    # =====================
    # 回测
    # =====================

    hits, total = backtest_color_hit(rows)

    print("\n最近10期真实回测:")

    print(f"二中一命中: {hits}/{total}")

    # =====================
    # 最大连空
    # =====================

    miss = max_miss(rows)

    print("\n真实最大连空:")

    print(f"{miss} 期")

    # =====================
    # 投注
    # =====================

    print("\n推荐投注方案:")

    print(f"{c1}: 300 元")

    print(f"{c2}: 150 元")

    # =====================
    # 赔率
    # =====================

    print("\n赔率参考:")

    print("红波: 2.7")

    print("蓝/绿波: 2.8")

# =========================
# 同步
# =========================
def sync():

    conn = init_db()

    rows = fetch_data()

    if not rows:
        print("未抓到真实开奖数据")
        return

    count = save_records(conn, rows)

    print(f"同步完成: {count} 条")

    show_result(conn)

# =========================
# 主程序
# =========================
def main():

    import sys

    if len(sys.argv) >= 2:

        cmd = sys.argv[1]

        if cmd == "sync":
            sync()
            return

    sync()

if __name__ == "__main__":
    main()