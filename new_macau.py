#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = str(SCRIPT_DIR / "new_macau.db")

# 只抓 新澳门彩
API_URL = "https://api3.marksix6.net/lottery_api.php?type=newMacau"

ALL_NUMBERS = list(range(1, 50))

# =========================
# 真实波色映射（六合彩标准）
# =========================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}


def get_color(num: int) -> str:
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"


def get_element(num: int) -> str:
    tail = num % 10

    if tail in [1, 6]:
        return "水"
    elif tail in [2, 7]:
        return "火"
    elif tail in [3, 8]:
        return "木"
    elif tail in [4, 9]:
        return "金"
    else:
        return "土"


def special_attrs(num: int) -> str:
    ds = "单" if num % 2 else "双"
    dx = "大" if num >= 25 else "小"
    color = get_color(num)
    wx = get_element(num)
    return f"{ds}/{dx} {color} {wx}"


@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


# =========================
# 数据库
# =========================

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER
    )
    """)
    conn.commit()


# =========================
# API 获取真实数据
# =========================

def fetch_data() -> List[DrawRecord]:

    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8")

    payload = json.loads(text)

    records = []

    # 兼容不同结构
    data_list = []

    if isinstance(payload, list):
        data_list = payload

    elif isinstance(payload, dict):

        if "data" in payload:
            data_list = payload["data"]

        elif "list" in payload:
            data_list = payload["list"]

        else:
            data_list = [payload]

    for item in data_list:

        try:

            issue = str(
                item.get("expect")
                or item.get("issue")
                or item.get("issue_no")
                or item.get("period")
                or ""
            )

            open_code = (
                item.get("opencode")
                or item.get("openCode")
                or item.get("number")
                or item.get("numbers")
                or ""
            )

            open_time = (
                item.get("opentime")
                or item.get("openTime")
                or item.get("time")
                or ""
            )

            if not issue:
                continue

            nums = []

            if isinstance(open_code, str):

                open_code = (
                    open_code.replace("+", ",")
                    .replace(" ", ",")
                )

                nums = [
                    int(x)
                    for x in open_code.split(",")
                    if x.strip().isdigit()
                ]

            elif isinstance(open_code, list):
                nums = [int(x) for x in open_code]

            if len(nums) != 7:
                continue

            draw_date = str(open_time)[:10]

            records.append(
                DrawRecord(
                    issue_no=issue,
                    draw_date=draw_date,
                    numbers=nums[:6],
                    special_number=nums[6]
                )
            )

        except Exception:
            continue

    if not records:
        raise RuntimeError("未抓到真实新澳门彩数据")

    return records


# =========================
# 保存数据
# =========================

def save_records(conn, records):

    inserted = 0

    for r in records:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r.issue_no,)
        ).fetchone()

        if exists:
            continue

        conn.execute(
            """
            INSERT INTO draws VALUES(?,?,?,?)
            """,
            (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number
            )
        )

        inserted += 1

    conn.commit()

    return inserted


# =========================
# 最近开奖
# =========================

def latest_rows(conn, limit=200):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT ?
    """, (limit,)).fetchall()

    return rows


# =========================
# 热号
# =========================

def hot_strategy(rows):

    counter = Counter()

    for r in rows[:30]:

        nums = json.loads(r["numbers_json"])

        for n in nums:
            counter[n] += 1

        counter[r["special_number"]] += 1

    hot = [n for n, _ in counter.most_common(12)]

    if len(hot) < 7:
        hot = ALL_NUMBERS.copy()

    main = hot[:6]

    remain = [x for x in hot if x not in main]

    if not remain:
        remain = [x for x in ALL_NUMBERS if x not in main]

    special = random.choice(remain)

    return sorted(main), special


# =========================
# 冷号
# =========================

def cold_strategy(rows):

    counter = Counter()

    for r in rows[:50]:

        nums = json.loads(r["numbers_json"])

        for n in nums:
            counter[n] += 1

        counter[r["special_number"]] += 1

    ranked = sorted(ALL_NUMBERS, key=lambda x: counter[x])

    main = sorted(ranked[:6])

    remain = [x for x in ranked if x not in main]

    if not remain:
        remain = [x for x in ALL_NUMBERS if x not in main]

    special = random.choice(remain)

    return main, special


# =========================
# 动量策略
# =========================

def momentum_strategy(rows):

    score = Counter()

    recent = rows[:10]

    for idx, r in enumerate(recent):

        weight = 10 - idx

        nums = json.loads(r["numbers_json"])

        for n in nums:
            score[n] += weight

        score[r["special_number"]] += weight

    ranked = [n for n, _ in score.most_common(15)]

    if len(ranked) < 7:
        ranked = ALL_NUMBERS.copy()

    main = sorted(ranked[:6])

    remain = [x for x in ranked if x not in main]

    if not remain:
        remain = [x for x in ALL_NUMBERS if x not in main]

    special = random.choice(remain)

    return main, special


# =========================
# 平衡策略
# =========================

def balanced_strategy(rows):

    h, _ = hot_strategy(rows)
    c, _ = cold_strategy(rows)

    mix = sorted(list(set(h[:3] + c[:3])))

    while len(mix) < 6:

        n = random.randint(1, 49)

        if n not in mix:
            mix.append(n)

    mix = sorted(mix[:6])

    remain = [x for x in ALL_NUMBERS if x not in mix]

    special = random.choice(remain)

    return mix, special


# =========================
# 集成策略
# =========================

def ensemble_strategy(rows):

    vote = Counter()

    funcs = [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy
    ]

    for f in funcs:

        nums, sp = f(rows)

        for n in nums:
            vote[n] += 1

        vote[sp] += 0.5

    ranked = [n for n, _ in vote.most_common(15)]

    if len(ranked) < 7:
        ranked = ALL_NUMBERS.copy()

    main = sorted(ranked[:6])

    remain = [x for x in ranked if x not in main]

    if not remain:
        remain = [x for x in ALL_NUMBERS if x not in main]

    special = random.choice(remain)

    return main, special


# =========================
# 真实最近10期波色预测
# 不偷看未来
# =========================

def predict_color(rows):

    specials = [
        r["special_number"]
        for r in rows[:10]
    ]

    colors = [get_color(x) for x in specials]

    c = Counter(colors)

    ranked = c.most_common()

    if not ranked:
        return "蓝", "绿"

    main = ranked[0][0]

    second = ranked[1][0] if len(ranked) >= 2 else ranked[0][0]

    return main, second


# =========================
# 最近10期大小单双
# =========================

def predict_dxds(rows):

    specials = [
        r["special_number"]
        for r in rows[:10]
    ]

    big = sum(1 for x in specials if x >= 25)
    small = len(specials) - big

    odd = sum(1 for x in specials if x % 2 == 1)
    even = len(specials) - odd

    dx = "大" if big >= small else "小"
    ds = "单" if odd >= even else "双"

    return dx, ds


# =========================
# 真实最大连空（最近10期）
# =========================

def max_miss(rows):

    specials = [
        r["special_number"]
        for r in rows[:10]
    ]

    colors = [get_color(x) for x in specials]

    result = {}

    for target in ["红", "蓝", "绿"]:

        miss = 0
        max_m = 0

        for c in colors:

            if c == target:
                miss = 0
            else:
                miss += 1
                max_m = max(max_m, miss)

        result[target] = max_m

    return result


# =========================
# 最近10期真实回测
# 不偷看未来
# =========================

def recent_hit(rows, strategy_func):

    if len(rows) < 20:
        return 0

    total_hit = 0

    tests = 0

    for i in range(10):

        future = rows[i]

        history = rows[i + 1:]

        if len(history) < 10:
            continue

        pred, _ = strategy_func(history)

        actual = json.loads(future["numbers_json"])

        hit = len(set(pred) & set(actual))

        total_hit += hit

        tests += 1

    if tests == 0:
        return 0

    return round(total_hit / tests, 2)


# =========================
# 投注建议
# =========================

def betting_plan(color, dx, ds):

    print("\n推荐投注方案:")

    print(f"{color}: 300 元")
    print(f"{dx}: 200 元")
    print(f"{ds}: 200 元")

    print("\n赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小单双: 1.95")


# =========================
# 展示
# =========================

def show_dashboard(conn):

    rows = latest_rows(conn)

    if not rows:
        print("数据库无数据")
        return

    latest = rows[0]

    nums = json.loads(latest["numbers_json"])

    print("\n最新开奖:")

    print(
        f"{latest['issue_no']} | "
        f"{' '.join(f'{x:02d}' for x in nums)} "
        f"+ {latest['special_number']:02d}"
    )

    next_issue = str(int(latest["issue_no"]) + 1)

    print(f"\n预测期号: {next_issue}")

    strategies = [
        ("组合策略", balanced_strategy),
        ("热号策略", hot_strategy),
        ("冷号回补", cold_strategy),
        ("近期动量", momentum_strategy),
        ("集成投票", ensemble_strategy),
    ]

    for name, func in strategies:

        main, sp = func(rows)

        print(
            f"{name:<12}: "
            f"{' '.join(f'{x:02d}' for x in main)} "
            f"+ {sp:02d}"
        )

        print(
            f"特码属性: {special_attrs(sp)}"
        )

    # 波色预测
    main_color, second_color = predict_color(rows)

    print("\n特码波色预测（最近10期真实数据）:")
    print(f"主强: {main_color} 次强: {second_color}")

    # 大小单双
    dx, ds = predict_dxds(rows)

    print("\n大小单双预测（最近10期真实数据）:")
    print(f"大小: {dx}")
    print(f"单双: {ds}")

    # 最大连空
    miss = max_miss(rows)

    print("\n真实最大连空（最近10期）:")
    print(f"红波: {miss['红']}期")
    print(f"蓝波: {miss['蓝']}期")
    print(f"绿波: {miss['绿']}期")

    betting_plan(main_color, dx, ds)

    # 回测
    print("\n最近10期真实历史命中统计:")

    for name, func in strategies:

        avg = recent_hit(rows, func)

        print(f"{name:<12}: 平均命中 {avg} 个")


# =========================
# sync
# =========================

def sync():

    conn = connect_db()

    init_db(conn)

    try:

        records = fetch_data()

        inserted = save_records(conn, records)

        print(f"同步完成: {inserted} 条")

        show_dashboard(conn)

    except Exception as e:

        print(f"错误: {e}")

    finally:

        conn.close()


# =========================
# show
# =========================

def show():

    conn = connect_db()

    try:
        show_dashboard(conn)

    finally:
        conn.close()


# =========================
# main
# =========================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "cmd",
        choices=["sync", "show"]
    )

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()

    elif args.cmd == "show":
        show()


if __name__ == "__main__":
    main()