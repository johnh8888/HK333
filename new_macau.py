# -*- coding: utf-8 -*-

import os
import re
import ssl
import json
import sqlite3
import urllib.request
import sys
from collections import Counter

DB_PATH = "macau.db"

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

ELEMENT = {
    "金": [4,5,12,13,26,27,34,35,48,49],
    "木": [3,8,17,18,25,30,39,40,47],
    "水": [2,9,10,23,24,31,32,45,46],
    "火": [1,14,15,22,29,36,37,44],
    "土": [6,7,11,16,19,20,21,28,33,38,41,42,43]
}

def init_db():

    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery(
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

def get_color(num):

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"

def get_element(num):

    for k,v in ELEMENT.items():

        if num in v:
            return k

    return "土"

def get_attrs(num):

    ds = "单" if num % 2 else "双"

    dx = "大" if num >= 25 else "小"

    hs = sum(int(x) for x in str(num))

    hds = "合单" if hs % 2 else "合双"

    hdx = "合大" if hs >= 7 else "合小"

    tail = num % 10

    tw = "尾大" if tail >= 5 else "尾小"

    color = get_color(num)

    element = get_element(num)

    return f"{ds}/{dx} {hds}/{hdx} {tw} {color} {element}"

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

            if "新澳门" not in name:
                continue

            issue = str(
                lottery.get("expect", "")
            ).strip()

            open_code = str(
                lottery.get("openCode", "")
            )

            nums = [
                int(x)
                for x in re.findall(r"\d+", open_code)
            ]

            if len(nums) >= 7:

                rows.append({
                    "issue": issue,
                    "nums": nums[:7]
                })

            histories = lottery.get(
                "history",
                []
            )

            for h in histories:

                try:

                    line = str(h).strip()

                    parts = re.findall(
                        r"\d+",
                        line
                    )

                    if len(parts) < 8:
                        continue

                    h_issue = parts[0]

                    h_nums = [
                        int(x)
                        for x in parts[1:8]
                    ]

                    rows.append({
                        "issue": h_issue,
                        "nums": h_nums
                    })

                except:
                    pass

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

        return rows[:600]

    except Exception as e:

        print(f"API获取失败: {e}")

        return []

def sync():

    init_db()

    rows = fetch_data()

    if not rows:

        print("未抓到真实开奖数据")
        return

    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()

    new_count = 0

    for row in rows:

        issue = row["issue"]

        nums = row["nums"]

        exists = cur.execute(
            "SELECT 1 FROM lottery WHERE issue=?",
            (issue,)
        ).fetchone()

        if exists:
            continue

        cur.execute(
            """
            INSERT INTO lottery
            (
                issue,
                n1,n2,n3,n4,n5,n6,special
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                issue,
                nums[0],
                nums[1],
                nums[2],
                nums[3],
                nums[4],
                nums[5],
                nums[6]
            )
        )

        new_count += 1

    conn.commit()

    total = cur.execute(
        "SELECT COUNT(*) FROM lottery"
    ).fetchone()[0]

    conn.close()

    print(
        f"数据同步完成: total={total}, new={new_count}"
    )

    predict()

def load_records():

    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute("""
    SELECT
    issue,n1,n2,n3,n4,n5,n6,special
    FROM lottery
    ORDER BY issue DESC
    """).fetchall()

    conn.close()

    return rows

def pick_hot(records):

    nums = []

    for r in records[:20]:
        nums += list(r[1:7])

    c = Counter(nums)

    return [x[0] for x in c.most_common(6)]

def pick_cold(records):

    nums = []

    for r in records[:50]:
        nums += list(r[1:7])

    c = Counter(nums)

    return [x[0] for x in c.most_common()[-6:]]

def predict_color(records):

    recent = records[:10]

    score = {
        "红":0,
        "蓝":0,
        "绿":0
    }

    weight = 10

    for r in recent:

        color = get_color(r[-1])

        score[color] += weight

        weight -= 1

    top2 = sorted(
        score.items(),
        key=lambda x:x[1],
        reverse=True
    )[:2]

    return top2

def predict_ds(records):

    recent = records[:10]

    big = 0
    small = 0

    odd = 0
    even = 0

    for r in recent:

        s = r[-1]

        if s >= 25:
            big += 1
        else:
            small += 1

        if s % 2:
            odd += 1
        else:
            even += 1

    size_predict = "大" if big >= small else "小"

    odd_even_predict = "单" if odd >= even else "双"

    return size_predict, odd_even_predict

def backtest_color(records):

    hit = 0
    total = 0

    max_miss = 0
    current_miss = 0

    for i in range(10, len(records)-1):

        recent = records[i-10:i]

        score = {
            "红":0,
            "蓝":0,
            "绿":0
        }

        weight = 10

        for r in recent:

            color = get_color(r[-1])

            score[color] += weight

            weight -= 1

        top2 = sorted(
            score.items(),
            key=lambda x:x[1],
            reverse=True
        )[:2]

        predict_colors = [
            x[0]
            for x in top2
        ]

        actual = get_color(
            records[i-1][-1]
        )

        total += 1

        if actual in predict_colors:

            hit += 1

            current_miss = 0

        else:

            current_miss += 1

            max_miss = max(
                max_miss,
                current_miss
            )

    rate = round(
        hit * 100 / total,
        1
    ) if total else 0

    return hit,total,rate,max_miss

def show_strategy(name, nums, special):

    print(f"  {name}　　　　: {' '.join(f'{x:02d}' for x in nums)} + {special:02d}")
    print(f"         特码属性: {get_attrs(special)}")

def predict():

    records = load_records()

    if len(records) < 20:

        print("数据不足")
        return

    latest = records[0]

    next_issue = str(
        int(latest[0]) + 1
    )

    print(f"最新开奖: {latest[0]} | {' '.join(f'{x:02d}' for x in latest[1:7])} + {latest[7]:02d}")

    print()

    print(f"预测期号: {next_issue}")

    hot = pick_hot(records)

    cold = pick_cold(records)

    combo = hot[:3] + cold[:3]

    vote = list(dict.fromkeys(
        hot + cold
    ))[:6]

    momentum = hot[::-1][:6]

    mining = latest[1:7]

    show_strategy("组合策略", combo, combo[0])

    show_strategy("冷号回补", cold, cold[0])

    show_strategy("集成投票", vote, vote[0])

    show_strategy("热号策略", hot, hot[0])

    show_strategy("近期动量", momentum, momentum[0])

    show_strategy("规律挖掘", mining, mining[0])

    print()

    top2 = predict_color(records)

    main_color = top2[0][0]
    second_color = top2[1][0]

    print("🎨 特码波色预测（最近10期真实数据）：")
    print(f"   主强: {main_color} (得分 {top2[0][1]})   次强: {second_color} (得分 {top2[1][1]})")

    print()

    size_predict, odd_even_predict = predict_ds(records)

    print("📊 大小单双预测（最近10期真实数据）：")
    print(f"   大小预测: {size_predict}   单双预测: {odd_even_predict}")

    print()

    hit,total,rate,max_miss = backtest_color(records)

    print("📊 历史回测（最近10期，二中一真实回测）：")
    print(f"   二中一命中率: {rate}%")
    print(f"   最近10期命中: {hit}/{total}")
    print(f"   最大连空: {max_miss}期")

if __name__ == "__main__":

    cmd = "sync"

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

    if cmd == "sync":
        sync()
    else:
        predict()