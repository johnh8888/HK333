# -*- coding: utf-8 -*-

import os
import re
import ssl
import json
import math
import sqlite3
import random
import urllib.request
from collections import Counter

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
    "金": {5, 6, 13, 14, 21, 22, 35, 36, 43, 44},
    "木": {3, 4, 17, 18, 25, 26, 39, 40, 47, 48},
    "水": {1, 2, 15, 16, 23, 24, 37, 38, 45, 46},
    "火": {7, 8, 19, 20, 27, 28, 41, 42, 49},
    "土": {9, 10, 11, 12, 29, 30, 31, 32, 33, 34}
}

# =========================================================
# 数据库
# =========================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
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
    conn.close()

# =========================================================
# 属性
# =========================================================

def get_wave(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

def get_element(n):

    for k, v in ELEMENTS.items():
        if n in v:
            return k

    return "?"

def get_big_small(n):

    return "大" if n >= 25 else "小"

def get_odd_even(n):

    return "单" if n % 2 else "双"

def get_tail_big_small(n):

    return "尾大" if n % 10 >= 5 else "尾小"

def get_sum_odd_even(n):

    s = sum(map(int, str(n)))
    return "合单" if s % 2 else "合双"

def special_info(n):

    return (
        f"{get_odd_even(n)}/"
        f"{get_big_small(n)} "
        f"{get_sum_odd_even(n)}/"
        f"{get_big_small(sum(map(int,str(n))))} "
        f"{get_tail_big_small(n)} "
        f"{get_wave(n)} "
        f"{get_element(n)}"
    )

# =========================================================
# 获取数据
# =========================================================

def fetch_data():

    url = "https://marksix6.net/index.php?api=1"

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Cache-Control": "no-cache"
            }
        )

        ctx = ssl._create_unverified_context()

        response = urllib.request.urlopen(
            req,
            timeout=20,
            context=ctx
        )

        raw = response.read().decode("utf-8")

        data = json.loads(raw)

        lotteries = data.get("lottery_data", [])

        rows = []

        for lottery in lotteries:

            name = str(
                lottery.get("name", "")
            )

            # 只取新澳门
            if "新澳门" not in name:
                continue

            # 当前期
            issue = str(
                lottery.get("expect", "")
            ).strip()

            open_code = str(
                lottery.get("openCode", "")
            )

            nums = [
                int(x)
                for x in re.findall(
                    r"\d+",
                    open_code
                )
            ]

            if len(nums) >= 7:

                rows.append({
                    "issue": issue,
                    "nums": nums[:7]
                })

            # 历史
            histories = lottery.get(
                "history",
                []
            )

            for h in histories:

                try:

                    if isinstance(h, dict):

                        h_issue = str(
                            h.get("expect", "")
                        ).strip()

                        h_open = str(
                            h.get("openCode", "")
                        )

                    else:

                        parts = str(h).split("|")

                        if len(parts) != 2:
                            continue

                        h_issue = parts[0].strip()

                        h_open = parts[1]

                    h_nums = [
                        int(x)
                        for x in re.findall(
                            r"\d+",
                            h_open
                        )
                    ]

                    if len(h_nums) < 7:
                        continue

                    rows.append({
                        "issue": h_issue,
                        "nums": h_nums[:7]
                    })

                except:
                    pass

        # 去重
        unique = {}

        for r in rows:
            unique[r["issue"]] = r

        rows = list(unique.values())

        rows.sort(
            key=lambda x: int(x["issue"]),
            reverse=True
        )

        print(f"API获取成功: {url}")
        print(f"抓取到历史数据: {len(rows)} 条")

        if rows:
            print(f"最新历史期号: {rows[0]['issue']}")

        return rows[:600]

    except Exception as e:

        print(f"API获取失败: {e}")

        return []

# =========================================================
# 保存
# =========================================================

def save_records(rows):

    conn = sqlite3.connect(DB_FILE)

    count = 0

    for r in rows:

        nums = r["nums"]

        conn.execute("""
        INSERT OR REPLACE INTO lottery
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["issue"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            nums[6]
        ))

        count += 1

    conn.commit()
    conn.close()

    return count

# =========================================================
# 读取数据
# =========================================================

def load_data():

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute("""
    SELECT *
    FROM lottery
    ORDER BY issue DESC
    """).fetchall()

    conn.close()

    return rows

# =========================================================
# 选号
# =========================================================

def pick_hot(data):

    nums = []

    for r in data[:30]:
        nums.extend(r[1:7])

    c = Counter(nums)

    main = [
        x[0]
        for x in c.most_common(6)
    ]

    sp = c.most_common(1)[0][0]

    return main, sp

def pick_cold(data):

    nums = []

    for r in data[:50]:
        nums.extend(r[1:7])

    c = Counter(nums)

    alln = set(range(1, 50))

    for n in alln:
        if n not in c:
            c[n] = 0

    cold = sorted(
        c.items(),
        key=lambda x: x[1]
    )[:6]

    main = [x[0] for x in cold]

    return main, main[0]

def pick_momentum(data):

    last = data[0][1:7]

    extra = random.sample(
        range(1, 50),
        2
    )

    nums = list(set(last + tuple(extra)))

    while len(nums) < 6:
        n = random.randint(1,49)
        if n not in nums:
            nums.append(n)

    return nums[:6], nums[0]

def pick_pattern(data):

    last = data[0][1:7]

    nums = sorted(last)[:5]

    while len(nums) < 6:
        n = random.randint(1,49)
        if n not in nums:
            nums.append(n)

    return nums[:6], nums[0]

def pick_vote(data):

    hot,_ = pick_hot(data)
    cold,_ = pick_cold(data)

    pool = hot[:3] + cold[:3]

    while len(pool) < 6:
        n = random.randint(1,49)
        if n not in pool:
            pool.append(n)

    return pool[:6], pool[0]

def pick_combo(data):

    hot,_ = pick_hot(data)
    cold,_ = pick_cold(data)

    nums = list(set(
        hot[:3] + cold[:3]
    ))

    while len(nums) < 6:
        n = random.randint(1,49)
        if n not in nums:
            nums.append(n)

    return nums[:6], nums[0]

# =========================================================
# 波色预测
# =========================================================

def predict_wave(data):

    score = {
        "红":0,
        "蓝":0,
        "绿":0
    }

    recent = data[:10]

    weight = 10

    for r in recent:

        sp = r[7]

        wave = get_wave(sp)

        score[wave] += weight

        weight -= 1

    ranked = sorted(
        score.items(),
        key=lambda x:x[1],
        reverse=True
    )

    return ranked

# =========================================================
# 大小单双
# =========================================================

def predict_bs(data):

    recent = data[:10]

    big = 0
    small = 0
    odd = 0
    even = 0

    for r in recent:

        sp = r[7]

        if sp >= 25:
            big += 1
        else:
            small += 1

        if sp % 2:
            odd += 1
        else:
            even += 1

    bs = "大" if big >= small else "小"
    oe = "单" if odd >= even else "双"

    return bs, oe

# =========================================================
# 回测
# =========================================================

def backtest_wave(data):

    hits = 0
    total = 0

    miss = 0
    max_miss = 0

    for i in range(10, 0, -1):

        window = data[i:i+10]

        if len(window) < 10:
            continue

        score = {
            "红":0,
            "蓝":0,
            "绿":0
        }

        weight = 10

        for r in window:

            sp = r[7]

            wave = get_wave(sp)

            score[wave] += weight

            weight -= 1

        ranked = sorted(
            score.items(),
            key=lambda x:x[1],
            reverse=True
        )

        pred = [
            ranked[0][0],
            ranked[1][0]
        ]

        real = get_wave(
            data[i-1][7]
        )

        total += 1

        if real in pred:

            hits += 1
            miss = 0

        else:

            miss += 1

            if miss > max_miss:
                max_miss = miss

    rate = round(
        hits / total * 100,
        1
    ) if total else 0

    return hits, total, rate, max_miss

# =========================================================
# 输出
# =========================================================

def show_strategy(name, nums, sp):

    main = " ".join(
        f"{x:02d}"
        for x in nums
    )

    print(
        f"  {name:<12}: "
        f"{main} + {sp:02d}"
    )

    print(
        f"         特码属性: "
        f"{special_info(sp)}"
    )

# =========================================================
# 主程序
# =========================================================

def sync():

    init_db()

    rows = fetch_data()

    if not rows:

        print("未抓到真实开奖数据")
        return

    count = save_records(rows)

    print(
        f"数据同步完成: "
        f"total={count}, new={count}"
    )

    data = load_data()

    if len(data) < 20:

        print("数据不足")
        return

    latest = data[0]

    latest_issue = latest[0]

    latest_nums = latest[1:7]

    latest_sp = latest[7]

    print(
        f"最新开奖: "
        f"{latest_issue} | "
        f"{' '.join(f'{x:02d}' for x in latest_nums)} "
        f"+ {latest_sp:02d}"
    )

    next_issue = str(
        int(latest_issue) + 1
    )

    print()
    print(f"预测期号: {next_issue}")

    combo = pick_combo(data)
    cold = pick_cold(data)
    vote = pick_vote(data)
    hot = pick_hot(data)
    momentum = pick_momentum(data)
    pattern = pick_pattern(data)

    show_strategy("组合策略", *combo)
    show_strategy("冷号回补", *cold)
    show_strategy("集成投票", *vote)
    show_strategy("热号策略", *hot)
    show_strategy("近期动量", *momentum)
    show_strategy("规律挖掘", *pattern)

    print()
    print("🎨 特码波色预测（加权频率，基于最近 10 期）：")

    ranked = predict_wave(data)

    print(
        f"   主强: {ranked[0][0]} "
        f"(得分 {ranked[0][1]})   "
        f"次强: {ranked[1][0]} "
        f"(得分 {ranked[1][1]})"
    )

    print()
    print("📊 大小单双预测（最近10期真实数据）：")

    bs, oe = predict_bs(data)

    print(
        f"   大小预测: {bs}   "
        f"单双预测: {oe}"
    )

    print()
    print("📊 历史回测（最近 10 期）：")

    hits, total, rate, max_miss = backtest_wave(data)

    print(
        f"   二中一命中率: {rate}%"
    )

    print(
        f"   最近10期命中: "
        f"{hits}/{total}"
    )

    print(
        f"   最大连空: {max_miss}期"
    )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    sync()