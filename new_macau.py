#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "new_macau.db"

# =========================
# 新澳门彩真实接口
# =========================

API_URLS = [
    "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "https://marksix6.net/index.php?api=1"
]

ALL_NUMBERS = list(range(1, 50))

# =========================
# 真实波色映射
# =========================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20,
    25, 26, 31, 36, 37, 41,
    42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21,
    22, 27, 28, 32, 33,
    38, 39, 43, 44, 49
}


# =========================
# 波色
# =========================

def get_color(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"


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
        issue TEXT PRIMARY KEY,
        numbers TEXT,
        special INTEGER,
        created TEXT
    )
    """)

    conn.commit()


# =========================
# 在线获取JSON
# =========================

def load_json(url):

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(req, timeout=20) as r:

        text = r.read().decode(
            "utf-8",
            "ignore"
        )

    return json.loads(text)


# =========================
# 解析接口
# =========================

def parse_records(payload):

    data = []

    # 直接数组
    if isinstance(payload, list):

        data = payload

    # dict
    elif isinstance(payload, dict):

        if "data" in payload:

            data = payload["data"]

        elif "result" in payload:

            data = payload["result"]

        elif "list" in payload:

            data = payload["list"]

        elif "lottery_data" in payload:

            for item in payload["lottery_data"]:

                name = str(
                    item.get("name", "")
                )

                # 只要新澳门彩
                if "新澳门" not in name:
                    continue

                history = item.get(
                    "history",
                    []
                )

                for row in history:

                    try:

                        left, right = row.split("期：")

                        issue = left.strip()

                        nums = [
                            int(x)
                            for x in right.split(",")
                        ]

                        if len(nums) != 7:
                            continue

                        data.append({
                            "issue": issue,
                            "numbers": nums
                        })

                    except:
                        pass

    records = []

    for row in data:

        try:

            issue = str(
                row.get("issue")
                or row.get("expect")
                or row.get("term")
                or row.get("draw")
                or row.get("drawNo")
            )

            nums = (
                row.get("numbers")
                or row.get("openCode")
                or row.get("code")
                or row.get("result")
            )

            if isinstance(nums, str):

                nums = nums.replace(
                    "+",
                    ","
                ).split(",")

            nums = [
                int(x)
                for x in nums
            ]

            if len(nums) != 7:
                continue

            records.append({
                "issue": issue,
                "nums": nums[:6],
                "special": nums[6]
            })

        except:
            pass

    return records


# =========================
# 获取真实数据
# =========================

def fetch_real_data():

    for url in API_URLS:

        try:

            payload = load_json(url)

            records = parse_records(payload)

            if records:

                return records

        except Exception as e:

            print(
                f"接口失败: {url} -> {e}"
            )

    return []


# =========================
# 获取最近10期在线数据
# =========================

def get_recent_online_10():

    records = fetch_real_data()

    if not records:
        return []

    cleaned = []

    seen = set()

    for r in records:

        issue = str(r["issue"])

        if issue in seen:
            continue

        seen.add(issue)

        cleaned.append({
            "issue": issue,
            "nums": r["nums"],
            "special": r["special"]
        })

    cleaned.sort(
        key=lambda x: int(x["issue"]),
        reverse=True
    )

    return cleaned[:10]


# =========================
# 保存数据库
# =========================

def save_records(conn, records):

    new_count = 0

    for r in records:

        exists = conn.execute(
            """
            SELECT 1
            FROM draws
            WHERE issue=?
            """,
            (r["issue"],)
        ).fetchone()

        if exists:
            continue

        conn.execute(
            """
            INSERT INTO draws
            VALUES(?,?,?,?)
            """,
            (
                r["issue"],
                json.dumps(r["nums"]),
                r["special"],
                datetime.now(
                    timezone.utc
                ).isoformat()
            )
        )

        new_count += 1

    conn.commit()

    return new_count


# =========================
# 属性
# =========================

def special_attrs(n):

    odd_even = "单" if n % 2 else "双"

    big_small = "大" if n >= 25 else "小"

    color = get_color(n)

    return odd_even, big_small, color


# =========================
# 热号策略
# =========================

def hot_strategy(rows):

    counter = Counter()

    for r in rows:

        counter.update(r["nums"])

    hot = [
        n
        for n, _ in counter.most_common(6)
    ]

    if not hot:

        hot = random.sample(
            ALL_NUMBERS,
            6
        )

    special = random.choice(hot)

    return hot, special


# =========================
# 冷号策略
# =========================

def cold_strategy(rows):

    counter = Counter()

    for r in rows:

        counter.update(r["nums"])

    ranked = sorted(
        counter.items(),
        key=lambda x: x[1]
    )

    cold = [
        n
        for n, _ in ranked[:6]
    ]

    while len(cold) < 6:

        x = random.randint(1, 49)

        if x not in cold:
            cold.append(x)

    special = cold[0]

    return cold[:6], special


# =========================
# 平衡策略
# =========================

def balanced_strategy(rows):

    h, _ = hot_strategy(rows)

    c, _ = cold_strategy(rows)

    nums = list(
        dict.fromkeys(
            h[:3] + c[:3]
        )
    )

    while len(nums) < 6:

        x = random.randint(1, 49)

        if x not in nums:
            nums.append(x)

    special = random.choice(nums)

    return nums, special


# =========================
# 波色预测（二中一）
# =========================

def predict_color(rows):

    specials = [
        r["special"]
        for r in rows[:10]
    ]

    counter = Counter(
        get_color(x)
        for x in specials
    )

    ranked = counter.most_common()

    if len(ranked) >= 2:

        return ranked[0][0], ranked[1][0]

    if len(ranked) == 1:

        return ranked[0][0], "蓝"

    return "红", "蓝"


# =========================
# 最近10期真实回测
# 不偷看未来
# =========================

def color_backtest(rows):

    hits = 0

    total = 0

    for i in range(len(rows)-1):

        future = rows[i]

        history = rows[i+1:i+11]

        if len(history) < 3:
            continue

        c1, c2 = predict_color(
            history
        )

        actual = get_color(
            future["special"]
        )

        # 二中一
        if actual in (c1, c2):

            hits += 1

        total += 1

    return hits, total


# =========================
# 最大连空
# =========================

def max_miss(rows):

    miss = 0

    best = 0

    for i in range(len(rows)-1):

        future = rows[i]

        history = rows[i+1:i+11]

        if len(history) < 3:
            continue

        c1, c2 = predict_color(
            history
        )

        actual = get_color(
            future["special"]
        )

        if actual in (c1, c2):

            miss = 0

        else:

            miss += 1

        if miss > best:

            best = miss

    return best


# =========================
# 推荐投注
# =========================

def betting_plan(c1, c2):

    plan = {}

    plan[c1] = 300

    if c2 != c1:

        plan[c2] = 150

    return plan


# =========================
# 主逻辑
# =========================

def sync():

    conn = connect_db()

    init_db(conn)

    records = fetch_real_data()

    if not records:

        print("未抓到真实开奖数据")

        return

    new_count = save_records(
        conn,
        records
    )

    print(f"同步完成: {new_count} 条")

    rows = get_recent_online_10()

    if not rows:

        print("无法获取最近10期")

        return

    latest = rows[0]

    nums = " ".join(
        f"{x:02d}"
        for x in latest["nums"]
    )

    print("\n最新开奖:")

    print(
        f'{latest["issue"]} | '
        f'{nums} + {latest["special"]:02d}'
    )

    next_issue = str(
        int(latest["issue"]) + 1
    )

    print(f"\n预测期号: {next_issue}")

    strategies = [
        ("组合策略", balanced_strategy),
        ("热号策略", hot_strategy),
        ("冷号回补", cold_strategy),
    ]

    for name, func in strategies:

        nums, special = func(rows)

        nums_str = " ".join(
            f"{x:02d}"
            for x in nums
        )

        print(
            f"{name:<12}: "
            f"{nums_str} + {special:02d}"
        )

        oe, bs, color = special_attrs(
            special
        )

        print(
            f"特码属性: "
            f"{oe}/{bs} {color}"
        )

    # =====================
    # 波色预测
    # =====================

    c1, c2 = predict_color(rows)

    print("\n特码波色预测（二中一）:")

    print(f"主强: {c1}")

    print(f"次强: {c2}")

    # =====================
    # 回测
    # =====================

    hits, total = color_backtest(rows)

    print("\n最近10期真实回测:")

    print(
        f"二中一命中: "
        f"{hits}/{total}"
    )

    # =====================
    # 最大连空
    # =====================

    miss = max_miss(rows)

    print("\n真实最大连空:")

    print(f"{miss} 期")

    # =====================
    # 投注建议
    # =====================

    print("\n推荐投注方案:")

    plan = betting_plan(c1, c2)

    for k, v in plan.items():

        print(f"{k}: {v} 元")

    print("\n赔率参考:")

    print("红波: 2.7")

    print("蓝/绿波: 2.8")

    conn.close()


# =========================
# show
# =========================

def show():

    rows = get_recent_online_10()

    if not rows:

        print("无数据")

        return

    latest = rows[0]

    nums = " ".join(
        f"{x:02d}"
        for x in latest["nums"]
    )

    print(
        f'{latest["issue"]} | '
        f'{nums} + {latest["special"]:02d}'
    )


# =========================
# main
# =========================

def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(
        dest="cmd"
    )

    sub.add_parser("sync")

    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "sync":

        sync()

    elif args.cmd == "show":

        show()


if __name__ == "__main__":

    main()