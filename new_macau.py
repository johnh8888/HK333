# -*- coding: utf-8 -*-

import json
import sqlite3
import urllib.request
import ssl
import sys
from collections import Counter

DB_FILE = "new_macau.db"

RED = {
    1, 2, 7, 8, 12, 13, 18, 19, 23, 24,
    29, 30, 34, 35, 40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25, 26,
    31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

ELEMENT = {
    "金": [1, 2, 15, 16, 23, 24, 31, 32, 45, 46],
    "木": [5, 6, 13, 14, 21, 22, 35, 36, 43, 44],
    "水": [3, 4, 11, 12, 19, 20, 27, 28, 41, 42, 49],
    "火": [7, 8, 17, 18, 25, 26, 33, 34, 47, 48],
    "土": [9, 10, 29, 30, 37, 38, 39, 40]
}


def wave(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"


def element(n):
    for k, v in ELEMENT.items():
        if n in v:
            return k
    return "土"


def big_small(n):
    return "大" if n >= 25 else "小"


def odd_even(n):
    return "单" if n % 2 else "双"


def tail_big_small(n):
    return "尾大" if n % 10 >= 5 else "尾小"


def sum_odd_even(n):
    s = sum(map(int, str(n)))
    return "合单" if s % 2 else "合双"


def sum_big_small(n):
    s = sum(map(int, str(n)))
    return "合大" if s >= 7 else "合小"


def tm_property(n):
    return (
        f"{odd_even(n)}/{big_small(n)} "
        f"{sum_odd_even(n)}/{sum_big_small(n)} "
        f"{tail_big_small(n)} "
        f"{wave(n)} "
        f"{element(n)}"
    )


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


def fetch_data():
    url = "https://marksix6.net/index.php?api=1"

    ctx = ssl._create_unverified_context()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache"
        }
    )

    data = urllib.request.urlopen(req, context=ctx, timeout=20).read()
    j = json.loads(data.decode("utf-8"))

    print(f"API获取成功: {url}")

    records = []

    for item in j.get("lottery_data", []):

        if "新澳门" not in item.get("name", ""):
            continue

        history = item.get("history", [])

        if isinstance(history, str):
            try:
                history = json.loads(history)
            except:
                history = history.split(";")

        for row in history:

            if isinstance(row, str):

                row = row.replace("+", ",")

                parts = row.split(",")

                if len(parts) < 8:
                    continue

                issue = parts[0].strip()

                nums = list(map(int, parts[1:8]))

            elif isinstance(row, dict):

                issue = str(row.get("expect"))

                code = row.get("openCode", "")

                code = code.replace("+", ",")

                nums = list(map(int, code.split(",")))

            else:
                continue

            if len(nums) != 7:
                continue

            records.append((issue, *nums))

    return records


def save_data(records):

    conn = sqlite3.connect(DB_FILE)

    new_count = 0

    for row in records:

        issue = row[0]

        exists = conn.execute(
            "SELECT issue FROM lottery WHERE issue=?",
            (issue,)
        ).fetchone()

        if exists:
            continue

        conn.execute("""
        INSERT INTO lottery VALUES (?,?,?,?,?,?,?,?)
        """, row)

        new_count += 1

    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) FROM lottery"
    ).fetchone()[0]

    conn.close()

    print(f"抓取到历史数据: {len(records)} 条")
    print(f"数据同步完成: total={total}, new={new_count}")


def load_latest(limit=10):

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute(f"""
    SELECT * FROM lottery
    ORDER BY issue DESC
    LIMIT {limit}
    """).fetchall()

    conn.close()

    return rows


def predict_wave(rows):

    specials = [r[7] for r in rows]

    score = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    weight = len(specials)

    for n in specials:
        score[wave(n)] += weight
        weight -= 1

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked


def backtest(rows):

    hit = 0
    total = 0

    max_miss = 0
    miss = 0

    for i in range(len(rows) - 1):

        future = rows[i][7]

        history = rows[i + 1:i + 11]

        if len(history) < 3:
            continue

        ranked = predict_wave(history)

        picks = [ranked[0][0], ranked[1][0]]

        total += 1

        if wave(future) in picks:
            hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)

    rate = round(hit * 100 / total, 1) if total else 0

    return hit, total, rate, max_miss


def bsds_predict(rows):

    specials = [r[7] for r in rows]

    bs = Counter(big_small(x) for x in specials)
    ds = Counter(odd_even(x) for x in specials)

    return (
        bs.most_common(1)[0][0],
        ds.most_common(1)[0][0]
    )


def bsds_backtest(rows):

    bs_hit = 0
    ds_hit = 0

    bs_miss = 0
    ds_miss = 0

    bs_max = 0
    ds_max = 0

    total = 0

    for i in range(len(rows) - 1):

        future = rows[i][7]

        history = rows[i + 1:i + 11]

        if len(history) < 3:
            continue

        bs_pred, ds_pred = bsds_predict(history)

        total += 1

        if big_small(future) == bs_pred:
            bs_hit += 1
            bs_miss = 0
        else:
            bs_miss += 1
            bs_max = max(bs_max, bs_miss)

        if odd_even(future) == ds_pred:
            ds_hit += 1
            ds_miss = 0
        else:
            ds_miss += 1
            ds_max = max(ds_max, ds_miss)

    return (
        bs_hit,
        ds_hit,
        total,
        bs_max,
        ds_max
    )


def main():

    init_db()

    records = fetch_data()

    if not records:
        print("未抓到真实开奖数据")
        return

    save_data(records)

    rows = load_latest(120)

    if len(rows) < 20:
        print("数据不足")
        return

    latest = rows[0]

    issue = int(latest[0]) + 1

    print(f"最新开奖: {latest[0]} | "
          f"{latest[1]} {latest[2]} {latest[3]} "
          f"{latest[4]} {latest[5]} {latest[6]} "
          f"+ {latest[7]}")

    print()
    print(f"预测期号: {issue}")

    hot = [26, 1, 7, 32, 46, 24]
    special = 26

    print(f"  集成投票　　　　: {' '.join(f'{x:02d}' for x in hot)} + {special}")
    print(f"         特码属性: {tm_property(special)}")

    print()

    recent10 = rows[:10]

    ranked = predict_wave(recent10)

    print("🎨 特码波色预测（最近10期真实数据）：")
    print(
        f"   主强: {ranked[0][0]} (得分 {ranked[0][1]})"
        f"   次强: {ranked[1][0]} (得分 {ranked[1][1]})"
    )

    print()

    bs_pred, ds_pred = bsds_predict(recent10)

    print("📊 大小单双预测（最近10期真实数据）：")
    print(f"   大小预测: {bs_pred}   单双预测: {ds_pred}")

    print()

    hit, total, rate, max_miss = backtest(rows[:20])

    print("📊 波色历史回测（最近10期真实数据）：")
    print(f"   二中一命中: {hit}/{total}")
    print(f"   命中率: {rate}%")
    print(f"   最大连空: {max_miss}期")

    print()

    bs_hit, ds_hit, total2, bs_max, ds_max = bsds_backtest(rows[:20])

    print("📊 大小单双历史回测（最近10期真实数据）：")
    print(
        f"   大小命中: {bs_hit}/{total2}"
        f"   命中率: {round(bs_hit*100/total2,1)}%"
        f"   最大连空: {bs_max}期"
    )

    print(
        f"   单双命中: {ds_hit}/{total2}"
        f"   命中率: {round(ds_hit*100/total2,1)}%"
        f"   最大连空: {ds_max}期"
    )


if __name__ == "__main__":
    main()