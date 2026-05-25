#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from urllib.request import Request, urlopen

DB = "new_macau.db"

API_URLS = [
    "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "https://marksix6.net/index.php?api=1"
]

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ALL_NUMS = list(range(1,50))


# =========================
# 数据库
# =========================

def db_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        date TEXT,
        numbers TEXT,
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
# 属性
# =========================

def special_attr(num):

    odd_even = "单" if num % 2 else "双"
    big_small = "大" if num >= 25 else "小"

    return odd_even, big_small, get_color(num)


# =========================
# 获取真实数据
# =========================

def fetch_json(url):

    req = Request(
        url,
        headers={
            "User-Agent":
            "Mozilla/5.0"
        }
    )

    with urlopen(req, timeout=20) as r:

        txt = r.read().decode("utf-8", errors="ignore")

        return json.loads(txt)


def parse_api1(data):

    rows = []

    if isinstance(data, list):

        for x in data:

            try:

                issue = str(
                    x.get("expect")
                    or x.get("issue")
                    or x.get("term")
                )

                nums = (
                    x.get("opencode")
                    or x.get("openCode")
                    or ""
                )

                arr = [
                    int(i)
                    for i in nums.replace("+", ",").split(",")
                    if i.strip()
                ]

                if len(arr) != 7:
                    continue

                rows.append({
                    "issue": issue,
                    "numbers": arr[:6],
                    "special": arr[6]
                })

            except:
                pass

    return rows


def parse_api2(data):

    rows = []

    try:

        lottery_data = data.get("lottery_data", [])

        target = None

        for x in lottery_data:

            if x.get("name") == "新澳门彩":
                target = x
                break

        if not target:
            return []

        history = target.get("history", [])

        for item in history:

            try:

                sp = item.split("期：")

                issue = sp[0].strip()

                arr = [
                    int(i)
                    for i in sp[1].split(",")
                ]

                if len(arr) != 7:
                    continue

                rows.append({
                    "issue": issue,
                    "numbers": arr[:6],
                    "special": arr[6]
                })

            except:
                pass

    except:
        pass

    return rows


def fetch_latest():

    for url in API_URLS:

        try:

            data = fetch_json(url)

            rows = parse_api1(data)

            if not rows:
                rows = parse_api2(data)

            if rows:
                return rows

        except:
            pass

    return []


# =========================
# 保存数据
# =========================

def save_records(conn, rows):

    count = 0

    for r in rows:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue=?",
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
                datetime.now().strftime("%Y-%m-%d"),
                json.dumps(r["numbers"]),
                r["special"]
            )
        )

        count += 1

    conn.commit()

    return count


# =========================
# 获取历史
# =========================

def load_rows(conn):

    rs = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue
    """).fetchall()

    out = []

    for r in rs:

        out.append({
            "issue": r["issue"],
            "numbers": json.loads(r["numbers"]),
            "special": r["special"]
        })

    return out


# =========================
# 策略
# =========================

def hot_strategy(rows):

    nums = []

    for r in rows[-30:]:
        nums += r["numbers"]

    c = Counter(nums)

    top = [
        x[0]
        for x in c.most_common(12)
    ]

    main = sorted(top[:6])

    special = top[0]

    return main, special


def cold_strategy(rows):

    nums = []

    for r in rows[-50:]:
        nums += r["numbers"]

    c = Counter(nums)

    miss = []

    for n in ALL_NUMS:
        miss.append(
            (n, c.get(n, 0))
        )

    miss.sort(key=lambda x: x[1])

    top = [x[0] for x in miss[:12]]

    main = sorted(top[:6])

    special = top[0]

    return main, special


def balanced_strategy(rows):

    hot,_ = hot_strategy(rows)
    cold,_ = cold_strategy(rows)

    mix = list(dict.fromkeys(
        hot[:3] + cold[:3]
    ))

    while len(mix) < 6:

        n = random.randint(1,49)

        if n not in mix:
            mix.append(n)

    special = random.choice(mix)

    return sorted(mix[:6]), special


# =========================
# 波色预测
# =========================

def predict_colors(rows):

    specials = [
        r["special"]
        for r in rows[-10:]
    ]

    colors = [
        get_color(x)
        for x in specials
    ]

    c = Counter(colors)

    arr = c.most_common()

    if len(arr) == 1:
        return arr[0][0], arr[0][0]

    return arr[0][0], arr[1][0]


# =========================
# 二中一真实回测
# =========================

def backtest_color(rows):

    hit = 0
    total = 0

    for i in range(3, len(rows)):

        history = rows[:i]

        main, second = predict_colors(history)

        actual = get_color(
            rows[i]["special"]
        )

        if actual in (main, second):
            hit += 1

        total += 1

    return hit, total


# =========================
# 最大连空
# =========================

def max_miss(rows):

    miss = 0
    mx = 0

    for i in range(3, len(rows)):

        history = rows[:i]

        main, second = predict_colors(history)

        actual = get_color(
            rows[i]["special"]
        )

        if actual not in (main, second):

            miss += 1
            mx = max(mx, miss)

        else:

            miss = 0

    return mx


# =========================
# 最近10期真实回测
# =========================

def recent_hit(rows, func):

    recent = rows[-10:]

    total = 0
    rounds = 0

    details = []

    for i in range(3, len(recent)):

        history = recent[:i]

        pred, special = func(history)

        actual = recent[i]["numbers"]

        special_real = recent[i]["special"]

        hit = len(
            set(pred) &
            set(actual)
        )

        special_hit = (
            special == special_real
        )

        total += hit
        rounds += 1

        details.append({
            "issue": recent[i]["issue"],
            "hit": hit,
            "special_hit": special_hit,
            "predict": pred,
            "special": special,
            "actual": actual,
            "actual_special": special_real
        })

    avg = round(
        total / rounds,
        2
    ) if rounds else 0

    return avg, details


# =========================
# 展示
# =========================

def sync():

    conn = db_conn()

    online = fetch_latest()

    if not online:
        print("未抓到真实开奖数据")
        return

    new_count = save_records(
        conn,
        online
    )

    rows = load_rows(conn)

    latest = rows[-1]

    print(
        f"同步完成: {new_count} 条"
    )

    print()
    print("最新开奖:")

    nums = " ".join(
        f"{x:02d}"
        for x in latest["numbers"]
    )

    print(
        f"{latest['issue']} | "
        f"{nums} + "
        f"{latest['special']:02d}"
    )

    next_issue = str(
        int(latest["issue"]) + 1
    )

    print()
    print(
        f"预测期号: {next_issue}"
    )

    strategy_map = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
    }

    print()

    for name, func in strategy_map.items():

        pred, special = func(rows)

        odd_even, big_small, color = special_attr(
            special
        )

        print(
            f"{name:<12}: "
            f"{' '.join(f'{x:02d}' for x in pred)} "
            f"+ {special:02d}"
        )

        print(
            f"特码属性: "
            f"{odd_even}/{big_small} "
            f"{color}"
        )

    main_color, second_color = predict_colors(rows)

    print()
    print("特码波色预测（二中一）:")

    print(
        f"主强: {main_color}"
    )

    print(
        f"次强: {second_color}"
    )

    hit,total = backtest_color(
        rows[-10:]
    )

    print()
    print("最近10期真实回测:")

    print(
        f"二中一命中: {hit}/{total}"
    )

    print()
    print("真实最大连空:")

    print(
        f"{max_miss(rows[-10:])} 期"
    )

    print()
    print("推荐投注方案:")

    print(
        f"{main_color}: 300 元"
    )

    print(
        f"{second_color}: 150 元"
    )

    print()
    print("赔率参考:")

    print("红波: 2.7")
    print("蓝/绿波: 2.8")

    print()
    print("最近10期真实历史回测:")

    for name, func in strategy_map.items():

        avg, details = recent_hit(
            rows,
            func
        )

        print()
        print(f"{name}:")

        for d in details:

            print(
                f"{d['issue']} "
                f"命中{d['hit']}个 "
                f"| 特别号"
                f"{'中' if d['special_hit'] else '错'} "
                f"| 预测: "
                f"{' '.join(f'{x:02d}' for x in d['predict'])} "
                f"+ {d['special']:02d} "
                f"| 开奖: "
                f"{' '.join(f'{x:02d}' for x in d['actual'])} "
                f"+ {d['actual_special']:02d}"
            )

        print()
        print(
            f"平均命中: {avg} 个"
        )


# =========================
# main
# =========================

def main():

    if len(sys.argv) <= 1:
        sync()
        return

    cmd = sys.argv[1]

    if cmd == "sync":
        sync()
    else:
        sync()


if __name__ == "__main__":
    main()