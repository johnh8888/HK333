# -*- coding: utf-8 -*-

import sqlite3
import requests
import argparse
from collections import Counter

DB_FILE = "new_macau.db"

API_URL = "https://api3.marksix6.net/lottery_api.php?type=newMacau"

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        n1 INT,
        n2 INT,
        n3 INT,
        n4 INT,
        n5 INT,
        n6 INT,
        special INT
    )
    """)

    conn.commit()

    return conn

def fetch_real_data():

    headers = {
        "User-Agent":"Mozilla/5.0"
    }

    r = requests.get(API_URL, headers=headers, timeout=30)

    if r.status_code != 200:
        return []

    data = r.json()

    if "data" not in data:
        return []

    rows = []

    for item in data["data"]:

        issue = str(item.get("expect","")).strip()

        opencode = str(item.get("opencode","")).strip()

        if not issue or not opencode:
            continue

        parts = opencode.split(",")

        if len(parts) != 7:
            continue

        try:

            nums = [int(x) for x in parts]

        except:
            continue

        ok = all(1 <= n <= 49 for n in nums)

        if not ok:
            continue

        rows.append((
            issue,
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            nums[6]
        ))

    return rows

def save_draws(conn, rows):

    for row in rows:

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?,?,?,?,?)
        """, row)

    conn.commit()

def load_rows(conn):

    cur = conn.cursor()

    cur.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT 80
    """)

    return cur.fetchall()

def color(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

def special_attr(n):

    ds = "单" if n % 2 else "双"

    dx = "大" if n >= 25 else "小"

    return f"{ds}/{dx} {color(n)}"

def hot_strategy(rows):

    nums = []

    for r in rows[:20]:
        nums.extend(r[1:8])

    c = Counter(nums)

    top = [n for n,_ in c.most_common(7)]

    return top[:6], top[6]

def cold_strategy(rows):

    nums = []

    for r in rows:
        nums.extend(r[1:8])

    c = Counter(nums)

    arr = sorted(c.items(), key=lambda x:x[1])

    cold = [n for n,_ in arr[:7]]

    return cold[:6], cold[6]

def momentum_strategy(rows):

    nums = []

    for r in rows[:10]:
        nums.extend(r[1:8])

    c = Counter(nums)

    top = [n for n,_ in c.most_common(7)]

    return top[:6], top[6]

def balanced_strategy(rows):

    hot,_ = hot_strategy(rows)

    cold,_ = cold_strategy(rows)

    nums = hot[:3] + cold[:3]

    return nums, hot[3]

def pattern_strategy(rows):

    even = []
    odd = []

    for r in rows[:15]:

        for n in r[1:8]:

            if n % 2:
                odd.append(n)
            else:
                even.append(n)

    nums = even[:3] + odd[:3]

    return nums, odd[3]

def ensemble_strategy(rows):

    all_nums = []

    for f in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy,
        pattern_strategy
    ]:

        n,_ = f(rows)

        all_nums.extend(n)

    c = Counter(all_nums)

    top = [n for n,_ in c.most_common(7)]

    return top[:6], top[6]

def predict_wave(rows):

    arr = []

    for r in rows[:10]:
        arr.append(color(r[7]))

    c = Counter(arr)

    top = c.most_common(2)

    return top[0][0], top[1][0]

def predict_bs(rows):

    sp = [r[7] for r in rows[:10]]

    big = sum(1 for x in sp if x >= 25)
    small = len(sp) - big

    odd = sum(1 for x in sp if x % 2)
    even = len(sp) - odd

    dx = "大" if big >= small else "小"

    ds = "单" if odd >= even else "双"

    return dx, ds

def max_miss(rows, target):

    miss = 0
    best = 0

    for r in rows[:10]:

        c = color(r[7])

        if c != target:
            miss += 1
            best = max(best, miss)
        else:
            miss = 0

    return best

def recent_hit(rows, func):

    total = 0
    count = 0

    limit = min(10, len(rows)-1)

    for i in range(1, limit):

        past = rows[i:]

        pred,_ = func(past)

        real = rows[i-1][1:8]

        hit = len(set(pred) & set(real))

        total += hit

        count += 1

    if count == 0:
        return 0

    return round(total / count, 2)

def print_strategy(name, result):

    nums, sp = result

    s = " ".join(f"{x:02d}" for x in nums)

    print(f"{name:<10}: {s} + {sp:02d}")

    print(f"特码属性: {special_attr(sp)}")

def sync():

    conn = init_db()

    rows = fetch_real_data()

    if not rows:
        print("未抓到真实开奖数据")
        return

    save_draws(conn, rows)

    dbrows = load_rows(conn)

    latest = dbrows[0]

    print(f"同步完成: {len(dbrows)} 条")
    print()

    print("最新开奖:")

    print(f"{latest[0]} | {' '.join(f'{x:02d}' for x in latest[1:7])} + {latest[7]:02d}")

    print()

    next_issue = str(int(latest[0]) + 1)

    print(f"预测期号: {next_issue}")

    strategies = {
        "组合策略":balanced_strategy,
        "热号策略":hot_strategy,
        "冷号回补":cold_strategy,
        "近期动量":momentum_strategy,
        "集成投票":ensemble_strategy,
        "规律挖掘":pattern_strategy
    }

    for k,v in strategies.items():

        print_strategy(k, v(dbrows))

    print()

    mw, sw = predict_wave(dbrows)

    print("特码波色预测:")
    print(f"主强: {mw} 次强: {sw}")

    print()

    dx, ds = predict_bs(dbrows)

    print("大小单双预测:")
    print(f"大小: {dx}")
    print(f"单双: {ds}")

    print()

    print("最大连空:")
    print(f"红波: {max_miss(dbrows,'红')}期")
    print(f"蓝波: {max_miss(dbrows,'蓝')}期")
    print(f"绿波: {max_miss(dbrows,'绿')}期")

    print()

    print("推荐投注方案:")
    print(f"{mw}: 450 元")
    print(f"{sw}: 150 元")
    print(f"{dx}: 200 元")
    print(f"{ds}: 200 元")

    print()

    print("赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小单双: 1.95")

    print()

    print("最近10期历史命中统计:")

    for k,v in strategies.items():

        avg = recent_hit(dbrows, v)

        print(f"{k:<10}: 期数=10 平均命中={avg}")

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("cmd", choices=["sync"])

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()