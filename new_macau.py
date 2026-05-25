import requests
import sqlite3
import random
from collections import Counter

DB = "new_macau.db"

API = "https://api3.marksix6.net/lottery_api.php?type=newMacau"

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

ELEMENTS = [
    "金", "木", "水", "火", "土"
]


def wave(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"


def element(n):
    return ELEMENTS[n % 5]


def big_small(n):
    return "大" if n >= 25 else "小"


def odd_even(n):
    return "双" if n % 2 == 0 else "单"


def init_db():
    conn = sqlite3.connect(DB)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        expect TEXT PRIMARY KEY,
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


def fetch_latest():
    try:
        resp = requests.get(
            API,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            },
            timeout=20
        )

        data = resp.json()

        rows = []

        if isinstance(data, list):

            for item in data:

                expect = str(item.get("expect", "")).strip()

                open_code = str(item.get("openCode", "")).strip()

                nums = [
                    int(x)
                    for x in open_code.replace("+", ",").split(",")
                    if x.strip().isdigit()
                ]

                if len(nums) != 7:
                    continue

                rows.append({
                    "expect": expect,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

        elif isinstance(data, dict):

            for item in data.get("lottery_data", []):

                if "新澳门" not in item.get("name", ""):
                    continue

                expect = str(item.get("expect", "")).strip()

                open_code = str(item.get("openCode", "")).strip()

                nums = [
                    int(x)
                    for x in open_code.replace("+", ",").split(",")
                    if x.strip().isdigit()
                ]

                if len(nums) != 7:
                    continue

                rows.append({
                    "expect": expect,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

        return rows

    except Exception as e:
        print("抓取失败:", e)
        return []


def save_draws(conn, draws):

    for d in draws:

        nums = d["numbers"]

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d["expect"],
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            d["special"]
        ))

    conn.commit()


def get_recent(conn, limit=80):

    rows = conn.execute(f"""
    SELECT *
    FROM draws
    ORDER BY expect DESC
    LIMIT {limit}
    """).fetchall()

    return rows


def hot_strategy(rows):

    nums = []

    for r in rows[:20]:
        nums.extend(r[1:7])

    hot = [x for x, _ in Counter(nums).most_common(6)]

    special = random.choice(hot)

    return hot[:6], special


def cold_strategy(rows):

    nums = []

    for r in rows[:30]:
        nums.extend(r[1:7])

    freq = Counter(nums)

    all_nums = list(range(1, 50))

    cold = sorted(all_nums, key=lambda x: freq[x])[:6]

    special = random.choice(cold)

    return cold, special


def momentum_strategy(rows):

    nums = []

    for r in rows[:10]:
        nums.extend(r[1:7])

    picks = [x for x, _ in Counter(nums).most_common(6)]

    special = random.randint(1, 49)

    return picks, special


def balanced_strategy(rows):

    nums = []

    for r in rows[:20]:
        nums.extend(r[1:7])

    freq = Counter(nums)

    middle = sorted(freq.items(), key=lambda x: x[1])

    picks = [x[0] for x in middle[10:16]]

    special = random.randint(1, 49)

    return picks, special


def mining_strategy(rows):

    nums = []

    for r in rows[:15]:
        nums.extend(r[1:7])

    freq = Counter(nums)

    picks = [x for x, _ in freq.most_common(3)]

    while len(picks) < 6:
        n = random.randint(1, 49)

        if n not in picks:
            picks.append(n)

    special = random.randint(1, 49)

    return picks, special


def voting_strategy(rows):

    strategies = [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy
    ]

    counter = Counter()

    for s in strategies:
        picks, _ = s(rows)

        counter.update(picks)

    final = [x for x, _ in counter.most_common(6)]

    special = random.randint(1, 49)

    return final, special


def wave_predict(rows):

    waves = []

    for r in rows[:10]:
        waves.append(wave(r[7]))

    freq = Counter(waves)

    ordered = [x for x, _ in freq.most_common()]

    unique = []

    for w in ordered:
        if w not in unique:
            unique.append(w)

    main = unique[0]

    second = unique[1] if len(unique) > 1 else unique[0]

    return main, second


def big_small_predict(rows):

    values = [big_small(r[7]) for r in rows[:10]]

    return Counter(values).most_common(1)[0][0]


def odd_even_predict(rows):

    values = [odd_even(r[7]) for r in rows[:10]]

    return Counter(values).most_common(1)[0][0]


def max_miss_wave(rows):

    result = {}

    for w in ["红", "蓝", "绿"]:

        miss = 0

        max_miss = 0

        for r in rows[:10]:

            actual = wave(r[7])

            if actual != w:
                miss += 1
            else:
                max_miss = max(max_miss, miss)
                miss = 0

        max_miss = max(max_miss, miss)

        result[w] = max_miss

    return result


def print_strategy(name, picks, special):

    nums = " ".join(f"{x:02d}" for x in picks)

    print(f"{name:<10}: {nums} + {special:02d}")

    print(
        f"特码属性: "
        f"{odd_even(special)}/"
        f"{big_small(special)} "
        f"{wave(special)} "
        f"{element(special)}"
    )


def backtest(rows):

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": voting_strategy,
        "规律挖掘": mining_strategy
    }

    print("\n最近10期历史命中统计:")

    for name, func in strategies.items():

        total_hit = 0
        count = 0

        for i in range(1, min(10, len(rows) - 1)):

            sample = rows[i:]

            picks, _ = func(sample)

            actual = set(rows[i - 1][1:7])

            hit = len(set(picks) & actual)

            total_hit += hit

            count += 1

        avg = total_hit / count if count else 0

        print(f"{name:<10}: 期数={count} 平均命中={avg:.2f}")


def recommend_bet(main_wave, second_wave, bs, oe):

    print("\n推荐投注方案:")

    if main_wave:
        print(f"{main_wave}: 450 元")

    if second_wave and second_wave != main_wave:
        print(f"{second_wave}: 150 元")

    print(f"{bs}: 200 元")
    print(f"{oe}: 200 元")

    print("\n赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")


def sync():

    conn = init_db()

    draws = fetch_latest()

    if not draws:
        print("未抓到真实开奖数据")
        return

    save_draws(conn, draws)

    rows = get_recent(conn)

    latest = rows[0]

    next_expect = str(int(latest[0]) + 1)

    print(f"同步完成: {len(rows)} 条")

    print("\n最新开奖:")

    nums = " ".join(f"{x:02d}" for x in latest[1:7])

    print(f"{latest[0]} | {nums} + {latest[7]:02d}")

    print(f"\n预测期号: {next_expect}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": voting_strategy,
        "规律挖掘": mining_strategy
    }

    for name, func in strategies.items():

        picks, special = func(rows)

        print_strategy(name, picks, special)

    main_wave, second_wave = wave_predict(rows)

    print("\n特码波色预测:")
    print(f"主强: {main_wave} 次强: {second_wave}")

    bs = big_small_predict(rows)

    oe = odd_even_predict(rows)

    print("\n大小单双预测:")
    print(f"大小: {bs}")
    print(f"单双: {oe}")

    print("\n最大连空:")

    misses = max_miss_wave(rows)

    for k, v in misses.items():
        print(f"{k}波: {v}期")

    recommend_bet(main_wave, second_wave, bs, oe)

    backtest(rows)


if __name__ == "__main__":
    sync()