# -- coding: utf-8 --

import requests
import sqlite3
import random
import statistics
import time
from collections import Counter

DB_FILE = "new_macau.db"

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

ELEMENTS = {
    "金": {4, 5, 12, 13, 26, 27, 34, 35, 42, 43},
    "木": {1, 2, 9, 10, 23, 24, 31, 32, 45, 46},
    "水": {7, 8, 15, 16, 29, 30, 37, 38},
    "火": {11, 17, 18, 25, 39, 40, 47, 48},
    "土": {3, 6, 14, 19, 20, 21, 22, 28, 33, 36, 41, 44, 49}
}


def get_color(num):
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"


def get_element(num):
    for k, v in ELEMENTS.items():
        if num in v:
            return k
    return "?"


def get_size(num):
    return "大" if num >= 25 else "小"


def get_odd_even(num):
    return "单" if num % 2 else "双"


def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
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

    conn.commit()
    return conn


def fetch_real_data():

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    urls = [
        "https://www.macaumarksix.com/api/macaujc.json",
        "https://www.macaumarksix.com/static/data/next_issues.json"
    ]

    for url in urls:

        try:

            r = requests.get(
                url,
                headers=headers,
                timeout=20
            )

            if r.status_code != 200:
                continue

            data = r.json()

            if isinstance(data, dict):
                data = data.get("data", [])

            if not data:
                continue

            draws = []

            for item in data:

                issue = str(
                    item.get("expect")
                    or item.get("issue")
                    or item.get("turnNum")
                    or ""
                )

                nums = (
                    item.get("openCode")
                    or item.get("result")
                    or ""
                )

                if isinstance(nums, str):
                    arr = nums.replace("+", ",").split(",")
                else:
                    arr = nums

                arr = [int(x) for x in arr if str(x).isdigit()]

                if len(arr) < 7:
                    continue

                draws.append({
                    "issue": issue,
                    "nums": arr[:6],
                    "special": arr[6]
                })

            if draws:
                return draws

        except:
            continue

    return []


def save_draws(conn, draws):

    for d in draws:

        row = (
            d["issue"],
            d["nums"][0],
            d["nums"][1],
            d["nums"][2],
            d["nums"][3],
            d["nums"][4],
            d["nums"][5],
            d["special"]
        )

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?,?,?,?,?)
        """, row)

    conn.commit()


def load_draws(conn):

    cur = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue
    """)

    rows = cur.fetchall()

    data = []

    for r in rows:

        data.append({
            "issue": r[0],
            "nums": list(r[1:7]),
            "special": r[7]
        })

    return data


def omission_stats(draws):

    miss = {}

    for n in range(1, 50):

        c = 0

        for d in reversed(draws):

            if n in d["nums"] or n == d["special"]:
                break

            c += 1

        miss[n] = c

    return miss


def hot_numbers(draws):

    nums = []

    for d in draws[-20:]:
        nums.extend(d["nums"])

    c = Counter(nums)

    return [x for x, _ in c.most_common(6)]


def cold_numbers(draws):

    miss = omission_stats(draws)

    arr = sorted(miss.items(), key=lambda x: -x[1])

    return [x[0] for x in arr[:6]]


def momentum_numbers(draws):

    nums = []

    for d in draws[-10:]:
        nums.extend(d["nums"])

    c = Counter(nums)

    return [x for x, _ in c.most_common(6)]


def balanced_numbers(draws):

    hot = hot_numbers(draws)[:3]
    cold = cold_numbers(draws)[:3]

    return hot + cold


def mining_numbers(draws):

    nums = []

    for d in draws[-30:]:
        nums.extend(d["nums"])

    c = Counter(nums)

    arr = [x for x, _ in c.most_common(12)]

    return random.sample(arr, 6)


def vote_numbers(draws):

    pool = []

    for s in [
        hot_numbers(draws),
        cold_numbers(draws),
        momentum_numbers(draws),
        balanced_numbers(draws)
    ]:
        pool.extend(s)

    c = Counter(pool)

    return [x for x, _ in c.most_common(6)]


def predict_special(draws):

    specials = [d["special"] for d in draws[-20:]]

    colors = [get_color(x) for x in specials]

    c = Counter(colors)

    main = c.most_common(2)

    return main[0][0], main[1][0]


def predict_size(draws):

    specials = [d["special"] for d in draws[-10:]]

    big = sum(1 for x in specials if x >= 25)

    return "大" if big >= 5 else "小"


def predict_odd_even(draws):

    specials = [d["special"] for d in draws[-10:]]

    odd = sum(1 for x in specials if x % 2)

    return "单" if odd >= 5 else "双"


def max_miss(draws):

    recent = draws[-10:]

    result = {}

    for color in ["红", "蓝", "绿"]:

        miss = 0
        best = 0

        for d in recent:

            c = get_color(d["special"])

            if c == color:
                best = max(best, miss)
                miss = 0
            else:
                miss += 1

        best = max(best, miss)

        result[color] = best

    return result


def strategy_special(color):

    arr = []

    for n in range(1, 50):

        if get_color(n) == color:
            arr.append(n)

    return random.choice(arr)


def print_strategy(name, nums, sp):

    print(f"{name:<10}: {' '.join(f'{x:02d}' for x in nums)} + {sp:02d}")

    print(
        f"特码属性: "
        f"{get_odd_even(sp)}/"
        f"{get_size(sp)} "
        f"{get_color(sp)} "
        f"{get_element(sp)}"
    )


def backtest(draws):

    recent = draws[-10:]

    stats = {}

    for name in [
        "组合策略",
        "热号策略",
        "冷号回补",
        "近期动量",
        "集成投票",
        "规律挖掘"
    ]:
        stats[name] = []

    for i in range(1, len(recent)):

        hist = recent[:i]

        real = recent[i]

        strategies = {
            "组合策略": balanced_numbers(hist),
            "热号策略": hot_numbers(hist),
            "冷号回补": cold_numbers(hist),
            "近期动量": momentum_numbers(hist),
            "集成投票": vote_numbers(hist),
            "规律挖掘": mining_numbers(hist)
        }

        for k, nums in strategies.items():

            hit = len(set(nums) & set(real["nums"]))

            stats[k].append(hit)

    print()
    print("最近10期历史命中统计:")

    for k, v in stats.items():

        avg = round(statistics.mean(v), 2) if v else 0

        print(f"{k:<10}: 期数={len(v)} 平均命中={avg}")


def betting_plan(main_color, second_color, size, odd_even):

    print()
    print("推荐投注方案:")

    print(f"{main_color}: 450 元")
    print(f"{second_color}: 150 元")

    print(f"{size}: 200 元")
    print(f"{odd_even}: 200 元")

    print()
    print("赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")


def sync():

    conn = init_db()

    draws = fetch_real_data()

    if not draws:
        print("未抓到真实开奖数据")
        return

    save_draws(conn, draws)

    all_draws = load_draws(conn)

    print(f"同步完成: {len(all_draws)} 条")

    latest = all_draws[-1]

    next_issue = str(int(latest["issue"]) + 1)

    print()
    print("最新开奖:")
    print(
        latest["issue"],
        "|",
        " ".join(f"{x:02d}" for x in latest["nums"]),
        "+",
        f"{latest['special']:02d}"
    )

    print()
    print(f"预测期号: {next_issue}")

    main_color, second_color = predict_special(all_draws)

    strategies = {
        "组合策略": balanced_numbers(all_draws),
        "热号策略": hot_numbers(all_draws),
        "冷号回补": cold_numbers(all_draws),
        "近期动量": momentum_numbers(all_draws),
        "集成投票": vote_numbers(all_draws),
        "规律挖掘": mining_numbers(all_draws)
    }

    for k, nums in strategies.items():

        sp = strategy_special(main_color)

        print_strategy(k, nums, sp)

    print()
    print("特码波色预测:")
    print(f"主强: {main_color} 次强: {second_color}")

    size = predict_size(all_draws)
    odd_even = predict_odd_even(all_draws)

    print()
    print("大小单双预测:")
    print(f"大小: {size}")
    print(f"单双: {odd_even}")

    miss = max_miss(all_draws)

    print()
    print("最大连空:")

    for k, v in miss.items():
        print(f"{k}波: {v}期")

    betting_plan(
        main_color,
        second_color,
        size,
        odd_even
    )

    backtest(all_draws)


if name == "main":
    sync()