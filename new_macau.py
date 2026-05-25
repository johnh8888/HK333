# -*- coding: utf-8 -*-

"""
新澳门六合彩 AI 自动预测系统（真实数据版）

功能：
1. 抓取真实澳门六合彩数据
2. SQLite 自动存储
3. 六大预测策略
4. 波色预测
5. 大小单双预测
6. 最近10期真实回测
7. 最大连空（真实未命中）
8. 投注比例建议
9. GitHub Actions 自动运行

运行:
python new_macau.py sync
"""

import sqlite3
import requests
import re
from collections import Counter
from datetime import datetime

DB_FILE = "new_macau.db"

# =========================================================
# 波色
# =========================================================

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

# =========================================================
# 五行
# =========================================================

ELEMENTS = {
    "金": [1, 2, 15, 16, 29, 30, 43, 44],
    "木": [5, 6, 19, 20, 33, 34, 47, 48],
    "水": [9, 10, 23, 24, 37, 38],
    "火": [13, 14, 27, 28, 41, 42],
    "土": [3, 4, 17, 18, 31, 32, 45, 46]
}

# =========================================================
# 工具
# =========================================================

def num_txt(n):
    return f"{n:02d}"

def get_color(num):

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"

def get_element(num):

    for k, arr in ELEMENTS.items():

        if num in arr:
            return k

    return "土"

def big_small(num):

    return "大" if num >= 25 else "小"

def odd_even(num):

    return "单" if num % 2 else "双"

# =========================================================
# 数据库
# =========================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        period TEXT PRIMARY KEY,
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
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT,
        strategy TEXT,
        numbers TEXT,
        special INTEGER,
        created_at TEXT
    )
    """)

    conn.commit()

    return conn

# =========================================================
# 真实数据抓取
# =========================================================

def fetch_latest_draws():

    url = "https://www.macauslot.com/lottery/marksix/history"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:

        r = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        html = r.text

    except Exception as e:

        print("抓取失败:", e)
        return []

    periods = re.findall(
        r'第(\d+)期',
        html
    )

    balls = re.findall(
        r'ball.*?>(\d+)<',
        html
    )

    draws = []

    if not periods or not balls:

        print("未抓到真实开奖数据")
        return []

    idx = 0

    for p in periods[:80]:

        try:

            nums = []

            for _ in range(7):

                nums.append(int(balls[idx]))
                idx += 1

            draws.append({
                "period": p,
                "numbers": nums[:6],
                "special": nums[6]
            })

        except:
            break

    return draws

# =========================================================
# 保存数据
# =========================================================

def save_draws(conn, draws):

    for d in draws:

        nums = d["numbers"]

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d["period"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            d["special"]
        ))

    conn.commit()

# =========================================================
# 读取数据
# =========================================================

def load_draws(conn):

    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM draws
    ORDER BY period ASC
    """)

    rows = cur.fetchall()

    arr = []

    for r in rows:

        arr.append({
            "period": r[0],
            "numbers": list(r[1:7]),
            "special": r[7]
        })

    return arr

# =========================================================
# 策略
# =========================================================

def hot_strategy(draws):

    nums = []

    for d in draws[-20:]:

        nums.extend(d["numbers"])
        nums.append(d["special"])

    c = Counter(nums)

    top = [x[0] for x in c.most_common(7)]

    return top[:6], top[6]

def cold_strategy(draws):

    nums = []

    for d in draws[-30:]:

        nums.extend(d["numbers"])
        nums.append(d["special"])

    c = Counter(nums)

    arr = sorted(c.items(), key=lambda x: x[1])

    top = [x[0] for x in arr[:7]]

    return top[:6], top[6]

def momentum_strategy(draws):

    nums = []

    for d in draws[-10:]:

        nums.extend(d["numbers"])

    c = Counter(nums)

    top = [x[0] for x in c.most_common(7)]

    return top[:6], top[6]

def balanced_strategy(draws):

    hot, _ = hot_strategy(draws)

    cold, _ = cold_strategy(draws)

    arr = hot[:3] + cold[:4]

    return arr[:6], arr[6]

def mining_strategy(draws):

    nums = []

    for d in draws[-15:]:

        nums.extend(d["numbers"])

    c = Counter(nums)

    top = [x[0] for x in c.most_common(7)]

    return top[:6], top[6]

def ensemble_strategy(draws):

    s1, t1 = hot_strategy(draws)
    s2, t2 = momentum_strategy(draws)
    s3, t3 = balanced_strategy(draws)

    pool = s1 + s2 + s3 + [t1, t2, t3]

    c = Counter(pool)

    top = [x[0] for x in c.most_common(7)]

    return top[:6], top[6]

# =========================================================
# 保存预测
# =========================================================

def save_prediction(conn, period, strategy, nums, sp):

    conn.execute("""
    INSERT INTO predictions
    (period, strategy, numbers, special, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (
        period,
        strategy,
        ",".join(map(str, nums)),
        sp,
        datetime.now().isoformat()
    ))

    conn.commit()

# =========================================================
# 输出策略
# =========================================================

def print_strategy(name, nums, sp):

    print(
        f"{name:<10}: "
        + " ".join(num_txt(x) for x in nums)
        + f" + {num_txt(sp)}"
    )

    print(
        f"特码属性: "
        f"{odd_even(sp)}/"
        f"{big_small(sp)} "
        f"{get_color(sp)} "
        f"{get_element(sp)}"
    )

# =========================================================
# 波色预测
# =========================================================

def predict_color(draws):

    colors = []

    for d in draws[-20:]:

        colors.append(get_color(d["special"]))

    c = Counter(colors)

    top = c.most_common()

    return top[0][0], top[1][0]

# =========================================================
# 大小单双预测
# =========================================================

def predict_bs_oe(draws):

    specials = [x["special"] for x in draws[-20:]]

    big = sum(1 for x in specials if x >= 25)
    small = len(specials) - big

    odd = sum(1 for x in specials if x % 2 == 1)
    even = len(specials) - odd

    bs = "大" if big >= small else "小"
    oe = "单" if odd >= even else "双"

    return bs, oe

# =========================================================
# 最大连空（真实未命中）
# =========================================================

def calc_max_miss(draws):

    result = {}

    for color in ["红", "蓝", "绿"]:

        miss = 0
        max_miss = 0

        for d in draws[-10:]:

            c = get_color(d["special"])

            if c == color:
                miss = 0
            else:
                miss += 1

            max_miss = max(max_miss, miss)

        result[color] = max_miss

    return result

# =========================================================
# 回测
# =========================================================

def backtest(draws):

    print()
    print("最近10期历史命中统计:")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": ensemble_strategy,
        "规律挖掘": mining_strategy
    }

    for name, func in strategies.items():

        hits = []

        for i in range(10, 1, -1):

            sub = draws[:-i]

            if len(sub) < 20:
                continue

            nums, sp = func(sub)

            actual = draws[-i]["numbers"]
            actual_sp = draws[-i]["special"]

            hit = len(set(nums) & set(actual))

            if sp == actual_sp:
                hit += 1

            hits.append(hit)

        if hits:

            avg = round(sum(hits) / len(hits), 2)

            print(
                f"{name:<10}: "
                f"期数={len(hits)} "
                f"平均命中={avg}"
            )

# =========================================================
# 投注建议
# =========================================================

def recommend_bet(main_color, second_color, bs, oe):

    print()
    print("推荐投注方案:")

    print(f"{main_color}: 450 元")
    print(f"{second_color}: 150 元")
    print(f"{bs}: 200 元")
    print(f"{oe}: 200 元")

    print()
    print("赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")

# =========================================================
# 主程序
# =========================================================

def sync():

    conn = init_db()

    draws = fetch_latest_draws()

    if not draws:

        print("无真实数据")
        return

    save_draws(conn, draws)

    draws = load_draws(conn)

    latest = draws[-1]

    next_period = str(int(latest["period"]) + 1)

    print(f"已生成 {next_period} 期预测")

    print(f"同步完成: {len(draws)} 条")

    print()
    print("累计收益: 0.00")

    print()
    print("最新开奖:")

    print(
        f"{latest['period']} | "
        + " ".join(num_txt(x) for x in latest["numbers"])
        + f" + {num_txt(latest['special'])}"
    )

    print()
    print(f"预测期号: {next_period}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": ensemble_strategy,
        "规律挖掘": mining_strategy
    }

    for name, func in strategies.items():

        nums, sp = func(draws)

        print_strategy(name, nums, sp)

        save_prediction(
            conn,
            next_period,
            name,
            nums,
            sp
        )

    print()

    main_color, second_color = predict_color(draws)

    print("特码波色预测:")
    print(f"主强: {main_color} 次强: {second_color}")

    print()

    bs, oe = predict_bs_oe(draws)

    print("大小单双预测:")
    print(f"大小: {bs}")
    print(f"单双: {oe}")

    print()

    print("最大连空:")

    miss = calc_max_miss(draws)

    print(f"红波: {miss['红']}期")
    print(f"蓝波: {miss['蓝']}期")
    print(f"绿波: {miss['绿']}期")

    recommend_bet(
        main_color,
        second_color,
        bs,
        oe
    )

    backtest(draws)

# =========================================================
# main
# =========================================================

if __name__ == "__main__":

    sync()