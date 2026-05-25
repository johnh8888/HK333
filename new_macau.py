# -*- coding: utf-8 -*-
# 新澳门六合彩 - 全自动真实数据分析系统
# 功能：
# 1. 官方 API 实时抓取新澳门数据
# 2. 最近10期真实回测
# 3. 特码波色二中一预测
# 4. 大小单双预测
# 5. 最大连空统计
# 6. 六大策略预测
# 7. GitHub Actions 日志格式
#
# 运行:
# python new_macau.py sync

import json
import sqlite3
import urllib.request
import ssl
import re
import sys
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
    "金": {1, 2, 15, 16, 23, 24, 37, 38},
    "木": {5, 6, 13, 14, 27, 28, 35, 36, 49},
    "水": {3, 4, 11, 12, 19, 20, 33, 34, 41, 42},
    "火": {7, 8, 21, 22, 29, 30, 43, 44},
    "土": {9, 10, 17, 18, 25, 26, 31, 32, 39, 40, 45, 46, 47, 48},
}

# =========================================================
# 数据库
# =========================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS records(
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

def get_color(n):

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


def get_sum_attr(n):

    s = sum(map(int, str(n)))

    he = "合单" if s % 2 else "合双"

    dx = "大" if s >= 7 else "小"

    return f"{he}/{dx}"


def get_tail_attr(n):

    tail = n % 10

    return "尾大" if tail >= 5 else "尾小"


def special_attr(n):

    return (
        f"{get_odd_even(n)}/"
        f"{get_big_small(n)} "
        f"{get_sum_attr(n)} "
        f"{get_tail_attr(n)} "
        f"{get_color(n)} "
        f"{get_element(n)}"
    )


# =========================================================
# 官方 API 获取
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

            name = lottery.get("name", "")

            # 只抓新澳门
            if "新澳门" not in name:
                continue

            issue = str(
                lottery.get("expect", "")
            ).strip()

            open_code = lottery.get(
                "openCode",
                ""
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

            # 历史
            histories = lottery.get("history", [])

            for h in histories:

                hs = re.findall(
                    r"(\d+)\|(.*)",
                    h
                )

                if not hs:
                    continue

                h_issue = hs[0][0]

                h_nums = [
                    int(x)
                    for x in re.findall(
                        r"\d+",
                        hs[0][1]
                    )
                ]

                if len(h_nums) >= 7:

                    rows.append({
                        "issue": h_issue,
                        "nums": h_nums[:7]
                    })

        # 去重
        unique = {}

        for r in rows:
            unique[r["issue"]] = r

        rows = list(unique.values())

        rows.sort(
            key=lambda x: x["issue"],
            reverse=True
        )

        print(f"API获取成功: {url}")

        return rows[:120]

    except Exception as e:

        print(f"API获取失败: {e}")

        return []


# =========================================================
# 保存
# =========================================================

def save_records(rows):

    conn = sqlite3.connect(DB_FILE)

    new_count = 0

    for row in rows:

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
        VALUES(?,?,?,?,?,?,?,?)
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


# =========================================================
# 读取
# =========================================================

def load_records(limit=120):

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


# =========================================================
# 六大策略
# =========================================================

def hot_strategy(records):

    nums = []

    for r in records[:20]:
        nums.extend(r["nums"])

    top = [
        n for n, _ in Counter(nums).most_common(6)
    ]

    return top[:6], top[0]


def cold_strategy(records):

    nums = []

    for r in records[:40]:
        nums.extend(r["nums"])

    counter = Counter(nums)

    cold = sorted(
        range(1, 50),
        key=lambda x: counter[x]
    )

    return cold[:6], cold[0]


def momentum_strategy(records):

    nums = []

    for r in records[:10]:
        nums.extend(r["nums"])

    top = [
        n for n, _ in Counter(nums).most_common(6)
    ]

    return top[:6], top[0]


def pattern_strategy(records):

    last = records[0]["nums"]

    result = sorted(last)[:3] + sorted(last)[-3:]

    return result[:6], result[0]


def combo_strategy(records):

    hot, _ = hot_strategy(records)

    cold, _ = cold_strategy(records)

    result = list(
        dict.fromkeys(hot[:3] + cold[:3])
    )

    while len(result) < 6:
        result.append(len(result)+1)

    return result[:6], result[0]


def ensemble_strategy(records):

    nums = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        pattern_strategy,
        combo_strategy
    ]:

        ns, _ = fn(records)

        nums.extend(ns)

    top = [
        n for n, _ in Counter(nums).most_common(6)
    ]

    return top[:6], top[0]


# =========================================================
# 波色预测
# =========================================================

def predict_colors(records):

    specials = [
        r["special"]
        for r in records[:10]
    ]

    score = Counter()

    weight = 10

    for n in specials:

        c = get_color(n)

        score[c] += weight

        weight -= 1

    return score.most_common(2)


# =========================================================
# 大小单双
# =========================================================

def predict_bs(records):

    specials = [
        r["special"]
        for r in records[:10]
    ]

    bs = Counter()

    oe = Counter()

    for n in specials:

        bs[get_big_small(n)] += 1

        oe[get_odd_even(n)] += 1

    return (
        bs.most_common(1)[0][0],
        oe.most_common(1)[0][0]
    )


# =========================================================
# 历史命中
# =========================================================

def history_stats(records, fn):

    total_hit = 0

    special_hit = 0

    count = 0

    for i in range(10, len(records)-1):

        window = records[i-10:i]

        nums, sp = fn(window)

        real_nums = set(records[i]["nums"])

        real_sp = records[i]["special"]

        hit = len(
            real_nums & set(nums)
        )

        total_hit += hit

        if sp == real_sp:
            special_hit += 1

        count += 1

    if count == 0:
        return 0, 0, 0

    avg = round(total_hit / count, 1)

    rate = round(
        total_hit * 100 / (count * 6),
        1
    )

    sp_rate = round(
        special_hit * 100 / count,
        1
    )

    return avg, rate, sp_rate


# =========================================================
# 波色二中一回测
# =========================================================

def backtest(records):

    hit = 0

    total = 0

    streak = 0

    max_streak = 0

    for i in range(10, len(records)-1):

        window = records[i-10:i]

        pred = predict_colors(window)

        c1 = pred[0][0]

        c2 = pred[1][0]

        real = get_color(
            records[i]["special"]
        )

        total += 1

        if real in [c1, c2]:

            hit += 1

            streak = 0

        else:

            streak += 1

            max_streak = max(
                max_streak,
                streak
            )

    rate = round(
        hit * 100 / total,
        1
    ) if total else 0

    return hit, total, rate, max_streak


# =========================================================
# 输出
# =========================================================

def show():

    records = load_records()

    if len(records) < 20:
        print("数据不足")
        return

    latest = records[0]

    next_issue = str(
        int(latest["issue"]) + 1
    )

    print(
        f"数据同步完成: total={len(records)}"
    )

    print(
        f"最新开奖: {latest['issue']} | "
        + " ".join(
            f"{x:02d}"
            for x in latest["nums"]
        )
        + f" + {latest['special']:02d}"
    )

    print()

    print(f"预测期号: {next_issue}")

    strategies = [
        ("组合策略", combo_strategy),
        ("冷号回补", cold_strategy),
        ("集成投票", ensemble_strategy),
        ("热号策略", hot_strategy),
        ("近期动量", momentum_strategy),
        ("规律挖掘", pattern_strategy),
    ]

    for name, fn in strategies:

        nums, sp = fn(records)

        print(
            f"  {name:<14}: "
            + " ".join(
                f"{x:02d}"
                for x in nums
            )
            + f" + {sp:02d}"
        )

        print(
            f"         特码属性: "
            f"{special_attr(sp)}"
        )

    print()

    print("历史命中统计:")

    for name, fn in strategies:

        avg, rate, sp_rate = history_stats(
            records,
            fn
        )

        print(
            f"  {name:<14}: "
            f"期数=10, "
            f"平均命中={avg}个, "
            f"命中率={rate}%, "
            f"特别号命中率={sp_rate}%"
        )

    print()

    colors = predict_colors(records)

    print(
        "🎨 特码波色预测（加权频率，基于最近 10 期）："
    )

    print(
        f"   主强: {colors[0][0]} "
        f"(得分 {colors[0][1]})   "
        f"次强: {colors[1][0]} "
        f"(得分 {colors[1][1]})"
    )

    print()

    bs, oe = predict_bs(records)

    print(
        "📊 大小单双预测（最近10期真实数据）："
    )

    print(
        f"   大小预测: {bs}   "
        f"单双预测: {oe}"
    )

    print()

    hit, total, rate, max_streak = backtest(records)

    print(
        "📊 历史回测（最近 10 期，方法=weighted，窗口=10）："
    )

    print(
        f"   二中一命中率: {rate}%"
    )

    print(
        f"   最近10期命中: {hit}/{total}"
    )

    print(
        f"   最大连空: {max_streak}期"
    )


# =========================================================
# 同步
# =========================================================

def sync():

    rows = fetch_data()

    if not rows:

        print("未抓到真实开奖数据")

        return

    new_count = save_records(rows)

    records = load_records()

    print(
        f"数据同步完成: "
        f"total={len(records)}, "
        f"new={new_count}"
    )

    show()


# =========================================================
# 主程序
# =========================================================

def main():

    init_db()

    cmd = "sync"

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

    if cmd == "sync":
        sync()
    else:
        show()


if __name__ == "__main__":
    main()