#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

DB_FILE = "new_macau.db"

API_URLS = [
    "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "https://api2.marksix6.net/lottery_api.php?type=newMacau",
    "https://api1.marksix6.net/lottery_api.php?type=newMacau",
]

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ALL_NUMS = list(range(1,50))


# =========================
# 数据库
# =========================

def db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        date TEXT,
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

    tail = num % 10

    tail_big_small = "大" if tail >= 5 else "小"

    tail_map = {
        1:"水",6:"水",
        2:"火",7:"火",
        3:"木",8:"木",
        4:"金",9:"金",
        0:"土",5:"土"
    }

    element = tail_map[tail]

    color = get_color(num)

    return (
        f"{odd_even}/{big_small} "
        f"合{total_odd_even}/{total_big_small} "
        f"尾{tail_big_small} "
        f"{color} {element}"
    )


# =========================
# 获取真实新澳门彩数据
# =========================

def fetch_data():

    for url in API_URLS:

        try:

            req = Request(
                url,
                headers={
                    "User-Agent":"Mozilla/5.0"
                }
            )

            res = urlopen(req, timeout=15)

            data = json.loads(res.read().decode("utf-8"))

            if not data:
                continue

            records = []

            for item in data:

                issue = str(item.get("expect","")).strip()

                opencode = item.get("opencode","").strip()

                opentime = item.get("opentime","").strip()

                if not issue or not opencode:
                    continue

                nums = [int(x) for x in opencode.split(",")]

                if len(nums) != 7:
                    continue

                records.append({
                    "issue": issue,
                    "date": opentime[:10],
                    "nums": nums
                })

            if records:
                return records

        except:
            pass

    return []


# =========================
# 保存数据
# =========================

def save_records(conn, records):

    new_count = 0

    for r in records:

        issue = r["issue"]

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue=?",
            (issue,)
        ).fetchone()

        nums = r["nums"]

        if exists:
            continue

        conn.execute("""
        INSERT INTO draws VALUES(
            ?,?,?,?,?,?,?,?,?,?
        )
        """,(
            issue,
            r["date"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            nums[6]
        ))

        new_count += 1

    conn.commit()

    return new_count


# =========================
# 最近数据
# =========================

def recent_draws(conn, limit=10):

    rows = conn.execute("""
    SELECT * FROM draws
    ORDER BY issue DESC
    LIMIT ?
    """,(limit,)).fetchall()

    return rows


# =========================
# 热号
# =========================

def hot_strategy(rows):

    specials = [r["special"] for r in rows]

    c = Counter(specials)

    hot = [x[0] for x in c.most_common(6)]

    while len(hot) < 6:

        n = random.randint(1,49)

        if n not in hot:
            hot.append(n)

    special = hot[0]

    return hot[:6], special


# =========================
# 冷号
# =========================

def cold_strategy(rows):

    specials = [r["special"] for r in rows]

    missing = []

    for n in ALL_NUMS:

        if n not in specials:
            missing.append(n)

    if len(missing) < 6:
        missing += random.sample(ALL_NUMS, 6-len(missing))

    special = missing[0]

    return missing[:6], special


# =========================
# 动量策略
# =========================

def momentum_strategy(rows):

    recent = [r["special"] for r in rows[:5]]

    picks = []

    for n in recent:

        if n not in picks:
            picks.append(n)

    while len(picks) < 6:

        n = random.randint(1,49)

        if n not in picks:
            picks.append(n)

    return picks[:6], recent[0]


# =========================
# 趋势策略
# =========================

def trend_strategy(rows):

    big = 0
    small = 0

    odd = 0
    even = 0

    colors = []

    for r in rows:

        s = r["special"]

        if s >= 25:
            big += 1
        else:
            small += 1

        if s % 2:
            odd += 1
        else:
            even += 1

        colors.append(get_color(s))

    target_color = Counter(colors).most_common(1)[0][0]

    pool = []

    for n in ALL_NUMS:

        if get_color(n) == target_color:
            pool.append(n)

    random.shuffle(pool)

    picks = pool[:6]

    special = picks[0]

    return picks, special


# =========================
# 回补策略
# =========================

def rebound_strategy(rows):

    specials = [r["special"] for r in rows]

    miss = []

    for n in ALL_NUMS:

        if n not in specials:
            miss.append(n)

    random.shuffle(miss)

    if len(miss) < 6:
        miss += random.sample(ALL_NUMS, 6-len(miss))

    return miss[:6], miss[0]


# =========================
# 组合策略
# =========================

def balanced_strategy(rows):

    h,_ = hot_strategy(rows)

    c,_ = cold_strategy(rows)

    mix = []

    for n in h[:3]:
        if n not in mix:
            mix.append(n)

    for n in c[:3]:
        if n not in mix:
            mix.append(n)

    while len(mix) < 6:

        n = random.randint(1,49)

        if n not in mix:
            mix.append(n)

    special = mix[0]

    return mix[:6], special


# =========================
# 集成策略
# =========================

def ensemble_strategy(rows):

    funcs = [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        trend_strategy,
        rebound_strategy,
        balanced_strategy
    ]

    score = Counter()

    for f in funcs:

        nums,_ = f(rows)

        for n in nums:
            score[n] += 1

    picks = [x[0] for x in score.most_common(6)]

    special = picks[0]

    return picks, special


# =========================
# 波色预测（二中一）
# =========================

def predict_colors(rows):

    specials = [r["special"] for r in rows]

    colors = [get_color(x) for x in specials]

    c = Counter(colors)

    ranked = c.most_common()

    main = ranked[0][0]

    second = ranked[1][0] if len(ranked) > 1 else main

    return main, second


# =========================
# 二中一真实回测（不偷看未来）
# =========================

def backtest_color(rows):

    rows = list(rows)

    if len(rows) < 10:
        return 0,0,0

    rows.reverse()

    hit = 0

    max_miss = 0

    current_miss = 0

    total = 0

    for i in range(3, len(rows)):

        history = rows[:i]

        actual = get_color(rows[i]["special"])

        p1,p2 = predict_colors(history[-10:])

        total += 1

        if actual in (p1,p2):

            hit += 1

            current_miss = 0

        else:

            current_miss += 1

            if current_miss > max_miss:
                max_miss = current_miss

    return hit,total,max_miss


# =========================
# 投注方案
# =========================

def betting_plan(main, second):

    print("\n推荐投注方案:")

    print(f"{main}: 300 元")

    print(f"{second}: 150 元")

    print("\n赔率参考:")

    print("红波: 2.7")

    print("蓝/绿波: 2.8")


# =========================
# 展示
# =========================

def show(conn):

    rows = recent_draws(conn, 10)

    if not rows:

        print("暂无数据")

        return

    latest = rows[0]

    issue = latest["issue"]

    nums = [
        latest["n1"],
        latest["n2"],
        latest["n3"],
        latest["n4"],
        latest["n5"],
        latest["n6"]
    ]

    special = latest["special"]

    print("\n最新开奖:")

    print(
        f"{issue} | "
        + " ".join(f"{x:02d}" for x in nums)
        + f" + {special:02d}"
    )

    next_issue = str(int(issue)+1)

    print(f"\n预测期号: {next_issue}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "趋势策略": trend_strategy,
        "回补策略": rebound_strategy,
        "集成投票": ensemble_strategy
    }

    for name,func in strategies.items():

        picks,s = func(rows)

        print(
            f"{name:<12}: "
            + " ".join(f"{x:02d}" for x in picks)
            + f" + {s:02d}"
        )

        print(
            f"特码属性: {special_attributes(s)}"
        )

    p1,p2 = predict_colors(rows)

    print("\n特码波色预测（二中一）:")

    print(f"主强: {p1}")

    print(f"次强: {p2}")

    hit,total,max_miss = backtest_color(rows)

    print("\n最近10期真实回测:")

    print(f"二中一命中: {hit}/{total}")

    rate = round(hit/total*100,1) if total else 0

    print(f"命中率: {rate}%")

    print("\n真实最大连空:")

    print(f"{max_miss} 期")

    betting_plan(p1,p2)


# =========================
# 同步
# =========================

def sync():

    conn = db_conn()

    records = fetch_data()

    if not records:

        print("未抓到真实开奖数据")

        return

    new_count = save_records(conn, records)

    print(f"同步完成: {new_count} 条")

    show(conn)

    conn.close()


# =========================
# 主程序
# =========================

def main():

    if len(sys.argv) == 1:

        sync()

        return

    cmd = sys.argv[1]

    if cmd == "sync":
        sync()

    elif cmd == "show":

        conn = db_conn()

        show(conn)

        conn.close()

    else:
        sync()


if __name__ == "__main__":
    main()