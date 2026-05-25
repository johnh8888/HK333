#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from urllib.request import Request, urlopen

DB_FILE = "new_macau.db"

# 新澳门彩真实波色
RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40, 45, 46
}

BLUE_WAVE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN_WAVE = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

STRATEGY_LABELS = {
    "balanced": "组合策略",
    "hot": "热号策略",
    "cold": "冷号回补",
    "momentum": "近期动量",
    "ensemble": "集成投票",
    "pattern": "规律挖掘"
}


# =========================
# 数据库
# =========================
def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        issue TEXT PRIMARY KEY,
        draw_date TEXT,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER
    )
    """)

    conn.commit()
    return conn


# =========================
# 波色
# =========================
def get_wave(num):
    if num in RED_WAVE:
        return "红"
    if num in BLUE_WAVE:
        return "蓝"
    return "绿"


# =========================
# 特码属性
# =========================
def get_special_attr(num):
    odd_even = "单" if num % 2 else "双"
    big_small = "大" if num >= 25 else "小"

    total = sum(map(int, str(num)))
    total_oe = "单" if total % 2 else "双"
    total_bs = "大" if total >= 7 else "小"

    tail = num % 10
    tail_bs = "大" if tail >= 5 else "小"

    wave = get_wave(num)

    if tail in [1, 6]:
        element = "水"
    elif tail in [2, 7]:
        element = "火"
    elif tail in [3, 8]:
        element = "木"
    elif tail in [4, 9]:
        element = "金"
    else:
        element = "土"

    return (
        f"{odd_even}/{big_small} "
        f"合{total_oe}/{total_bs} "
        f"尾{tail_bs} "
        f"{wave} {element}"
    )


# =========================
# 获取真实数据
# =========================
def fetch_real_data():
    urls = [
        "https://marksix6.net/index.php?api=1",
        "https://api.macaumarksix.com/history",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for url in urls:
        try:
            req = Request(url, headers=headers)

            with urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8")

            data = json.loads(raw)

            # marksix6
            if "lottery_data" in data:
                for item in data["lottery_data"]:
                    if item.get("name") == "新澳门彩":

                        result = []

                        for row in item.get("history", [])[:120]:

                            try:
                                issue, nums = row.split("期：")

                                nums = [
                                    int(x.strip())
                                    for x in nums.split(",")
                                ]

                                if len(nums) != 7:
                                    continue

                                result.append({
                                    "issue": issue.strip(),
                                    "numbers": nums[:6],
                                    "special": nums[6]
                                })

                            except:
                                continue

                        if result:
                            return result

        except:
            continue

    return []


# =========================
# 保存数据
# =========================
def save_records(conn, records):
    count = 0

    for r in records:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue=?",
            (r["issue"],)
        ).fetchone()

        if exists:
            continue

        nums = r["numbers"]

        conn.execute("""
        INSERT INTO draws VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            r["issue"],
            str(datetime.now())[:19],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            r["special"]
        ))

        count += 1

    conn.commit()
    return count


# =========================
# 获取历史数据
# =========================
def load_draws(conn, limit=120):
    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT ?
    """, (limit,)).fetchall()

    result = []

    for r in rows:
        result.append({
            "issue": r[0],
            "numbers": [r[2], r[3], r[4], r[5], r[6], r[7]],
            "special": r[8]
        })

    return result


# =========================
# 预测策略
# =========================
def hot_strategy(draws):
    c = Counter()

    for d in draws[:20]:
        for n in d["numbers"]:
            c[n] += 1

    return [x[0] for x in c.most_common(6)]


def cold_strategy(draws):
    c = Counter()

    for d in draws[:50]:
        for n in d["numbers"]:
            c[n] += 1

    arr = sorted(c.items(), key=lambda x: x[1])

    return [x[0] for x in arr[:6]]


def momentum_strategy(draws):
    score = Counter()

    for idx, d in enumerate(draws[:15]):
        weight = 15 - idx

        for n in d["numbers"]:
            score[n] += weight

    return [x[0] for x in score.most_common(6)]


def balanced_strategy(draws):
    hot = hot_strategy(draws)[:3]
    cold = cold_strategy(draws)[:2]
    mom = momentum_strategy(draws)[:1]

    result = []

    for n in hot + cold + mom:
        if n not in result:
            result.append(n)

    return result[:6]


def ensemble_strategy(draws):
    score = Counter()

    for n in hot_strategy(draws):
        score[n] += 3

    for n in cold_strategy(draws):
        score[n] += 2

    for n in momentum_strategy(draws):
        score[n] += 4

    return [x[0] for x in score.most_common(6)]


def pattern_strategy(draws):
    recent = draws[:30]

    odd = []
    even = []

    for d in recent:
        for n in d["numbers"]:
            if n % 2:
                odd.append(n)
            else:
                even.append(n)

    result = []

    result.extend(odd[:3])
    result.extend(even[:3])

    seen = []
    for n in result:
        if n not in seen:
            seen.append(n)

    return seen[:6]


# =========================
# 波色预测
# =========================
def predict_wave(draws):
    recent = draws[:10]

    waves = []

    for d in recent:
        waves.append(get_wave(d["special"]))

    c = Counter(waves)

    top = c.most_common(2)

    if len(top) < 2:
        return "红", "蓝"

    return top[0][0], top[1][0]


# =========================
# 二中一回测（特码）
# 不偷看未来
# =========================
def backtest_wave(draws):

    total = 0
    hit = 0

    streak = 0
    max_streak = 0

    for i in range(10, len(draws)):

        history = draws[i-10:i]

        p1, p2 = predict_wave(history)

        actual = get_wave(draws[i]["special"])

        total += 1

        if actual in [p1, p2]:
            hit += 1
            streak = 0
        else:
            streak += 1
            max_streak = max(max_streak, streak)

    return hit, total, max_streak


# =========================
# 显示
# =========================
def show_dashboard(conn):

    draws = load_draws(conn)

    if not draws:
        print("暂无数据")
        return

    latest = draws[0]

    issue = latest["issue"]

    nums = " ".join(
        f"{x:02d}"
        for x in latest["numbers"]
    )

    special = latest["special"]

    print()
    print("最新开奖:")
    print(f"{issue} | {nums} + {special:02d}")

    next_issue = str(int(issue) + 1)

    print()
    print(f"预测期号: {next_issue}")

    strategies = {
        "balanced": balanced_strategy(draws),
        "hot": hot_strategy(draws),
        "cold": cold_strategy(draws),
        "momentum": momentum_strategy(draws),
        "ensemble": ensemble_strategy(draws),
        "pattern": pattern_strategy(draws),
    }

    for key, nums in strategies.items():

        special_num = nums[0]

        print(
            f"{STRATEGY_LABELS[key]:<12}: "
            f"{' '.join(f'{x:02d}' for x in nums)} "
            f"+ {special_num:02d}"
        )

        print(
            f"特码属性: "
            f"{get_special_attr(special_num)}"
        )

    print()

    # 波色预测
    p1, p2 = predict_wave(draws)

    print("特码波色预测（二中一）:")
    print(f"主强: {p1}")
    print(f"次强: {p2}")

    print()

    # 回测
    hit, total, max_streak = backtest_wave(draws)

    print("最近10期真实回测:")
    print(f"二中一命中: {hit}/{total}")

    if total > 0:
        rate = round(hit * 100 / total, 1)
        print(f"命中率: {rate}%")

    print()

    print("真实最大连空:")
    print(f"{max_streak} 期")

    print()

    print("推荐投注方案:")

    if p1 == "红":
        print("红: 300 元")
        print(f"{p2}: 150 元")
    elif p1 == "蓝":
        print("蓝: 300 元")
        print(f"{p2}: 150 元")
    else:
        print("绿: 300 元")
        print(f"{p2}: 150 元")

    print()

    print("赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")


# =========================
# 同步
# =========================
def sync():

    conn = init_db()

    records = fetch_real_data()

    if not records:
        print("未抓到真实开奖数据")
        return

    count = save_records(conn, records)

    print(f"同步完成: {count} 条")

    show_dashboard(conn)


# =========================
# main
# =========================
def main():

    if len(sys.argv) >= 2:

        if sys.argv[1] == "sync":
            sync()
            return

    sync()


if __name__ == "__main__":
    main()