#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sqlite3
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

# 主数据源（真实新澳门六合彩）
API_URLS = [
    "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "https://marksix6.net/index.php?api=1",
]

ALL_NUMBERS = list(range(1, 50))

# =========================
# 真正六合彩波色映射
# =========================
RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40, 45, 46
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
# 数据结构
# =========================
@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


# =========================
# 工具
# =========================
def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_color(num: int) -> str:
    if num in RED_WAVE:
        return "红"
    if num in BLUE_WAVE:
        return "蓝"
    return "绿"


def special_attributes(num: int):

    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    tail = num % 10

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

    return {
        "单双": odd_even,
        "大小": big_small,
        "色波": get_color(num),
        "五行": element,
    }


# =========================
# 数据库
# =========================
def connect_db(db_path):

    conn = sqlite3.connect(db_path)

    conn.row_factory = sqlite3.Row

    return conn


def init_db(conn):

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER,
        created_at TEXT
    );
    """)

    conn.commit()


# =========================
# 真实数据解析
# =========================
def parse_api3(payload):

    records = []

    data = payload.get("data") or payload.get("result") or []

    for item in data:

        try:

            issue = str(
                item.get("expect")
                or item.get("issue")
                or item.get("period")
                or item.get("qishu")
            )

            opencode = (
                item.get("opencode")
                or item.get("openCode")
                or item.get("number")
                or ""
            )

            if not opencode:
                continue

            nums = []

            for x in opencode.replace("+", ",").split(","):
                x = x.strip()
                if x.isdigit():
                    nums.append(int(x))

            if len(nums) != 7:
                continue

            draw_date = str(
                item.get("opentime")
                or item.get("openTime")
                or item.get("date")
                or datetime.now().strftime("%Y-%m-%d")
            )[:10]

            records.append(
                DrawRecord(
                    issue_no=issue,
                    draw_date=draw_date,
                    numbers=nums[:6],
                    special_number=nums[6],
                )
            )

        except:
            pass

    return records


def parse_marksix6(payload):

    records = []

    lottery_data = payload.get("lottery_data", [])

    target = None

    for x in lottery_data:

        name = str(x.get("name", ""))

        if "澳门" in name:
            target = x
            break

    if not target:
        return []

    history = target.get("history", [])

    for row in history:

        try:

            if "期：" not in row:
                continue

            issue, nums_str = row.split("期：")

            nums = [int(x) for x in nums_str.split(",")]

            if len(nums) != 7:
                continue

            records.append(
                DrawRecord(
                    issue_no=issue.strip(),
                    draw_date=datetime.now().strftime("%Y-%m-%d"),
                    numbers=nums[:6],
                    special_number=nums[6],
                )
            )

        except:
            pass

    return records


# =========================
# 获取真实数据
# =========================
def fetch_real_records():

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for url in API_URLS:

        try:

            req = Request(url, headers=headers)

            with urlopen(req, timeout=20) as resp:

                text = resp.read().decode("utf-8", errors="ignore")

            payload = json.loads(text)

            if "api3.marksix6.net" in url:

                records = parse_api3(payload)

            else:

                records = parse_marksix6(payload)

            if records:

                return records, url

        except Exception as e:

            print(f"数据源失败: {url}")
            print(e)

    return [], None


# =========================
# 入库
# =========================
def upsert_draw(conn, r: DrawRecord):

    exists = conn.execute(
        "SELECT 1 FROM draws WHERE issue_no=?",
        (r.issue_no,)
    ).fetchone()

    if exists:

        return

    conn.execute(
        """
        INSERT INTO draws VALUES(?,?,?,?,?)
        """,
        (
            r.issue_no,
            r.draw_date,
            json.dumps(r.numbers, ensure_ascii=False),
            r.special_number,
            utc_now(),
        )
    )


# =========================
# 预测
# =========================
def get_draws(conn):

    rows = conn.execute(
        """
        SELECT * FROM draws
        ORDER BY issue_no DESC
        LIMIT 200
        """
    ).fetchall()

    result = []

    for r in rows:

        result.append({
            "issue": r["issue_no"],
            "nums": json.loads(r["numbers_json"]),
            "special": r["special_number"],
        })

    return result


def hot_strategy(rows):

    freq = Counter()

    for r in rows[:50]:

        for n in r["nums"]:
            freq[n] += 1

    hot = [n for n, _ in freq.most_common(6)]

    if len(hot) < 6:

        remain = [n for n in ALL_NUMBERS if n not in hot]

        hot.extend(remain[:6 - len(hot)])

    special_pool = [n for n in hot if n not in hot[:6]]

    if not special_pool:
        special_pool = ALL_NUMBERS

    special = random.choice(special_pool)

    return hot[:6], special


def cold_strategy(rows):

    freq = Counter()

    for r in rows[:80]:

        for n in r["nums"]:
            freq[n] += 1

    cold = sorted(freq.items(), key=lambda x: x[1])

    nums = [n for n, _ in cold[:6]]

    while len(nums) < 6:

        for n in ALL_NUMBERS:

            if n not in nums:
                nums.append(n)

            if len(nums) >= 6:
                break

    special = nums[0]

    return nums, special


def momentum_strategy(rows):

    score = defaultdict(float)

    for idx, r in enumerate(rows[:30]):

        weight = 30 - idx

        for n in r["nums"]:
            score[n] += weight

    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)

    nums = [n for n, _ in ranked[:6]]

    while len(nums) < 6:

        for n in ALL_NUMBERS:

            if n not in nums:
                nums.append(n)

            if len(nums) >= 6:
                break

    special = nums[-1]

    return nums, special


def balanced_strategy(rows):

    h, _ = hot_strategy(rows)

    c, _ = cold_strategy(rows)

    m, _ = momentum_strategy(rows)

    mix = []

    for x in h[:2] + c[:2] + m[:2]:

        if x not in mix:
            mix.append(x)

    while len(mix) < 6:

        n = random.randint(1, 49)

        if n not in mix:
            mix.append(n)

    special = random.randint(1, 49)

    return mix[:6], special


def ensemble_strategy(rows):

    score = Counter()

    for func in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy,
    ]:

        nums, _ = func(rows)

        for n in nums:
            score[n] += 1

    ranked = [n for n, _ in score.most_common(6)]

    while len(ranked) < 6:

        for n in ALL_NUMBERS:

            if n not in ranked:
                ranked.append(n)

            if len(ranked) >= 6:
                break

    special = ranked[-1]

    return ranked[:6], special


# =========================
# 波色预测
# =========================
def predict_color(rows):

    specials = [r["special"] for r in rows[:20]]

    colors = [get_color(x) for x in specials]

    freq = Counter(colors)

    ranked = freq.most_common()

    if not ranked:
        return "蓝", "绿"

    main = ranked[0][0]

    second = ranked[1][0] if len(ranked) > 1 else "绿"

    return main, second


# =========================
# 最大连空
# =========================
def calc_max_miss(rows):

    specials = [r["special"] for r in rows]

    result = {}

    for color in ["红", "蓝", "绿"]:

        miss = 0
        max_miss = 0

        for n in specials:

            if get_color(n) == color:

                miss = 0

            else:

                miss += 1

                max_miss = max(max_miss, miss)

        result[color] = max_miss

    return result


# =========================
# 最近10期回测
# =========================
def recent_hit(rows, strategy_func):

    if len(rows) < 15:
        return 0

    hits = []

    for i in range(10):

        past = rows[i + 1:]

        current = rows[i]

        nums, _ = strategy_func(past)

        hit = len(set(nums) & set(current["nums"]))

        hits.append(hit)

    return round(sum(hits) / len(hits), 2)


# =========================
# 展示
# =========================
def show_dashboard(conn):

    rows = get_draws(conn)

    if not rows:

        print("数据库暂无真实数据")

        return

    latest = rows[0]

    nums_str = " ".join(f"{x:02d}" for x in latest["nums"])

    print("\n最新开奖:")
    print(f"{latest['issue']} | {nums_str} + {latest['special']:02d}")

    next_issue = str(int(latest["issue"]) + 1)

    print(f"\n预测期号: {next_issue}")

    strategies = [
        ("组合策略", balanced_strategy),
        ("热号策略", hot_strategy),
        ("冷号回补", cold_strategy),
        ("近期动量", momentum_strategy),
        ("集成投票", ensemble_strategy),
    ]

    for name, func in strategies:

        nums, special = func(rows)

        attrs = special_attributes(special)

        print(f"{name:<12}: {' '.join(f'{x:02d}' for x in nums)} + {special:02d}")

        print(
            f"特码属性: "
            f"{attrs['单双']}/"
            f"{attrs['大小']} "
            f"{attrs['色波']} "
            f"{attrs['五行']}"
        )

    # 波色预测
    main_color, second_color = predict_color(rows)

    print("\n特码波色预测:")
    print(f"主强: {main_color} 次强: {second_color}")

    # 大小单双
    last_special = latest["special"]

    print("\n大小单双预测:")
    print(f"大小: {'大' if last_special >= 25 else '小'}")
    print(f"单双: {'单' if last_special % 2 else '双'}")

    # 最大连空
    miss = calc_max_miss(rows)

    print("\n真实最大连空:")

    for k, v in miss.items():
        print(f"{k}波: {v}期")

    # 投注建议
    print("\n推荐投注方案:")

    print(f"{main_color}: 300 元")

    if last_special >= 25:
        print("大: 200 元")
    else:
        print("小: 200 元")

    if last_special % 2:
        print("单: 200 元")
    else:
        print("双: 200 元")

    # 赔率
    print("\n赔率参考:")

    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")

    # 最近10期回测
    print("\n最近10期历史命中统计:")

    for name, func in strategies:

        avg = recent_hit(rows, func)

        print(f"{name:<12}: 平均命中 {avg} 个")


# =========================
# 同步
# =========================
def sync(conn):

    records, source = fetch_real_records()

    if not records:

        print("未抓到真实开奖数据")

        return

    for r in records:

        upsert_draw(conn, r)

    conn.commit()

    print(f"同步完成: {len(records)} 条")

    print(f"真实数据源: {source}")

    show_dashboard(conn)


# =========================
# main
# =========================
def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "cmd",
        choices=["sync", "show"]
    )

    args = parser.parse_args()

    conn = connect_db(DB_PATH_DEFAULT)

    init_db(conn)

    if args.cmd == "sync":

        sync(conn)

    else:

        show_dashboard(conn)

    conn.close()


if __name__ == "__main__":
    main()