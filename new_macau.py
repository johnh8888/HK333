import sqlite3
import requests
import random
import argparse
import re
from collections import Counter
from statistics import mean

DB_FILE = "new_macau.db"

RED_WAVE = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE_WAVE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN_WAVE = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

ELEMENTS = {
    "金": {1, 8, 15, 22, 29, 36, 43, 49},
    "木": {3, 10, 17, 24, 31, 38, 45},
    "水": {5, 12, 19, 26, 33, 40, 47},
    "火": {2, 9, 16, 23, 30, 37, 44},
    "土": {4, 11, 18, 25, 32, 39, 46}
}


def connect_db():
    return sqlite3.connect(DB_FILE)


def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        issue TEXT PRIMARY KEY,
        numbers TEXT,
        special TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        issue TEXT,
        strategy TEXT,
        numbers TEXT,
        special TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()


def fetch_real_data():

    urls = [
        "https://www.macaumarksix.com/api/macaujc.php",
        "https://www.macaumarksix.com/api/macaujc2.php",
        "https://www.macaumarksix.com/api/history.php"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*"
    }

    for url in urls:

        try:

            r = requests.get(url, headers=headers, timeout=20)

            if r.status_code != 200:
                continue

            text = r.text.strip()

            if not text:
                continue

            if text.startswith("{") or text.startswith("["):

                data = r.json()

                if isinstance(data, dict):
                    data = [data]

                draws = []

                for item in data:

                    issue = str(
                        item.get("expect")
                        or item.get("issue")
                        or item.get("period")
                        or item.get("qishu")
                        or ""
                    )

                    nums = (
                        item.get("openCode")
                        or item.get("opencode")
                        or item.get("code")
                        or item.get("numbers")
                        or ""
                    )

                    arr = re.findall(r"\d+", str(nums))

                    if len(arr) < 7:
                        continue

                    draws.append({
                        "issue": issue,
                        "numbers": ",".join(arr[:6]),
                        "special": arr[6]
                    })

                if draws:
                    return draws

            else:

                issues = re.findall(r"20\d{5,}", text)
                nums = re.findall(r">\s*(\d{2})\s*<", text)

                draws = []

                idx = 0

                for issue in issues:

                    if idx + 7 > len(nums):
                        break

                    group = nums[idx:idx + 7]

                    draws.append({
                        "issue": issue,
                        "numbers": ",".join(group[:6]),
                        "special": group[6]
                    })

                    idx += 7

                if draws:
                    return draws

        except Exception:
            continue

    return []


def save_draws(conn, draws):

    for d in draws:

        conn.execute("""
        INSERT OR REPLACE INTO draws
        (issue, numbers, special)
        VALUES (?, ?, ?)
        """, (
            d["issue"],
            d["numbers"],
            d["special"]
        ))

    conn.commit()


def load_draws(conn, limit=80):

    cur = conn.execute("""
    SELECT issue, numbers, special
    FROM draws
    ORDER BY issue DESC
    LIMIT ?
    """, (limit,))

    return cur.fetchall()


def get_wave(n):

    n = int(n)

    if n in RED_WAVE:
        return "红"

    if n in BLUE_WAVE:
        return "蓝"

    return "绿"


def get_element(n):

    n = int(n)

    for k, v in ELEMENTS.items():
        if n in v:
            return k

    return "土"


def get_size(n):
    return "大" if int(n) >= 25 else "小"


def get_odd_even(n):
    return "双" if int(n) % 2 == 0 else "单"


def get_tail(n):
    return "尾大" if int(n) % 10 >= 5 else "尾小"


def get_sum_type(n):

    s = sum(int(x) for x in str(n))

    return "合双" if s % 2 == 0 else "合单"


def number_attr(n):

    return (
        f"{get_odd_even(n)}/"
        f"{get_size(n)} "
        f"{get_sum_type(n)}/"
        f"{get_tail(n)} "
        f"{get_wave(n)} "
        f"{get_element(n)}"
    )


def random_pick(pool, count=6):

    pool = list(set(pool))

    if len(pool) < count:
        pool = list(range(1, 50))

    return random.sample(pool, count)


def hot_strategy(draws):

    nums = []

    for _, n, s in draws[:20]:
        nums.extend(map(int, n.split(",")))
        nums.append(int(s))

    hot = [x for x, _ in Counter(nums).most_common(15)]

    main = random_pick(hot)

    special = random.choice(hot)

    return main, special


def cold_strategy(draws):

    nums = []

    for _, n, s in draws[:30]:
        nums.extend(map(int, n.split(",")))
        nums.append(int(s))

    alln = set(range(1, 50))

    cold = list(alln - set(nums))

    if len(cold) < 10:
        cold = list(alln)

    main = random_pick(cold)

    special = random.choice(cold)

    return main, special


def momentum_strategy(draws):

    latest = draws[:10]

    nums = []

    for _, n, s in latest:
        nums.extend(map(int, n.split(",")))

    freq = [x for x, _ in Counter(nums).most_common(20)]

    main = random_pick(freq)

    special = random.choice(freq)

    return main, special


def balanced_strategy(draws):

    hot_main, _ = hot_strategy(draws)

    cold_main, _ = cold_strategy(draws)

    mix = hot_main[:3] + cold_main[:3]

    special = random.randint(1, 49)

    return mix, special


def mining_strategy(draws):

    nums = []

    for _, n, _ in draws[:15]:
        nums.extend(map(int, n.split(",")))

    even = [x for x in nums if x % 2 == 0]
    odd = [x for x in nums if x % 2 == 1]

    pick = random_pick(even, 3) + random_pick(odd, 3)

    special = random.randint(1, 49)

    return pick, special


def voting_strategy(draws):

    allnums = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy
    ]:

        m, _ = fn(draws)

        allnums.extend(m)

    top = [x for x, _ in Counter(allnums).most_common(12)]

    main = random_pick(top)

    special = random.choice(top)

    return main, special


def predict_wave(draws):

    waves = []

    for _, _, s in draws[:10]:
        waves.append(get_wave(s))

    cnt = Counter(waves)

    top = cnt.most_common(2)

    if len(top) == 1:
        return top[0][0], top[0][0]

    return top[0][0], top[1][0]


def predict_bs(draws):

    sizes = []
    odds = []

    for _, _, s in draws[:10]:

        sizes.append(get_size(s))
        odds.append(get_odd_even(s))

    size = Counter(sizes).most_common(1)[0][0]
    odd = Counter(odds).most_common(1)[0][0]

    return size, odd


def calc_miss(draws):

    result = {}

    for wave in ["红", "蓝", "绿"]:

        miss = 0

        for _, _, s in draws[:10]:

            if get_wave(s) != wave:
                miss += 1
            else:
                break

        result[wave] = miss

    return result


def save_prediction(conn, issue, strategy, nums, special):

    conn.execute("""
    INSERT INTO predictions
    (issue, strategy, numbers, special)
    VALUES (?, ?, ?, ?)
    """, (
        issue,
        strategy,
        ",".join(f"{x:02d}" for x in nums),
        f"{special:02d}"
    ))

    conn.commit()


def backtest(conn):

    cur = conn.execute("""
    SELECT issue, strategy, numbers, special
    FROM predictions
    ORDER BY created_at DESC
    LIMIT 60
    """)

    rows = cur.fetchall()

    stats = {}

    for issue, strategy, nums, sp in rows:

        d = conn.execute("""
        SELECT numbers, special
        FROM draws
        WHERE issue=?
        """, (issue,)).fetchone()

        if not d:
            continue

        real_nums = set(map(int, d[0].split(",")))
        real_special = int(d[1])

        pred = set(map(int, nums.split(",")))

        hit = len(real_nums & pred)

        if int(sp) == real_special:
            hit += 1

        stats.setdefault(strategy, []).append(hit)

    print("\n最近10期历史命中统计:")

    for k, v in stats.items():

        recent = v[:10]

        avg = mean(recent) if recent else 0

        print(f"{k:<10}: 期数={len(recent)} 平均命中={avg:.2f}")


def print_strategy(name, nums, sp):

    text = " ".join(f"{x:02d}" for x in nums)

    print(f"{name:<10}: {text} + {sp:02d}")

    print(f"特码属性: {number_attr(sp)}")


def recommend_bet(main_wave, second_wave, size, odd):

    print("\n推荐投注方案:")

    plan = {
        main_wave: 450,
        second_wave: 150,
        size: 200,
        odd: 200
    }

    for k, v in plan.items():
        print(f"{k}: {v} 元")

    print("\n赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")


def sync():

    conn = connect_db()

    init_db(conn)

    draws = fetch_real_data()

    if not draws:
        print("未抓到真实开奖数据")
        return

    save_draws(conn, draws)

    print(f"同步完成: {len(draws)} 条")

    history = load_draws(conn, 80)

    latest = history[0]

    issue = str(int(latest[0]) + 1)

    print("\n最新开奖:")
    print(f"{latest[0]} | {latest[1].replace(',', ' ')} + {latest[2]}")

    print(f"\n预测期号: {issue}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": voting_strategy,
        "规律挖掘": mining_strategy
    }

    for name, fn in strategies.items():

        nums, sp = fn(history)

        print_strategy(name, nums, sp)

        save_prediction(conn, issue, name, nums, sp)

    mw, sw = predict_wave(history)

    print("\n特码波色预测:")
    print(f"主强: {mw} 次强: {sw}")

    size, odd = predict_bs(history)

    print("\n大小单双预测:")
    print(f"大小: {size}")
    print(f"单双: {odd}")

    miss = calc_miss(history)

    print("\n最大连空:")

    for k, v in miss.items():
        print(f"{k}波: {v}期")

    recommend_bet(mw, sw, size, odd)

    backtest(conn)

    conn.close()


def show():

    conn = connect_db()

    rows = load_draws(conn, 20)

    for r in rows:
        print(r)

    conn.close()


def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync")

    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()

    elif args.cmd == "show":
        show()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()