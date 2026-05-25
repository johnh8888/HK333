# -*- coding: utf-8 -*-

import json
import sqlite3
import urllib.request
import ssl
import sys
from collections import Counter

DB_FILE = "new_macau.db"

# =========================================
# 波色
# =========================================

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

# =========================================
# 五行
# =========================================

ELEMENT_MAP = {
    "金": {1, 2, 15, 16, 23, 24, 31, 32, 45, 46},
    "木": {5, 6, 13, 14, 21, 22, 35, 36, 43, 44},
    "水": {3, 4, 11, 12, 19, 20, 27, 28, 41, 42, 49},
    "火": {7, 8, 17, 18, 25, 26, 33, 34, 47, 48},
    "土": {9, 10, 29, 30, 37, 38, 39, 40}
}

# =========================================
# 工具
# =========================================

def get_color(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"


def get_element(n):
    for k, v in ELEMENT_MAP.items():
        if n in v:
            return k
    return "土"


def get_big_small(n):
    return "大" if n >= 25 else "小"


def get_odd_even(n):
    return "双" if n % 2 == 0 else "单"


def get_sum_type(n):
    s = sum(map(int, str(n)))
    return "合双" if s % 2 == 0 else "合单"


def get_sum_big_small(n):
    s = sum(map(int, str(n)))
    return "合大" if s >= 7 else "合小"


def get_tail_big_small(n):
    t = n % 10
    return "尾大" if t >= 5 else "尾小"


def special_info(n):
    return (
        f"{get_odd_even(n)}/"
        f"{get_big_small(n)} "
        f"{get_sum_type(n)}/"
        f"{get_sum_big_small(n)} "
        f"{get_tail_big_small(n)} "
        f"{get_color(n)} "
        f"{get_element(n)}"
    )

# =========================================
# 数据库
# =========================================

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

# =========================================
# API抓取
# =========================================

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

    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
        data = json.loads(r.read().decode())

    print(f"API获取成功: {url}")

    records = []

    for item in data["lottery_data"]:

        history = item.get("history", [])

        for row in history:

            try:

                parts = row.split("|")

                issue = parts[0].strip()

                nums = parts[1].strip().split(",")

                nums = [int(x) for x in nums]

                if len(nums) != 7:
                    continue

                records.append({
                    "issue": issue,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

            except:
                pass

    print(f"抓取到历史数据: {len(records)} 条")

    return records

# =========================================
# 保存
# =========================================

def save_records(records):

    conn = sqlite3.connect(DB_FILE)

    new_count = 0

    for r in records:

        exists = conn.execute(
            "SELECT issue FROM lottery WHERE issue=?",
            (r["issue"],)
        ).fetchone()

        if exists:
            continue

        nums = r["numbers"]

        conn.execute("""
        INSERT INTO lottery VALUES(
            ?,?,?,?,?,?,?,?
        )
        """, (
            r["issue"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            r["special"]
        ))

        new_count += 1

    conn.commit()
    conn.close()

    return new_count

# =========================================
# 读取
# =========================================

def load_records():

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute("""
    SELECT *
    FROM lottery
    ORDER BY issue
    """).fetchall()

    conn.close()

    records = []

    for r in rows:

        records.append({
            "issue": r[0],
            "numbers": [r[1], r[2], r[3], r[4], r[5], r[6]],
            "special": r[7]
        })

    return records

# =========================================
# 策略
# =========================================

def hot_strategy(records):

    nums = []

    for r in records[-20:]:
        nums.extend(r["numbers"])

    c = Counter(nums)

    picks = [x[0] for x in c.most_common(6)]

    special = picks[0]

    return picks, special


def cold_strategy(records):

    nums = []

    for r in records[-30:]:
        nums.extend(r["numbers"])

    c = Counter(nums)

    all_nums = set(range(1, 50))

    miss = []

    for n in all_nums:
        miss.append((n, c.get(n, 0)))

    miss.sort(key=lambda x: x[1])

    picks = [x[0] for x in miss[:6]]

    special = picks[0]

    return picks, special


def momentum_strategy(records):

    nums = []

    for r in records[-10:]:
        nums.extend(r["numbers"])

    c = Counter(nums)

    picks = [x[0] for x in c.most_common(6)]

    special = picks[-1]

    return picks, special


def pattern_strategy(records):

    latest = records[-1]

    picks = latest["numbers"][:]

    special = latest["numbers"][0]

    return picks, special


def ensemble_strategy(records):

    a, _ = hot_strategy(records)
    b, _ = momentum_strategy(records)

    mix = list(dict.fromkeys(a + b))

    picks = mix[:6]

    special = picks[0]

    return picks, special


def combo_strategy(records):

    a, _ = hot_strategy(records)
    b, _ = cold_strategy(records)

    mix = list(dict.fromkeys(a[:3] + b[:3]))

    special = mix[0]

    return mix[:6], special

# =========================================
# 波色预测
# =========================================

def predict_wave(records):

    recent = records[-10:]

    score = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    weight = 10

    for r in recent:

        c = get_color(r["special"])

        score[c] += weight

        weight -= 1

    s = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return s[0], s[1]

# =========================================
# 大小单双预测
# =========================================

def predict_bs_oe(records):

    recent = records[-10:]

    big = 0
    small = 0

    odd = 0
    even = 0

    for r in recent:

        tm = r["special"]

        if tm >= 25:
            big += 1
        else:
            small += 1

        if tm % 2 == 0:
            even += 1
        else:
            odd += 1

    bs = "大" if big >= small else "小"

    oe = "双" if even >= odd else "单"

    return bs, oe

# =========================================
# 最近10期真实回测
# =========================================

def backtest_recent_10(records):

    if len(records) < 20:
        return None

    test_records = records[-10:]

    wave_hit = 0
    wave_miss = 0
    wave_max_miss = 0

    bs_hit = 0
    bs_miss = 0
    bs_max_miss = 0

    oe_hit = 0
    oe_miss = 0
    oe_max_miss = 0

    for i in range(10):

        history = records[: len(records) - 10 + i]

        recent = history[-10:]

        # 波色

        score = {
            "红": 0,
            "蓝": 0,
            "绿": 0
        }

        weight = 10

        for r in recent:

            c = get_color(r["special"])

            score[c] += weight

            weight -= 1

        s = sorted(
            score.items(),
            key=lambda x: x[1],
            reverse=True
        )

        pred1 = s[0][0]
        pred2 = s[1][0]

        real_tm = test_records[i]["special"]

        real_color = get_color(real_tm)

        if real_color in [pred1, pred2]:

            wave_hit += 1

            wave_miss = 0

        else:

            wave_miss += 1

            wave_max_miss = max(
                wave_max_miss,
                wave_miss
            )

        # 大小

        big = 0
        small = 0

        for r in recent:

            if r["special"] >= 25:
                big += 1
            else:
                small += 1

        pred_bs = "大" if big >= small else "小"

        real_bs = "大" if real_tm >= 25 else "小"

        if pred_bs == real_bs:

            bs_hit += 1

            bs_miss = 0

        else:

            bs_miss += 1

            bs_max_miss = max(
                bs_max_miss,
                bs_miss
            )

        # 单双

        odd = 0
        even = 0

        for r in recent:

            if r["special"] % 2 == 0:
                even += 1
            else:
                odd += 1

        pred_oe = "双" if even >= odd else "单"

        real_oe = "双" if real_tm % 2 == 0 else "单"

        if pred_oe == real_oe:

            oe_hit += 1

            oe_miss = 0

        else:

            oe_miss += 1

            oe_max_miss = max(
                oe_max_miss,
                oe_miss
            )

    return {
        "wave_hit": wave_hit,
        "wave_rate": round(wave_hit / 10 * 100, 1),
        "wave_max_miss": wave_max_miss,

        "bs_hit": bs_hit,
        "bs_rate": round(bs_hit / 10 * 100, 1),
        "bs_max_miss": bs_max_miss,

        "oe_hit": oe_hit,
        "oe_rate": round(oe_hit / 10 * 100, 1),
        "oe_max_miss": oe_max_miss
    }

# =========================================
# 主程序
# =========================================

def sync():

    init_db()

    data = fetch_data()

    if not data:
        print("未抓到真实开奖数据")
        return

    new_count = save_records(data)

    records = load_records()

    print(
        f"数据同步完成: total={len(records)}, new={new_count}"
    )

    if len(records) < 20:
        print("数据不足")
        return

    latest = records[-1]

    print(
        f"最新开奖: {latest['issue']} | "
        f"{' '.join(f'{x:02d}' for x in latest['numbers'])} "
        f"+ {latest['special']:02d}"
    )

    next_issue = str(int(latest["issue"]) + 1)

    print()
    print(f"预测期号: {next_issue}")

    strategies = {
        "组合策略": combo_strategy,
        "冷号回补": cold_strategy,
        "集成投票": ensemble_strategy,
        "热号策略": hot_strategy,
        "近期动量": momentum_strategy,
        "规律挖掘": pattern_strategy
    }

    for name, func in strategies.items():

        picks, special = func(records)

        print(
            f"  {name}　　　　: "
            f"{' '.join(f'{x:02d}' for x in picks)} "
            f"+ {special:02d}"
        )

        print(
            f"         特码属性: "
            f"{special_info(special)}"
        )

    # 波色

    print()

    main_wave, second_wave = predict_wave(records)

    print("🎨 特码波色预测（最近10期真实数据）：")

    print(
        f"   主强: {main_wave[0]} "
        f"(得分 {main_wave[1]})   "
        f"次强: {second_wave[0]} "
        f"(得分 {second_wave[1]})"
    )

    # 大小单双

    bs, oe = predict_bs_oe(records)

    print()

    print("📊 大小单双预测（最近10期真实数据）：")

    print(
        f"   大小预测: {bs}   "
        f"单双预测: {oe}"
    )

    # 回测

    bt = backtest_recent_10(records)

    print()

    print("📊 历史回测（最近10期真实数据）：")

    print(
        f"   波色二中一命中: "
        f"{bt['wave_hit']}/10   "
        f"命中率: {bt['wave_rate']}%   "
        f"最大连空: {bt['wave_max_miss']}期"
    )

    print(
        f"   大小命中: "
        f"{bt['bs_hit']}/10   "
        f"命中率: {bt['bs_rate']}%   "
        f"最大连空: {bt['bs_max_miss']}期"
    )

    print(
        f"   单双命中: "
        f"{bt['oe_hit']}/10   "
        f"命中率: {bt['oe_rate']}%   "
        f"最大连空: {bt['oe_max_miss']}期"
    )

# =========================================
# main
# =========================================

def main():

    cmd = "sync"

    if len(sys.argv) >= 2:
        cmd = sys.argv[1]

    if cmd == "sync":
        sync()
    else:
        sync()


if __name__ == "__main__":
    main()