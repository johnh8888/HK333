#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from urllib.request import Request, urlopen

DB_FILE = "new_macau.db"

# =========================
# 真实新澳门彩波色
# =========================
RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE_WAVE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN_WAVE = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

# =========================
# 获取波色
# =========================
def get_color(num):

    if num in RED_WAVE:
        return "红"

    if num in BLUE_WAVE:
        return "蓝"

    return "绿"

# =========================
# 特码属性
# =========================
def special_attributes(num):

    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    total = sum(map(int, str(num)))

    total_oe = "单" if total % 2 else "双"

    total_bs = "大" if total >= 7 else "小"

    tail = num % 10

    tail_bs = "大" if tail >= 5 else "小"

    color = get_color(num)

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
        f"{color} {element}"
    )

# =========================
# 建库
# =========================
def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        issue TEXT PRIMARY KEY,
        numbers TEXT,
        special INTEGER,
        created TEXT
    )
    """)

    conn.commit()

    return conn

# =========================
# 获取真实新澳门彩数据
# =========================
def fetch_real_data():

    urls = [
        "https://marksix6.net/index.php?api=1",
        "https://api.macaumarksix.com/history",
    ]

    for url in urls:

        try:

            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            res = urlopen(req, timeout=15)

            text = res.read().decode("utf-8")

            data = json.loads(text)

            # marksix6
            if "lottery_data" in data:

                for item in data["lottery_data"]:

                    if item.get("name") == "新澳门彩":

                        result = []

                        for row in item["history"][:120]:

                            try:

                                issue, nums = row.split("期：")

                                arr = [int(x) for x in nums.split(",")]

                                if len(arr) != 7:
                                    continue

                                result.append({
                                    "issue": issue.strip(),
                                    "numbers": arr[:6],
                                    "special": arr[6]
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

    new_count = 0

    for r in records:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue=?",
            (r["issue"],)
        ).fetchone()

        if exists:
            continue

        conn.execute(
            """
            INSERT INTO draws(issue,numbers,special,created)
            VALUES(?,?,?,?)
            """,
            (
                r["issue"],
                json.dumps(r["numbers"]),
                r["special"],
                datetime.now().isoformat()
            )
        )

        new_count += 1

    conn.commit()

    return new_count

# =========================
# 获取最近开奖
# =========================
def get_latest_draw(conn):

    row = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT 1
    """).fetchone()

    return row

# =========================
# 最近120期特别号
# =========================
def get_specials(conn):

    rows = conn.execute("""
    SELECT special
    FROM draws
    ORDER BY issue ASC
    """).fetchall()

    return [r[0] for r in rows]

# =========================
# 热号策略
# =========================
def hot_strategy(draws):

    counter = Counter()

    for d in draws[-20:]:

        for n in d:
            counter[n] += 1

    return [x[0] for x in counter.most_common(6)]

# =========================
# 冷号策略
# =========================
def cold_strategy(draws):

    counter = Counter()

    for d in draws[-30:]:

        for n in d:
            counter[n] += 1

    ranked = sorted(counter.items(), key=lambda x: x[1])

    return [x[0] for x in ranked[:6]]

# =========================
# 动量策略
# =========================
def momentum_strategy(draws):

    score = {}

    recent = draws[-10:]

    for idx, d in enumerate(recent):

        weight = idx + 1

        for n in d:
            score[n] = score.get(n, 0) + weight

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return [x[0] for x in ranked[:6]]

# =========================
# 平衡策略
# =========================
def balanced_strategy(draws):

    hot = hot_strategy(draws)[:3]

    cold = cold_strategy(draws)[:3]

    result = hot + cold

    return result[:6]

# =========================
# 集成策略
# =========================
def ensemble_strategy(draws):

    score = Counter()

    strategies = [
        hot_strategy(draws),
        cold_strategy(draws),
        momentum_strategy(draws),
        balanced_strategy(draws),
    ]

    for s in strategies:

        for idx, n in enumerate(s):
            score[n] += (6 - idx)

    ranked = score.most_common(6)

    return [x[0] for x in ranked]

# =========================
# 规律挖掘
# =========================
def pattern_strategy(draws):

    counter = Counter()

    for d in draws[-50:]:

        for n in d:

            zone = (n - 1) // 10

            counter[(zone, n)] += 1

    ranked = sorted(
        counter.items(),
        key=lambda x: x[1],
        reverse=True
    )

    result = []

    for (_, n), _ in ranked:

        if n not in result:
            result.append(n)

        if len(result) == 6:
            break

    return result

# =========================
# 波色预测（二中一）
# =========================
def predict_colors(specials, window=10):

    recent = specials[-window:]

    counter = Counter(
        get_color(x)
        for x in recent
    )

    ranked = sorted(
        counter.items(),
        key=lambda x: (-x[1], x[0])
    )

    main = ranked[0][0]

    second = ranked[1][0]

    return main, second

# =========================
# 最近10期真实回测
# 不偷看未来
# =========================
def backtest_color_prediction(specials, window=10):

    if len(specials) < 20:
        return 0, 0, 0

    hit = 0

    total = 0

    start = len(specials) - 10

    for i in range(start, len(specials)):

        train = specials[:i]

        recent = train[-window:]

        counter = Counter(
            get_color(x)
            for x in recent
        )

        ranked = sorted(
            counter.items(),
            key=lambda x: (-x[1], x[0])
        )

        predicts = [ranked[0][0]]

        if len(ranked) > 1:
            predicts.append(ranked[1][0])

        actual = get_color(specials[i])

        if actual in predicts:
            hit += 1

        total += 1

    rate = round(hit / total * 100, 1)

    return hit, total, rate

# =========================
# 最大连空
# =========================
def real_max_miss(specials, window=10):

    recent = specials[-10:]

    miss = 0

    max_miss = 0

    for i in range(len(recent)):

        train = recent[:i]

        if len(train) < 3:
            continue

        counter = Counter(
            get_color(x)
            for x in train
        )

        ranked = sorted(
            counter.items(),
            key=lambda x: (-x[1], x[0])
        )

        predicts = [ranked[0][0]]

        if len(ranked) > 1:
            predicts.append(ranked[1][0])

        actual = get_color(recent[i])

        if actual in predicts:
            miss = 0
        else:
            miss += 1

        max_miss = max(max_miss, miss)

    return max_miss

# =========================
# 显示
# =========================
def show_dashboard(conn):

    latest = get_latest_draw(conn)

    if not latest:
        print("暂无数据")
        return

    issue = latest[0]

    nums = json.loads(latest[1])

    special = latest[2]

    print()
    print("最新开奖:")
    print(
        f"{issue} | "
        + " ".join(f"{x:02d}" for x in nums)
        + f" + {special:02d}"
    )

    next_issue = str(int(issue) + 1)

    print()
    print(f"预测期号: {next_issue}")

    rows = conn.execute("""
    SELECT numbers
    FROM draws
    ORDER BY issue ASC
    """).fetchall()

    draws = [
        json.loads(r[0])
        for r in rows
    ]

    strategies = {
        "组合策略": balanced_strategy(draws),
        "热号策略": hot_strategy(draws),
        "冷号回补": cold_strategy(draws),
        "近期动量": momentum_strategy(draws),
        "集成投票": ensemble_strategy(draws),
        "规律挖掘": pattern_strategy(draws),
    }

    for name, picks in strategies.items():

        special_pick = picks[0]

        attr = special_attributes(special_pick)

        print(
            f"{name:<12}: "
            + " ".join(f"{x:02d}" for x in picks)
            + f" + {special_pick:02d}"
        )

        print(f"特码属性: {attr}")

    specials = get_specials(conn)

    main_color, second_color = predict_colors(specials)

    print()
    print("特码波色预测（二中一）:")
    print(f"主强: {main_color}")
    print(f"次强: {second_color}")
    print(f"二中一: {main_color}/{second_color}")

    hit, total, rate = backtest_color_prediction(specials)

    print()
    print("最近10期真实特别号回测:")
    print(f"二中一命中: {hit}/{total}")
    print(f"命中率: {rate}%")

    miss = real_max_miss(specials)

    print()
    print("真实最大连空:")
    print(f"{miss} 期")

    print()
    print("推荐投注方案:")

    print(f"{main_color}: 300 元")
    print(f"{second_color}: 150 元")

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

    new_count = save_records(conn, records)

    print(f"同步完成: {len(records)} 条")

    show_dashboard(conn)

# =========================
# main
# =========================
def main():

    if len(sys.argv) < 2:
        sync()
        return

    cmd = sys.argv[1]

    if cmd == "sync":
        sync()
    else:
        sync()

if __name__ == "__main__":
    main()