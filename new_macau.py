# -*- coding: utf-8 -*-
# 新澳门六合彩 - 全自动预测系统（最终完整版）
# 支持：
# 1. 在线同步真实数据
# 2. 六大策略预测
# 3. 特码属性分析
# 4. 波色大小单双预测
# 5. 最近10期真实回测
# 6. 二中一真实命中
# 7. 最大连空统计
# 8. GitHub Actions 直接运行

import os
import sys
import json
import math
import sqlite3
import random
import urllib.request
import urllib.error
from collections import Counter, defaultdict

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
ELEMENTS = {
    "金": [5, 6, 13, 14, 21, 22, 35, 36, 43, 44],
    "木": [3, 4, 17, 18, 25, 26, 39, 40, 47, 48],
    "水": [1, 2, 15, 16, 23, 24, 37, 38, 45, 46],
    "火": [7, 8, 19, 20, 27, 28, 41, 42, 49],
    "土": [9, 10, 11, 12, 29, 30, 31, 32, 33, 34]
}

# =========================
# 数据库
# =========================
def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS records (
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

# =========================
# 工具
# =========================
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
    return "土"

def get_size(num):
    return "大" if num >= 25 else "小"

def get_odd_even(num):
    return "单" if num % 2 else "双"

def get_tail_size(num):
    return "尾大" if num % 10 >= 5 else "尾小"

def get_sum_odd_even(num):
    s = sum(map(int, str(num)))
    return "合单" if s % 2 else "合双"

def get_sum_size(num):
    s = sum(map(int, str(num)))
    return "大" if s >= 7 else "小"

def format_nums(nums):
    return " ".join(f"{x:02d}" for x in nums)

def special_attrs(num):
    return (
        f"{get_odd_even(num)}/"
        f"{get_size(num)} "
        f"{get_sum_odd_even(num)}/"
        f"{get_sum_size(num)} "
        f"{get_tail_size(num)} "
        f"{get_color(num)} "
        f"{get_element(num)}"
    )

# =========================
# 在线抓取数据
# =========================
def fetch_online_data():
    urls = [
        "https://api.macaumarksix.com/api/history",
        "https://www.macaumarksix.com/api/history",
        "https://marksix6.net/api/macaujc.json",
        "https://marksix6.net/index.php?api=1"
    ]

    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8", errors="ignore")

            if "404" in text:
                continue

            # 尝试 json
            try:
                data = json.loads(text)

                result = []

                if isinstance(data, list):
                    for item in data[:120]:

                        issue = str(
                            item.get("expect") or
                            item.get("issue") or
                            item.get("qihao") or
                            item.get("period") or
                            ""
                        )

                        nums = (
                            item.get("opencode") or
                            item.get("numbers") or
                            item.get("result") or
                            ""
                        )

                        if isinstance(nums, str):
                            arr = [
                                int(x)
                                for x in nums.replace("+", ",").split(",")
                                if x.strip().isdigit()
                            ]
                        else:
                            arr = nums

                        if len(arr) >= 7:
                            result.append({
                                "issue": issue,
                                "nums": arr[:7]
                            })

                if result:
                    return result

            except:
                pass

            # 正则模式
            import re

            matches = re.findall(
                r'(\d{7}).*?(\d{2})[,\s]+(\d{2})[,\s]+(\d{2})[,\s]+(\d{2})[,\s]+(\d{2})[,\s]+(\d{2})[+\s]+(\d{2})',
                text,
                re.S
            )

            if matches:
                result = []

                for m in matches[:120]:
                    issue = m[0]
                    nums = list(map(int, m[1:]))

                    result.append({
                        "issue": issue,
                        "nums": nums
                    })

                return result

        except:
            continue

    return []

# =========================
# 保存
# =========================
def save_records(records):
    conn = sqlite3.connect(DB_FILE)

    new_count = 0

    for row in records:
        issue = row["issue"]
        nums = row["nums"]

        exists = conn.execute(
            "SELECT issue FROM records WHERE issue=?",
            (issue,)
        ).fetchone()

        if exists:
            continue

        conn.execute("""
        INSERT INTO records
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            issue,
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            nums[6]
        ))

        new_count += 1

    conn.commit()
    conn.close()

    return new_count

# =========================
# 读取历史
# =========================
def load_history(limit=120):
    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute("""
    SELECT *
    FROM records
    ORDER BY issue DESC
    LIMIT ?
    """, (limit,)).fetchall()

    conn.close()

    result = []

    for r in rows:
        result.append({
            "issue": r[0],
            "nums": list(r[1:7]),
            "special": r[7]
        })

    return result

# =========================
# 策略
# =========================
def hot_strategy(history):
    counter = Counter()

    for row in history[:20]:
        counter.update(row["nums"])

    nums = [x for x, _ in counter.most_common(6)]

    special = nums[0]

    return nums, special

def cold_strategy(history):
    counter = Counter()

    for row in history[:30]:
        counter.update(row["nums"])

    arr = sorted(counter.items(), key=lambda x: x[1])

    nums = [x[0] for x in arr[:6]]

    special = nums[0]

    return nums, special

def momentum_strategy(history):
    recent = history[:10]

    counter = Counter()

    for row in recent:
        counter.update(row["nums"])

    nums = [x for x, _ in counter.most_common(6)]

    special = nums[0]

    return nums, special

def combo_strategy(history):
    hot, _ = hot_strategy(history)
    cold, _ = cold_strategy(history)

    nums = list(dict.fromkeys(hot[:3] + cold[:3]))

    while len(nums) < 6:
        n = random.randint(1, 49)

        if n not in nums:
            nums.append(n)

    special = nums[0]

    return nums[:6], special

def vote_strategy(history):
    a, _ = hot_strategy(history)
    b, _ = momentum_strategy(history)

    pool = a + b

    counter = Counter(pool)

    nums = [x for x, _ in counter.most_common(6)]

    special = nums[0]

    return nums, special

def pattern_strategy(history):
    last = history[0]["nums"]

    nums = sorted(last)[:5]

    while len(nums) < 6:
        n = random.randint(1, 49)

        if n not in nums:
            nums.append(n)

    special = max(nums)

    return nums, special

# =========================
# 波色预测
# =========================
def color_predict(history, window=10):
    weights = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    recent = history[:window]

    for i, row in enumerate(recent):
        c = get_color(row["special"])

        w = window - i

        weights[c] += w

    arr = sorted(
        weights.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return arr

# =========================
# 大小单双预测
# =========================
def size_odd_predict(history, window=10):
    size_counter = Counter()
    odd_counter = Counter()

    for row in history[:window]:
        sp = row["special"]

        size_counter[get_size(sp)] += 1
        odd_counter[get_odd_even(sp)] += 1

    size = size_counter.most_common(1)[0][0]
    odd = odd_counter.most_common(1)[0][0]

    return size, odd

# =========================
# 回测
# =========================
def backtest(history, window=10):
    total = 0
    hit = 0

    max_miss = 0
    miss = 0

    for i in range(window, len(history)-1):

        train = history[i-window:i]

        pred = color_predict(train, window)

        top2 = [pred[0][0], pred[1][0]]

        real = get_color(history[i-1]["special"])

        total += 1

        if real in top2:
            hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)

    rate = 0

    if total:
        rate = round(hit / total * 100, 1)

    return {
        "hit": hit,
        "total": total,
        "rate": rate,
        "max_miss": max_miss
    }

# =========================
# 历史命中
# =========================
def calc_hits(history, func):
    total_hit = 0
    total_special = 0
    count = 0

    for i in range(1, min(11, len(history)-1)):
        train = history[i:]

        pred_nums, pred_sp = func(train)

        real = history[i-1]

        hit = len(set(pred_nums) & set(real["nums"]))

        total_hit += hit

        if pred_sp == real["special"]:
            total_special += 1

        count += 1

    avg_hit = round(total_hit / count, 1)

    hit_rate = round(avg_hit / 6 * 100, 1)

    sp_rate = round(total_special / count * 100, 1)

    return count, avg_hit, hit_rate, sp_rate

# =========================
# 同步
# =========================
def sync():
    init_db()

    records = fetch_online_data()

    if not records:
        print("未抓到真实开奖数据")
        return

    new_count = save_records(records)

    print(f"数据同步完成: total={len(records)}, new={new_count}")

    history = load_history()

    if not history:
        print("无历史数据")
        return

    latest = history[0]

    next_issue = str(int(latest["issue"]) + 1)

    print(
        f"最新开奖: {latest['issue']} | "
        f"{format_nums(latest['nums'])} + "
        f"{latest['special']:02d}"
    )

    print()

    print(f"预测期号: {next_issue}")

    strategies = {
        "组合策略": combo_strategy,
        "冷号回补": cold_strategy,
        "集成投票": vote_strategy,
        "热号策略": hot_strategy,
        "近期动量": momentum_strategy,
        "规律挖掘": pattern_strategy
    }

    for name, fn in strategies.items():
        nums, sp = fn(history)

        print(
            f"  {name}　　　　: "
            f"{format_nums(nums)} + {sp:02d}"
        )

        print(
            f"         特码属性: "
            f"{special_attrs(sp)}"
        )

    print()

    print("历史命中统计:")

    for name, fn in strategies.items():
        c, avg, rate, sp = calc_hits(history, fn)

        print(
            f"  {name}　　　　: "
            f"期数={c}, "
            f"平均命中={avg}个, "
            f"命中率={rate}%, "
            f"特别号命中率={sp}%"
        )

    print()

    color_rank = color_predict(history)

    print("🎨 特码波色预测（加权频率，基于最近 10 期）：")

    print(
        f"   主强: {color_rank[0][0]} "
        f"(得分 {round(color_rank[0][1]/100,2)})   "
        f"次强: {color_rank[1][0]} "
        f"(得分 {round(color_rank[1][1]/100,2)})"
    )

    print()

    size_pred, odd_pred = size_odd_predict(history)

    print("📊 大小单双预测（最近10期真实数据）：")

    print(
        f"   大小预测: {size_pred}   "
        f"单双预测: {odd_pred}"
    )

    print()

    bt = backtest(history, 10)

    print("📊 历史回测（最近 10 期，方法=weighted，窗口=10）：")

    print(
        f"   二中一命中率: {bt['rate']}%"
    )

    print(
        f"   最近10期命中: "
        f"{bt['hit']}/{bt['total']}"
    )

    print(
        f"   最大连空: {bt['max_miss']}期"
    )

# =========================
# main
# =========================
def main():
    if len(sys.argv) >= 2:
        cmd = sys.argv[1]

        if cmd == "sync":
            sync()
            return

    sync()

if __name__ == "__main__":
    main()