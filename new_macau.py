#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import sqlite3

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen


# =========================================================
# 基础配置
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

OFFICIAL_URL_DEFAULT = (
    "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
)

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "ensemble_v2": "集成投票",
    "pattern_mined_v1": "规律挖掘",
}

STRATEGY_IDS = [
    "balanced_v1",
    "hot_v1",
    "cold_rebound_v1",
    "momentum_v1",
    "ensemble_v2",
    "pattern_mined_v1",
]


# =========================================================
# 数据结构
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


# =========================================================
# 工具
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def connect_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER,
        source TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    conn.commit()


def get_color(num):

    if 1 <= num <= 16:
        return "红"

    elif 17 <= num <= 32:
        return "蓝"

    return "绿"


def special_attributes(num):

    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    color = get_color(num)

    tail = num % 10

    if tail in (1, 6):
        element = "水"

    elif tail in (2, 7):
        element = "火"

    elif tail in (3, 8):
        element = "木"

    elif tail in (4, 9):
        element = "金"

    else:
        element = "土"

    return {
        "单双": odd_even,
        "大小": big_small,
        "色波": color,
        "五行": element,
    }


# =========================================================
# 数据获取
# =========================================================

def _parse_official_json(payload):

    records = []

    if not isinstance(payload, list):
        return records

    for item in payload:

        try:

            issue_no = str(
                item.get("drawNo")
                or item.get("issueNo")
                or ""
            )

            draw_date = str(
                item.get("drawDate", "")
            )[:10]

            nums = []

            for i in range(1, 7):

                v = item.get(f"no{i}")

                if v is not None:
                    nums.append(int(v))

            special = item.get("specialNumber")

            if special is None:
                special = item.get("no7")

            special = int(special)

            if len(nums) != 6:
                continue

            records.append(
                DrawRecord(
                    issue_no,
                    draw_date,
                    nums,
                    special
                )
            )

        except:
            continue

    return records


def fetch_online_records():

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    # =====================================================
    # 1 官方 HKJC
    # =====================================================

    try:

        req = Request(
            OFFICIAL_URL_DEFAULT,
            headers=headers
        )

        with urlopen(req, timeout=20) as resp:

            payload = json.loads(
                resp.read().decode("utf-8-sig")
            )

        records = _parse_official_json(payload)

        if records:

            print("使用官方数据源")

            return records

    except Exception as e:

        print(f"官方源失败: {e}")

    # =====================================================
    # 2 marksix6 历史接口
    # =====================================================

    history_url = "https://marksix6.net/index.php?api=1"

    try:

        req = Request(history_url, headers=headers)

        with urlopen(req, timeout=20) as resp:

            text = resp.read().decode(
                "utf-8",
                errors="ignore"
            )

        payload = json.loads(text)

        records = []

        if isinstance(payload, dict):

            for key, value in payload.items():

                if not isinstance(value, list):
                    continue

                for item in value:

                    try:

                        issue = str(
                            item.get("expect")
                            or item.get("issue")
                            or item.get("qihao")
                            or ""
                        )

                        opencode = (
                            item.get("opencode")
                            or item.get("openCode")
                            or item.get("number")
                            or ""
                        )

                        if not issue:
                            continue

                        if not opencode:
                            continue

                        nums = [
                            int(x)
                            for x in opencode
                            .replace("+", ",")
                            .split(",")
                            if x.strip().isdigit()
                        ]

                        if len(nums) < 7:
                            continue

                        draw_date = str(
                            item.get("opentime")
                            or item.get("openTime")
                            or datetime.now().strftime("%Y-%m-%d")
                        )[:10]

                        records.append(
                            DrawRecord(
                                issue,
                                draw_date,
                                nums[:6],
                                nums[6]
                            )
                        )

                    except:
                        continue

        if records:

            print(f"使用 marksix6 历史源，共 {len(records)} 条")

            return records

    except Exception as e:

        print(f"marksix6 历史源失败: {e}")

    # =====================================================
    # 3 api3 最新开奖
    # =====================================================

    latest_url = (
        "https://api3.marksix6.net/"
        "lottery_api.php?type=newMacau"
    )

    try:

        req = Request(latest_url, headers=headers)

        with urlopen(req, timeout=20) as resp:

            text = resp.read().decode(
                "utf-8",
                errors="ignore"
            )

        payload = json.loads(text)

        issue = str(payload.get("expect", ""))

        opencode = payload.get("opencode", "")

        nums = [
            int(x)
            for x in opencode.split(",")
            if x.strip().isdigit()
        ]

        if issue and len(nums) >= 7:

            print("使用 api3 最新开奖源")

            return [
                DrawRecord(
                    issue,
                    datetime.now().strftime("%Y-%m-%d"),
                    nums[:6],
                    nums[6]
                )
            ]

    except Exception as e:

        print(f"api3 最新源失败: {e}")

    raise RuntimeError("所有在线数据源均失败")


# =========================================================
# 数据库存储
# =========================================================

def upsert_draw(conn, record, source):

    now = utc_now()

    exists = conn.execute(
        "SELECT 1 FROM draws WHERE issue_no=?",
        (record.issue_no,)
    ).fetchone()

    if exists:

        conn.execute("""
        UPDATE draws
        SET draw_date=?,
            numbers_json=?,
            special_number=?,
            source=?,
            updated_at=?
        WHERE issue_no=?
        """, (
            record.draw_date,
            json.dumps(record.numbers),
            record.special_number,
            source,
            now,
            record.issue_no
        ))

        return "updated"

    else:

        conn.execute("""
        INSERT INTO draws
        VALUES (?,?,?,?,?,?,?)
        """, (
            record.issue_no,
            record.draw_date,
            json.dumps(record.numbers),
            record.special_number,
            source,
            now,
            now
        ))

        return "inserted"


def sync_records(conn, records, source):

    ins = 0
    upd = 0

    for r in records:

        res = upsert_draw(conn, r, source)

        if res == "inserted":
            ins += 1
        else:
            upd += 1

    conn.commit()

    return len(records), ins, upd


# =========================================================
# 策略
# =========================================================

def load_draws(conn, limit=200):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT ?
    """, (limit,)).fetchall()

    draws = []

    for r in rows:

        nums = json.loads(r["numbers_json"])

        draws.append(nums + [r["special_number"]])

    return draws


def freq_scores(draws):

    counter = Counter()

    for d in draws:

        for n in d:
            counter[n] += 1

    return counter


def omission_scores(draws):

    miss = {}

    for n in ALL_NUMBERS:

        miss[n] = 999

        for i, d in enumerate(draws):

            if n in d:

                miss[n] = i

                break

    return miss


def momentum_scores(draws):

    score = defaultdict(float)

    for idx, d in enumerate(draws):

        weight = 1 / (idx + 1)

        for n in d:
            score[n] += weight

    return score


def pick_top(counter, count=6):

    arr = sorted(
        counter.items(),
        key=lambda x: x[1],
        reverse=True
    )

    nums = []

    for n, _ in arr:

        if n not in nums:
            nums.append(n)

        if len(nums) >= count:
            break

    while len(nums) < count:

        x = random.choice(ALL_NUMBERS)

        if x not in nums:
            nums.append(x)

    return nums


def hot_strategy(draws):

    freq = freq_scores(draws)

    nums = pick_top(freq)

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


def cold_strategy(draws):

    omit = omission_scores(draws)

    arr = sorted(
        omit.items(),
        key=lambda x: x[1],
        reverse=True
    )

    nums = [n for n, _ in arr[:6]]

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


def momentum_strategy(draws):

    mom = momentum_scores(draws)

    nums = pick_top(mom)

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


def balanced_strategy(draws):

    hot, _ = hot_strategy(draws)

    cold, _ = cold_strategy(draws)

    nums = list(dict.fromkeys(
        hot[:3] + cold[:3]
    ))

    while len(nums) < 6:

        x = random.choice(ALL_NUMBERS)

        if x not in nums:
            nums.append(x)

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


def ensemble_strategy(draws):

    pools = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        balanced_strategy,
    ]:

        nums, _ = fn(draws)

        pools.extend(nums)

    counter = Counter(pools)

    nums = pick_top(counter)

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


def pattern_strategy(draws):

    counter = Counter()

    for d in draws[:30]:

        for n in d:

            zone = (n - 1) // 10

            counter[n] += (5 - zone)

    nums = pick_top(counter)

    remain = [n for n in ALL_NUMBERS if n not in nums]

    if not remain:
        remain = ALL_NUMBERS.copy()

    special = random.choice(remain)

    return nums, special


# =========================================================
# 展示
# =========================================================

def print_prediction(name, nums, special):

    attrs = special_attributes(special)

    print(
        f"{name:<10}: "
        f"{' '.join(f'{n:02d}' for n in nums)} "
        f"+ {special:02d}"
    )

    print(
        f"特码属性: "
        f"{attrs['单双']}/"
        f"{attrs['大小']} "
        f"{attrs['色波']} "
        f"{attrs['五行']}"
    )


def show_dashboard(conn):

    latest = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 1
    """).fetchone()

    if not latest:

        print("数据库没有数据")

        return

    nums = json.loads(latest["numbers_json"])

    print("\n最新开奖:")

    print(
        f"{latest['issue_no']} | "
        f"{' '.join(f'{n:02d}' for n in nums)} "
        f"+ {latest['special_number']:02d}"
    )

    draws = load_draws(conn)

    if len(draws) < 5:

        print("\n历史数据不足")

        return

    next_issue = str(
        int(latest["issue_no"]) + 1
    )

    print(f"\n预测期号: {next_issue}")

    strategies = [
        ("组合策略", balanced_strategy),
        ("热号策略", hot_strategy),
        ("冷号回补", cold_strategy),
        ("近期动量", momentum_strategy),
        ("集成投票", ensemble_strategy),
        ("规律挖掘", pattern_strategy),
    ]

    for label, fn in strategies:

        nums, special = fn(draws)

        print_prediction(label, nums, special)

    specials = [
        r["special_number"]
        for r in conn.execute("""
        SELECT special_number
        FROM draws
        ORDER BY issue_no DESC
        LIMIT 20
        """).fetchall()
    ]

    color_counter = Counter(
        get_color(x)
        for x in specials
    )

    print("\n特码波色预测:")

    for color, count in color_counter.items():

        print(f"{color}: {count} 次")


# =========================================================
# 命令
# =========================================================

def sync():

    conn = connect_db(DB_PATH_DEFAULT)

    try:

        init_db(conn)

        records = fetch_online_records()

        total, ins, upd = sync_records(
            conn,
            records,
            "online"
        )

        print(
            f"\n同步完成: "
            f"total={total} "
            f"new={ins} "
            f"updated={upd}"
        )

        show_dashboard(conn)

    finally:

        conn.close()


def show():

    conn = connect_db(DB_PATH_DEFAULT)

    try:

        show_dashboard(conn)

    finally:

        conn.close()


# =========================================================
# 主入口
# =========================================================

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