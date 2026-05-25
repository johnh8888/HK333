# -*- coding: utf-8 -*-
import sqlite3
import requests
import random
from collections import Counter, defaultdict

DB_FILE = "new_macau.db"

# =========================
# 波色
# =========================

RED = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

# =========================
# 五行
# =========================

ELEMENTS = {
    "金": {1, 2, 15, 16, 23, 24, 31, 32, 45, 46},
    "木": {5, 6, 13, 14, 27, 28, 35, 36, 43, 44},
    "水": {9, 10, 17, 18, 25, 26, 39, 40, 47, 48},
    "火": {3, 4, 11, 12, 19, 20, 33, 34, 41, 42, 49},
    "土": {7, 8, 21, 22, 29, 30, 37, 38}
}

# =========================
# 数据库
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
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
    CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT,
        strategy TEXT,
        numbers TEXT,
        special INTEGER
    )
    """)

    conn.commit()
    return conn

# =========================
# 工具
# =========================

def wave_color(n):
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

def size_text(n):
    return "大" if n >= 25 else "小"

def odd_even(n):
    return "单" if n % 2 else "双"

def num_tail(n):
    return n % 10

# =========================
# 模拟真实开奖数据
# =========================

def fake_latest_draws():
    draws = []

    start = 2026065

    for i in range(80):
        nums = random.sample(range(1, 50), 7)

        draws.append({
            "period": str(start + i),
            "nums": nums[:6],
            "special": nums[6]
        })

    return draws

# =========================
# 保存开奖
# =========================

def save_draws(conn, draws):
    for d in draws:
        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?,?,?,?,?)
        """, (
            d["period"],
            d["nums"][0],
            d["nums"][1],
            d["nums"][2],
            d["nums"][3],
            d["nums"][4],
            d["nums"][5],
            d["special"]
        ))

    conn.commit()

# =========================
# 读取开奖
# =========================

def load_draws(conn):
    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY period
    """).fetchall()

    result = []

    for r in rows:
        result.append({
            "period": r[0],
            "nums": list(r[1:7]),
            "special": r[7]
        })

    return result

# =========================
# 策略
# =========================

def hot_strategy(draws):
    freq = Counter()

    for d in draws[-20:]:
        freq.update(d["nums"] + [d["special"]])

    nums = [x for x, _ in freq.most_common(7)]

    return nums[:6], nums[6]

def cold_strategy(draws):
    freq = Counter()

    for d in draws[-20:]:
        freq.update(d["nums"] + [d["special"]])

    nums = sorted(range(1, 50), key=lambda x: freq[x])

    return nums[:6], nums[6]

def momentum_strategy(draws):
    freq = Counter()

    for d in draws[-10:]:
        freq.update(d["nums"] + [d["special"]])

    nums = [x for x, _ in freq.most_common(7)]

    return nums[:6], nums[6]

def balanced_strategy(draws):
    freq = Counter()

    for d in draws[-15:]:
        freq.update(d["nums"] + [d["special"]])

    hot = [x for x, _ in freq.most_common(15)]

    nums = random.sample(hot, 7)

    return nums[:6], nums[6]

def pattern_strategy(draws):
    freq = Counter()

    for d in draws[-12:]:
        freq.update(d["nums"])

    nums = [x for x, _ in freq.most_common(7)]

    while len(nums) < 7:
        n = random.randint(1, 49)

        if n not in nums:
            nums.append(n)

    return nums[:6], nums[6]

def ensemble_strategy(draws):
    all_nums = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy
    ]:
        n, s = fn(draws)
        all_nums.extend(n)
        all_nums.append(s)

    freq = Counter(all_nums)

    nums = [x for x, _ in freq.most_common(7)]

    return nums[:6], nums[6]

# =========================
# 保存预测
# =========================

def save_prediction(conn, period, strategy, nums, special):
    conn.execute("""
    INSERT INTO predictions(period,strategy,numbers,special)
    VALUES(?,?,?,?)
    """, (
        period,
        strategy,
        ",".join(map(str, nums)),
        special
    ))

    conn.commit()

# =========================
# 波色预测
# =========================

def predict_wave(draws):
    colors = []

    for d in draws[-10:]:
        colors.append(wave_color(d["special"]))

    freq = Counter(colors)

    top = freq.most_common(2)

    main = top[0][0]
    second = top[1][0]

    return main, second

# =========================
# 大小单双
# =========================

def predict_size_odd(draws):
    specials = [d["special"] for d in draws[-10:]]

    big = sum(1 for x in specials if x >= 25)
    small = 10 - big

    odd = sum(1 for x in specials if x % 2)
    even = 10 - odd

    size_pred = "大" if big >= small else "小"
    odd_pred = "单" if odd >= even else "双"

    return size_pred, odd_pred

# =========================
# 最大连中
# =========================

def max_hit_streak(draws):
    recent = draws[-10:]

    streak = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    current = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    for d in recent:
        color = wave_color(d["special"])

        for c in current:
            if c == color:
                current[c] += 1
            else:
                current[c] = 0

            streak[c] = max(streak[c], current[c])

    return streak

# =========================
# 最近10期回测
# =========================

def recent_backtest(conn):
    rows = conn.execute("""
    SELECT period,strategy,numbers,special
    FROM predictions
    ORDER BY id DESC
    LIMIT 60
    """).fetchall()

    draws = {
        d["period"]: d
        for d in load_draws(conn)
    }

    stats = defaultdict(list)

    for period, strategy, numbers, special in rows:
        if period not in draws:
            continue

        real = draws[period]

        nums = list(map(int, numbers.split(",")))

        hit = len(set(nums) & set(real["nums"]))

        if special == real["special"]:
            hit += 1

        stats[strategy].append(hit)

    print("\n最近10期历史命中统计:")

    for k, v in stats.items():
        v = v[:10]

        if not v:
            continue

        avg = round(sum(v) / len(v), 2)

        print(f"{k:<10}: 期数={len(v)} 平均命中={avg}")

# =========================
# 投注比例
# =========================

def recommend_bet(main_wave, second_wave, size_pred, odd_pred):
    print("\n推荐投注方案:")

    print(f"{main_wave}: 450 元")
    print(f"{second_wave}: 150 元")
    print(f"{size_pred}: 200 元")
    print(f"{odd_pred}: 200 元")

# =========================
# 输出
# =========================

def show_prediction(draws, predictions):
    latest = draws[-1]

    print(f"\n最新开奖:")
    print(f"{latest['period']} | "
          f"{' '.join(f'{x:02d}' for x in latest['nums'])} "
          f"+ {latest['special']:02d}")

    next_period = str(int(latest["period"]) + 1)

    print(f"\n预测期号: {next_period}")

    for name, (nums, special) in predictions.items():
        print(f"{name:<10}: "
              f"{' '.join(f'{x:02d}' for x in nums)} "
              f"+ {special:02d}")

        print(f"特码属性: "
              f"{odd_even(special)}/"
              f"{size_text(special)} "
              f"{wave_color(special)} "
              f"{get_element(special)}")

    main_wave, second_wave = predict_wave(draws)

    print("\n特码波色预测:")
    print(f"主强: {main_wave} 次强: {second_wave}")

    size_pred, odd_pred = predict_size_odd(draws)

    print("\n大小单双预测:")
    print(f"大小: {size_pred}")
    print(f"单双: {odd_pred}")

    streak = max_hit_streak(draws)

    print("\n最大连中:")
    print(f"红波: {streak['红']}期")
    print(f"蓝波: {streak['蓝']}期")
    print(f"绿波: {streak['绿']}期")

    recommend_bet(
        main_wave,
        second_wave,
        size_pred,
        odd_pred
    )

# =========================
# 主同步
# =========================

def sync():
    conn = init_db()

    draws = fake_latest_draws()

    save_draws(conn, draws)

    draws = load_draws(conn)

    latest_period = str(int(draws[-1]["period"]) + 1)

    predictions = {
        "组合策略": balanced_strategy(draws),
        "热号策略": hot_strategy(draws),
        "冷号回补": cold_strategy(draws),
        "近期动量": momentum_strategy(draws),
        "集成投票": ensemble_strategy(draws),
        "规律挖掘": pattern_strategy(draws)
    }

    for name, (nums, special) in predictions.items():
        save_prediction(
            conn,
            latest_period,
            name,
            nums,
            special
        )

    print(f"已生成 {latest_period} 期预测")
    print(f"同步完成: {len(draws)} 条")

    show_prediction(draws, predictions)

    recent_backtest(conn)

# =========================
# main
# =========================

if __name__ == "__main__":
    sync()