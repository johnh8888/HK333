# -*- coding: utf-8 -*-

import sqlite3
import requests
import argparse
import random
from collections import Counter
from statistics import mean

DB_FILE = "new_macau.db"

# =========================
# 波色
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
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}

# =========================
# 五行
# =========================

ELEMENT_MAP = {
    "金": {1, 2, 15, 16, 23, 24, 37, 38},
    "木": {5, 6, 13, 14, 27, 28, 35, 36, 49},
    "水": {3, 4, 11, 12, 19, 20, 33, 34, 41, 42},
    "火": {7, 8, 21, 22, 29, 30, 43, 44},
    "土": {9, 10, 17, 18, 25, 26, 31, 32, 39, 40, 45, 46, 47, 48},
}

# =========================
# 初始化数据库
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
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
    CREATE TABLE IF NOT EXISTS predicts (
        issue TEXT,
        strategy TEXT,
        numbers TEXT,
        special INTEGER
    )
    """)

    conn.commit()
    return conn

# =========================
# 获取真实数据
# =========================

def fetch_real_data():

    urls = [
        "https://www.macaumarksix.com/api/macaujc.com",
        "https://www.macaumarksix.com/api/history",
        "https://www.macaumarksix.com"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=15)

            if r.status_code != 200:
                continue

            data = r.json()

            rows = []

            for item in data:

                issue = str(item.get("expect", ""))

                nums = item.get("openCode", "")
                nums = nums.replace("+", ",")

                arr = []

                for x in nums.split(","):
                    x = x.strip()

                    if x.isdigit():
                        arr.append(int(x))

                if len(arr) != 7:
                    continue

                rows.append({
                    "issue": issue,
                    "nums": arr[:6],
                    "special": arr[6]
                })

            if rows:
                return rows

        except:
            pass

    return []

# =========================
# 保存数据
# =========================

def save_draws(conn, rows):

    for row in rows:

        nums = row["nums"]

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["issue"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            row["special"]
        ))

    conn.commit()

# =========================
# 获取历史
# =========================

def get_draws(conn, limit=80):

    cur = conn.cursor()

    cur.execute(f"""
    SELECT * FROM draws
    ORDER BY issue DESC
    LIMIT {limit}
    """)

    return cur.fetchall()

# =========================
# 属性
# =========================

def get_color(num):

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"

def get_element(num):

    for k, v in ELEMENT_MAP.items():
        if num in v:
            return k

    return "?"

def get_size(num):
    return "大" if num >= 25 else "小"

def get_odd_even(num):
    return "双" if num % 2 == 0 else "单"

def get_tail(num):
    return "尾大" if num % 10 >= 5 else "尾小"

# =========================
# 策略
# =========================

def hot_strategy(draws):

    nums = []

    for d in draws[:20]:
        nums.extend(d[1:7])

    c = Counter(nums)

    picks = [x[0] for x in c.most_common(6)]

    sp = c.most_common(1)[0][0]

    return picks, sp

def cold_strategy(draws):

    nums = []

    for d in draws[:30]:
        nums.extend(d[1:7])

    c = Counter(nums)

    arr = []

    for i in range(1, 50):
        arr.append((i, c[i]))

    arr.sort(key=lambda x: x[1])

    picks = [x[0] for x in arr[:6]]

    sp = arr[0][0]

    return picks, sp

def momentum_strategy(draws):

    recent = draws[:10]

    nums = []

    for d in recent:
        nums.extend(d[1:7])

    c = Counter(nums)

    picks = [x[0] for x in c.most_common(6)]

    sp = recent[0][-1]

    return picks, sp

def balanced_strategy(draws):

    hot, _ = hot_strategy(draws)
    cold, _ = cold_strategy(draws)

    arr = hot[:3] + cold[:3]

    random.shuffle(arr)

    return arr, random.randint(1, 49)

def pattern_strategy(draws):

    nums = []

    for d in draws[:15]:
        nums.extend(d[1:7])

    c = Counter(nums)

    arr = []

    for n, _ in c.most_common(12):

        if n not in arr:
            arr.append(n)

        if len(arr) == 6:
            break

    return arr, random.choice(arr)

def ensemble_strategy(draws):

    all_nums = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy,
        pattern_strategy
    ]:
        arr, _ = fn(draws)
        all_nums.extend(arr)

    c = Counter(all_nums)

    picks = [x[0] for x in c.most_common(6)]

    return picks, random.choice(picks)

# =========================
# 波色预测
# =========================

def predict_color(draws):

    colors = []

    for d in draws[:10]:
        colors.append(get_color(d[-1]))

    c = Counter(colors)

    arr = c.most_common()

    main = arr[0][0]

    second = arr[1][0] if len(arr) > 1 else main

    return main, second

# =========================
# 大小单双
# =========================

def predict_size_odd(draws):

    specials = [d[-1] for d in draws[:10]]

    sizes = [get_size(x) for x in specials]
    odds = [get_odd_even(x) for x in specials]

    size = Counter(sizes).most_common(1)[0][0]
    odd = Counter(odds).most_common(1)[0][0]

    return size, odd

# =========================
# 最大真实连空
# =========================

def max_miss(draws):

    recent = draws[:10]

    result = {}

    for color in ["红", "蓝", "绿"]:

        max_gap = 0
        cur = 0

        for d in recent:

            c = get_color(d[-1])

            if c == color:
                cur = 0
            else:
                cur += 1

            max_gap = max(max_gap, cur)

        result[color] = max_gap

    return result

# =========================
# 回测
# =========================

def backtest(draws):

    result = {}

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": ensemble_strategy,
        "规律挖掘": pattern_strategy
    }

    for name, fn in strategies.items():

        hits = []

        for i in range(10, 1, -1):

            hist = draws[i:]

            if len(hist) < 20:
                continue

            pred, _ = fn(hist)

            actual = draws[i - 1][1:7]

            hit = len(set(pred) & set(actual))

            hits.append(hit)

        if hits:

            result[name] = {
                "count": len(hits),
                "avg": round(mean(hits), 2)
            }

    return result

# =========================
# 投注
# =========================

def recommend_bet(main_color, second_color, size, odd):

    return {
        main_color: 450,
        second_color: 150,
        size: 200,
        odd: 200
    }

# =========================
# 打印
# =========================

def print_strategy(name, picks, sp):

    nums = " ".join([f"{x:02d}" for x in picks])

    print(f"{name:<10}: {nums} + {sp:02d}")

    print(
        f"特码属性: "
        f"{get_odd_even(sp)}/"
        f"{get_size(sp)} "
        f"{get_color(sp)} "
        f"{get_element(sp)}"
    )

# =========================
# 主同步
# =========================

def sync():

    conn = init_db()

    rows = fetch_real_data()

    if not rows:
        print("未抓到真实开奖数据")
        return

    save_draws(conn, rows)

    draws = get_draws(conn)

    print(f"同步完成: {len(draws)} 条")
    print()

    latest = draws[0]

    print("最新开奖:")
    print(
        f"{latest[0]} | "
        f"{latest[1]:02d} "
        f"{latest[2]:02d} "
        f"{latest[3]:02d} "
        f"{latest[4]:02d} "
        f"{latest[5]:02d} "
        f"{latest[6]:02d} "
        f"+ {latest[7]:02d}"
    )

    print()

    next_issue = str(int(latest[0]) + 1)

    print(f"预测期号: {next_issue}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": ensemble_strategy,
        "规律挖掘": pattern_strategy
    }

    for name, fn in strategies.items():

        picks, sp = fn(draws)

        print_strategy(name, picks, sp)

    print()

    main_color, second_color = predict_color(draws)

    print("特码波色预测:")
    print(f"主强: {main_color} 次强: {second_color}")

    print()

    size, odd = predict_size_odd(draws)

    print("大小单双预测:")
    print(f"大小: {size}")
    print(f"单双: {odd}")

    print()

    print("最大连空:")

    miss = max_miss(draws)

    for k, v in miss.items():
        print(f"{k}波: {v}期")

    print()

    print("推荐投注方案:")

    plan = recommend_bet(
        main_color,
        second_color,
        size,
        odd
    )

    for k, v in plan.items():
        print(f"{k}: {v} 元")

    print()

    print("赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")

    print()

    print("最近10期历史命中统计:")

    bt = backtest(draws)

    for k, v in bt.items():
        print(
            f"{k:<10}: "
            f"期数={v['count']} "
            f"平均命中={v['avg']}"
        )

# =========================
# 显示
# =========================

def show_latest():

    conn = init_db()

    draws = get_draws(conn, 10)

    for d in draws:

        print(
            f"{d[0]} | "
            f"{d[1]:02d} "
            f"{d[2]:02d} "
            f"{d[3]:02d} "
            f"{d[4]:02d} "
            f"{d[5]:02d} "
            f"{d[6]:02d} "
            f"+ {d[7]:02d}"
        )

# =========================
# main
# =========================

def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync")
    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()

    elif args.cmd == "show":
        show_latest()

    else:
        parser.print_help()

if __name__ == "__main__":
    main()