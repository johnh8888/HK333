#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen

# =========================================================
# CONFIG
# =========================================================

ROOT = Path(__file__).resolve().parent

DB_PATH = ROOT / "new_macau.db"

TARGET_LOTTERY_NAME = "新澳门彩"

API_URLS = [
    "https://marksix6.net/index.php?api=1"
]

ALL_NUMBERS = list(range(1, 50))

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

# =========================================================
# MODEL
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special: int

# =========================================================
# TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def today_str():
    return utc_now()[:10]

# =========================================================
# NORMALIZE
# =========================================================

def normalize(score_map: Dict[int, float]):

    vals = list(score_map.values())

    mn = min(vals)
    mx = max(vals)

    if mx == mn:
        return {k: 0.0 for k in score_map}

    return {
        k: (v - mn) / (mx - mn)
        for k, v in score_map.items()
    }

# =========================================================
# ATTR
# =========================================================

def get_wave(n: int):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

def get_size(n: int):
    return "大" if n >= 25 else "小"

def get_odd_even(n: int):
    return "单" if n % 2 else "双"

def get_tail_size(n: int):
    return "尾大" if n % 10 >= 5 else "尾小"

def get_sum_size(n: int):

    s = sum(map(int, str(n)))

    return "合大" if s >= 7 else "合小"

def get_sum_odd_even(n: int):

    s = sum(map(int, str(n)))

    return "合单" if s % 2 else "合双"

def special_text(n: int):

    return (
        f"{get_odd_even(n)}/"
        f"{get_size(n)} "
        f"{get_sum_odd_even(n)}/"
        f"{get_sum_size(n)} "
        f"{get_tail_size(n)} "
        f"{get_wave(n)}"
    )

# =========================================================
# DATABASE
# =========================================================

def connect_db():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn

def init_db(conn):

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS draws (

        issue_no TEXT PRIMARY KEY,

        draw_date TEXT NOT NULL,

        n1 INTEGER NOT NULL,
        n2 INTEGER NOT NULL,
        n3 INTEGER NOT NULL,
        n4 INTEGER NOT NULL,
        n5 INTEGER NOT NULL,
        n6 INTEGER NOT NULL,

        special INTEGER NOT NULL,

        source TEXT,
        created_at TEXT NOT NULL

    );

    CREATE TABLE IF NOT EXISTS analytics (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        created_at TEXT,
        avg_hit REAL,
        special_rate REAL,
        montecarlo REAL

    );

    """)

    conn.commit()

# =========================================================
# API
# =========================================================

def parse_code_string(code_str):

    nums = []

    for x in code_str.replace("，", ",").split(","):

        x = x.strip()

        if x.isdigit():
            nums.append(int(x))

    return nums

def parse_new_macau_records(payload):

    records = []

    lottery_data = payload.get("lottery_data", [])

    target = None

    for item in lottery_data:

        if item.get("name") == TARGET_LOTTERY_NAME:

            target = item
            break

    if not target:
        return records

    # 最新一期
    latest_issue = str(
        target.get("expect", "")
    ).strip()

    latest_code = str(
        target.get("openCode", "")
    ).strip()

    latest_nums = parse_code_string(latest_code)

    if latest_issue and len(latest_nums) >= 7:

        records.append(
            DrawRecord(
                issue_no=latest_issue,
                draw_date=str(
                    target.get("openTime", "")
                )[:10] or today_str(),
                numbers=latest_nums[:6],
                special=latest_nums[6]
            )
        )

    # 历史记录
    histories = target.get("history", [])

    for row in histories:

        if not isinstance(row, str):
            continue

        row = row.strip()

        try:

            parts = row.split("期", 1)

            issue_no = parts[0].strip()

            code_part = parts[1]

            if "：" in code_part:
                code_part = code_part.split("：", 1)[1]

            elif ":" in code_part:
                code_part = code_part.split(":", 1)[1]

            nums = []

            for x in code_part.replace(",", " ").split():

                x = x.strip()

                if x.isdigit():
                    nums.append(int(x))

            if len(nums) >= 7:

                records.append(
                    DrawRecord(
                        issue_no=issue_no,
                        draw_date=today_str(),
                        numbers=nums[:6],
                        special=nums[6]
                    )
                )

        except:
            continue

    # 去重
    uniq = {}

    for r in records:
        uniq[r.issue_no] = r

    result = list(uniq.values())

    result.sort(key=lambda x: x.issue_no)

    return result

def fetch_records():

    for url in API_URLS:

        try:

            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cache-Control": "no-cache"
                }
            )

            with urlopen(req, timeout=20) as resp:

                payload = json.loads(
                    resp.read().decode("utf-8")
                )

            rows = parse_new_macau_records(payload)

            if rows:

                print(
                    f"API获取成功: "
                    f"{url} "
                    f"| "
                    f"{TARGET_LOTTERY_NAME}"
                )

                return rows, url

        except Exception as e:

            print("API失败:", e)

    raise RuntimeError("全部API失败")

# =========================================================
# STORAGE
# =========================================================

def upsert_draw(conn, row, source):

    conn.execute("""

    INSERT OR REPLACE INTO draws (

        issue_no,
        draw_date,

        n1,
        n2,
        n3,
        n4,
        n5,
        n6,

        special,

        source,
        created_at

    ) VALUES (

        ?,
        ?,

        ?,
        ?,
        ?,
        ?,
        ?,
        ?,

        ?,

        ?,
        ?

    )

    """, (

        row.issue_no,
        row.draw_date,

        row.numbers[0],
        row.numbers[1],
        row.numbers[2],
        row.numbers[3],
        row.numbers[4],
        row.numbers[5],

        row.special,

        source,
        utc_now()

    ))

def sync_data(conn):

    rows, source = fetch_records()

    new_count = 0

    for row in rows:

        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (row.issue_no,)
        ).fetchone()

        if not exists:
            new_count += 1

        upsert_draw(conn, row, source)

    conn.commit()

    print(
        f"数据同步完成: "
        f"total={len(rows)}, "
        f"new={new_count}"
    )

def load_draws(conn):

    rows = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no ASC

    """).fetchall()

    result = []

    for r in rows:

        result.append(
            DrawRecord(
                issue_no=r["issue_no"],
                draw_date=r["draw_date"],
                numbers=[
                    r["n1"],
                    r["n2"],
                    r["n3"],
                    r["n4"],
                    r["n5"],
                    r["n6"]
                ],
                special=r["special"]
            )
        )

    return result

# =========================================================
# SCORE ENGINE
# =========================================================

def frequency_score(draws, window=80):

    recent = draws[-window:]

    freq = defaultdict(float)

    total = max(len(recent), 1)

    for idx, draw in enumerate(recent):

        weight = (idx + 1) / total

        for n in draw.numbers:
            freq[n] += weight

    for n in ALL_NUMBERS:
        freq.setdefault(n, 0.0)

    return normalize(freq)

def omission_score(draws):

    latest_seen = {
        n: None
        for n in ALL_NUMBERS
    }

    reversed_draws = list(reversed(draws))

    for idx, draw in enumerate(reversed_draws):

        for n in draw.numbers:

            if latest_seen[n] is None:
                latest_seen[n] = idx

    mx = max(
        v if v is not None else 0
        for v in latest_seen.values()
    )

    result = {}

    for n in ALL_NUMBERS:

        gap = latest_seen[n]

        if gap is None:
            gap = mx + 1

        result[n] = float(gap)

    return normalize(result)

def momentum_score(draws):

    recent = draws[-20:]

    older = draws[-60:-20]

    r = Counter()
    o = Counter()

    for draw in recent:
        for n in draw.numbers:
            r[n] += 1

    for draw in older:
        for n in draw.numbers:
            o[n] += 1

    result = {}

    for n in ALL_NUMBERS:
        result[n] = r[n] - (o[n] / 2)

    return normalize(result)

def pair_affinity_score(draws):

    pair_count = defaultdict(int)

    for draw in draws[-120:]:

        nums = sorted(draw.numbers)

        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):

                pair_count[
                    (nums[i], nums[j])
                ] += 1

    social = defaultdict(float)

    for (a, b), c in pair_count.items():

        social[a] += c
        social[b] += c

    for n in ALL_NUMBERS:
        social.setdefault(n, 0.0)

    return normalize(social)

# =========================================================
# STRATEGY
# =========================================================

class EnsembleStrategy:

    name = "ensemble_v6"

    def predict(self, draws):

        freq = frequency_score(draws)
        omit = omission_score(draws)
        momentum = momentum_score(draws)
        pair = pair_affinity_score(draws)

        final = {}

        for n in ALL_NUMBERS:

            final[n] = (
                freq[n] * 0.35 +
                omit[n] * 0.30 +
                momentum[n] * 0.25 +
                pair[n] * 0.10
            )

        ranked = sorted(
            final.items(),
            key=lambda x: x[1],
            reverse=True
        )

        main6 = ranked[:6]

        special = ranked[6]

        return {
            "numbers": [n for n, _ in main6],
            "special": special[0],
            "scores": final
        }

# =========================================================
# MONTECARLO
# =========================================================

def montecarlo_baseline(draws, simulations=1000):

    scores = []

    for draw in draws[-20:]:

        actual = set(draw.numbers)

        total = 0

        for _ in range(simulations):

            nums = random.sample(
                ALL_NUMBERS,
                6
            )

            hit = len(
                set(nums) & actual
            )

            total += hit

        scores.append(
            total / simulations
        )

    return round(
        statistics.mean(scores),
        4
    )

# =========================================================
# WALK FORWARD
# =========================================================

def walk_forward_backtest(draws, strategy, train_size=30):

    results = []

    for i in range(train_size, len(draws)):

        train = draws[:i]

        test = draws[i]

        pred = strategy.predict(train)

        predicted = set(
            pred["numbers"]
        )

        actual = set(
            test.numbers
        )

        hit = len(
            predicted & actual
        )

        special_hit = (
            1
            if pred["special"] == test.special
            else 0
        )

        results.append({
            "issue": test.issue_no,
            "hit": hit,
            "special_hit": special_hit
        })

    return results

# =========================================================
# WAVE
# =========================================================

def predict_wave(draws):

    recent = draws[-10:]

    score = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    weight = len(recent)

    for draw in reversed(recent):

        wave = get_wave(draw.special)

        score[wave] += weight

        weight -= 1

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[0], ranked[1]

# =========================================================
# BIG SMALL
# =========================================================

def predict_big_small(draws):

    recent = draws[-10:]

    big = 0
    small = 0

    odd = 0
    even = 0

    for draw in recent:

        s = draw.special

        if s >= 25:
            big += 1
        else:
            small += 1

        if s % 2:
            odd += 1
        else:
            even += 1

    size_pred = "大" if big >= small else "小"
    odd_pred = "单" if odd >= even else "双"

    return size_pred, odd_pred

# =========================================================
# DASHBOARD
# =========================================================

def print_dashboard(draws):

    if not draws:

        print("暂无数据")
        return

    latest = draws[-1]

    nums = " ".join(
        str(x).zfill(2)
        for x in latest.numbers
    )

    print("=" * 70)

    print(
        f"最新开奖: "
        f"{latest.issue_no}"
    )

    print(
        f"号码: "
        f"{nums} "
        f"+ "
        f"{str(latest.special).zfill(2)}"
    )

    print("=" * 70)

    next_issue = str(
        int(latest.issue_no) + 1
    )

    print(
        f"\n预测期号: {next_issue}"
    )

    strategy = EnsembleStrategy()

    pred = strategy.predict(draws)

    pred_nums = " ".join(
        str(x).zfill(2)
        for x in pred["numbers"]
    )

    print("\n🎯 集成预测")

    print(
        f"号码: "
        f"{pred_nums} "
        f"+ "
        f"{str(pred['special']).zfill(2)}"
    )

    print(
        f"特码属性: "
        f"{special_text(pred['special'])}"
    )

    print("\n🎨 波色预测")

    main_wave, second_wave = predict_wave(draws)

    print(
        f"主强: "
        f"{main_wave[0]} "
        f"({main_wave[1]})"
    )

    print(
        f"次强: "
        f"{second_wave[0]} "
        f"({second_wave[1]})"
    )

    print("\n📊 大小单双")

    size_pred, odd_pred = predict_big_small(draws)

    print(f"大小预测: {size_pred}")
    print(f"单双预测: {odd_pred}")

    print("\n📈 WalkForward回测")

    results = walk_forward_backtest(
        draws,
        strategy
    )

    if results:

        avg_hit = round(
            statistics.mean(
                r["hit"]
                for r in results
            ),
            4
        )

        special_rate = round(
            (
                sum(
                    r["special_hit"]
                    for r in results
                )
                / len(results)
            ) * 100,
            2
        )

        montecarlo = montecarlo_baseline(draws)

        print(f"平均命中: {avg_hit}")
        print(f"特别号命中率: {special_rate}%")
        print(f"MonteCarlo基准: {montecarlo}")

    print("=" * 70)

# =========================================================
# COMMAND
# =========================================================

def cmd_sync():

    conn = connect_db()

    init_db(conn)

    sync_data(conn)

    draws = load_draws(conn)

    print_dashboard(draws)

    conn.close()

def cmd_show():

    conn = connect_db()

    init_db(conn)

    draws = load_draws(conn)

    print_dashboard(draws)

    conn.close()

# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(
        dest="cmd"
    )

    sub.add_parser("sync")
    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "sync":

        cmd_sync()

    elif args.cmd == "show":

        cmd_show()

    else:

        parser.print_help()

if __name__ == "__main__":
    main()