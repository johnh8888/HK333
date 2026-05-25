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
from typing import Dict, List
from urllib.request import Request, urlopen

# =========================================================
# CONFIG
# =========================================================

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "macau_pro.db"

THIRD_PARTY_URLS = [
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
# UTILS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()

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

def special_text(n: int):

    return (
        f"{get_odd_even(n)}/"
        f"{get_size(n)} "
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

    """)

    conn.commit()

# =========================================================
# FETCH
# =========================================================

def parse_marksix6(payload):

    result = []

    data = payload.get("lottery_data", [])

    target = None

    for item in data:

        if "澳门" in item.get("name", ""):
            target = item
            break

    if not target:
        return result

    histories = target.get("history", [])

    for row in histories:

        try:

            parts = row.split("期：")

            issue = parts[0].strip()

            nums = [
                int(x.strip())
                for x in parts[1].split(",")
            ]

            if len(nums) >= 7:

                result.append(
                    DrawRecord(
                        issue_no=issue,
                        draw_date=utc_now()[:10],
                        numbers=nums[:6],
                        special=nums[6]
                    )
                )

        except:
            pass

    return result

def fetch_records():

    for url in THIRD_PARTY_URLS:

        try:

            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urlopen(req, timeout=20) as resp:

                payload = json.loads(
                    resp.read().decode("utf-8")
                )

            rows = parse_marksix6(payload)

            if rows:
                return rows, url

        except Exception as e:

            print("fetch failed:", e)

    raise RuntimeError("all data sources failed")

# =========================================================
# STORAGE
# =========================================================

def upsert_draw(conn, row, source):

    conn.execute("""

    INSERT OR REPLACE INTO draws VALUES (
        ?,?,?,?,?,?,?,?,?,?
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

    for row in rows:
        upsert_draw(conn, row, source)

    conn.commit()

    print(f"同步完成: {len(rows)} 条")

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
# ANALYTICS
# =========================================================

def frequency_score(draws, window=80):

    freq = defaultdict(float)

    recent = draws[-window:]

    total = max(len(recent), 1)

    for idx, draw in enumerate(recent):

        weight = (idx + 1) / total

        for n in draw.numbers:
            freq[n] += weight

    for n in ALL_NUMBERS:
        freq.setdefault(n, 0.0)

    return normalize(freq)

def omission_score(draws):

    latest = {
        n: None
        for n in ALL_NUMBERS
    }

    rev = list(reversed(draws))

    for idx, draw in enumerate(rev):

        for n in draw.numbers:

            if latest[n] is None:
                latest[n] = idx

    mx = max(
        v if v is not None else 0
        for v in latest.values()
    )

    result = {}

    for n in ALL_NUMBERS:

        gap = latest[n]

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

    score = {}

    for n in ALL_NUMBERS:

        score[n] = (
            r[n] - (o[n] / 2.0)
        )

    return normalize(score)

def pair_affinity_score(draws):

    pair_count = defaultdict(int)

    for draw in draws[-100:]:

        nums = sorted(draw.numbers)

        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):

                pair_count[
                    (nums[i], nums[j])
                ] += 1

    score = defaultdict(float)

    for (a, b), c in pair_count.items():

        score[a] += c
        score[b] += c

    for n in ALL_NUMBERS:
        score.setdefault(n, 0.0)

    return normalize(score)

# =========================================================
# STRATEGY
# =========================================================

class EnsembleStrategy:

    name = "ensemble_v3"

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

        top6 = ranked[:6]

        special = ranked[6]

        return {
            "numbers": [n for n, _ in top6],
            "special": special[0],
            "scores": final
        }

# =========================================================
# MONTE CARLO
# =========================================================

def montecarlo_baseline(draws, simulations=1000):

    hits = []

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

        hits.append(
            total / simulations
        )

    return round(
        sum(hits) / len(hits),
        4
    )

# =========================================================
# BACKTEST
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

    print("=" * 60)

    print(
        f"最新开奖: {latest.issue_no}"
    )

    print(
        f"号码: {nums} + {str(latest.special).zfill(2)}"
    )

    print("=" * 60)

    strategy = EnsembleStrategy()

    pred = strategy.predict(draws)

    pred_nums = " ".join(
        str(x).zfill(2)
        for x in pred["numbers"]
    )

    print("\n预测结果:")

    print(
        f"集成策略: "
        f"{pred_nums} "
        f"+ {str(pred['special']).zfill(2)}"
    )

    print(
        f"特码属性: "
        f"{special_text(pred['special'])}"
    )

    print("\n波色预测:")

    main_wave, second_wave = predict_wave(draws)

    print(
        f"主强: {main_wave[0]} "
        f"({main_wave[1]})"
    )

    print(
        f"次强: {second_wave[0]} "
        f"({second_wave[1]})"
    )

    print("\n回测分析:")

    results = walk_forward_backtest(
        draws,
        strategy
    )

    if results:

        avg_hit = (
            sum(r["hit"] for r in results)
            / len(results)
        )

        special_rate = (
            sum(r["special_hit"] for r in results)
            / len(results)
        ) * 100

        print(
            f"平均命中: {avg_hit:.2f}"
        )

        print(
            f"特别号命中率: "
            f"{special_rate:.2f}%"
        )

    baseline = montecarlo_baseline(draws)

    print(
        f"\nMonteCarlo随机基准: "
        f"{baseline:.4f}"
    )

    print("=" * 60)

# =========================================================
# COMMANDS
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