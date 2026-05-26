#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

API_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "ensemble_v2": "集成投票",
}

STRATEGY_IDS = list(STRATEGY_LABELS.keys())


# =========================================================
# 波色
# =========================================================

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}


def get_color(num: int) -> str:

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"


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
# 数据库
# =========================================================

def connect_db(db_path: str):

    conn = sqlite3.connect(db_path)

    conn.row_factory = sqlite3.Row

    return conn


def init_db(conn):

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS draws (

        issue_no TEXT PRIMARY KEY,

        draw_date TEXT,

        numbers_json TEXT,

        special_number INTEGER,

        created_at TEXT

    );

    CREATE TABLE IF NOT EXISTS prediction_runs (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        issue_no TEXT,

        strategy TEXT,

        hit_count INTEGER,

        created_at TEXT

    );

    CREATE TABLE IF NOT EXISTS prediction_picks (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        run_id INTEGER,

        number INTEGER,

        rank_no INTEGER,

        score REAL

    );

    """)

    conn.commit()


# =========================================================
# 获取新澳门数据
# =========================================================

def fetch_new_macau():

    req = Request(

        API_URL,

        headers={

            "User-Agent": "Mozilla/5.0",

            "Cache-Control": "no-cache"

        }

    )

    with urlopen(req, timeout=20) as resp:

        data = json.loads(resp.read().decode("utf-8"))

    lotteries = data.get("lottery_data", [])

    target = None

    for item in lotteries:

        if item.get("name") == "新澳门彩":

            target = item

            break

    if not target:

        raise RuntimeError("未找到新澳门彩数据")

    records = []

    latest_issue = str(target["expect"])

    latest_nums = [int(x) for x in target["openCode"].split(",")]

    latest_date = target.get("openTime", "")[:10]

    records.append(

        DrawRecord(

            latest_issue,

            latest_date,

            latest_nums[:6],

            latest_nums[6]

        )

    )

    history = target.get("history", [])

    for item in history:

        try:

            parts = item.split("期：")

            issue_no = parts[0].strip()

            nums = [int(x) for x in parts[1].split(",")]

            if len(nums) != 7:
                continue

            records.append(

                DrawRecord(

                    issue_no,

                    latest_date,

                    nums[:6],

                    nums[6]

                )

            )

        except:
            continue

    return records


# =========================================================
# 保存数据
# =========================================================

def upsert_draw(conn, r: DrawRecord):

    exists = conn.execute(

        "SELECT 1 FROM draws WHERE issue_no=?",

        (r.issue_no,)

    ).fetchone()

    now = datetime.now(timezone.utc).isoformat()

    if exists:

        conn.execute("""

        UPDATE draws

        SET draw_date=?,
            numbers_json=?,
            special_number=?

        WHERE issue_no=?

        """, (

            r.draw_date,

            json.dumps(r.numbers),

            r.special_number,

            r.issue_no

        ))

        return "updated"

    conn.execute("""

    INSERT INTO draws

    VALUES (?, ?, ?, ?, ?)

    """, (

        r.issue_no,

        r.draw_date,

        json.dumps(r.numbers),

        r.special_number,

        now

    ))

    return "inserted"


# =========================================================
# 统计
# =========================================================

def _normalize(score_map):

    vals = list(score_map.values())

    mn = min(vals)

    mx = max(vals)

    if mx == mn:

        return {k: 0.0 for k in score_map}

    return {

        k: (v - mn) / (mx - mn)

        for k, v in score_map.items()

    }


def _freq_map(draws):

    freq = {n: 0.0 for n in ALL_NUMBERS}

    for draw in draws:

        for n in draw:

            freq[n] += 1

    return freq


def _momentum_map(draws):

    m = {n: 0.0 for n in ALL_NUMBERS}

    for i, draw in enumerate(draws):

        w = 1 / (1 + i)

        for n in draw:

            m[n] += w

    return m


def _omission_map(draws):

    omission = {

        n: float(len(draws))

        for n in ALL_NUMBERS

    }

    for i, draw in enumerate(draws):

        for n in draw:

            omission[n] = min(

                omission[n],

                float(i + 1)

            )

    return omission


# =========================================================
# 预测
# =========================================================

def generate_scores(draws):

    freq = _normalize(_freq_map(draws))

    momentum = _normalize(_momentum_map(draws))

    omission = _normalize(_omission_map(draws))

    scores = {}

    for n in ALL_NUMBERS:

        scores[n] = (

            freq[n] * 0.45 +

            momentum[n] * 0.35 +

            omission[n] * 0.20

        )

    return scores


def generate_prediction(draws):

    scores = generate_scores(draws)

    ranked = sorted(

        scores.items(),

        key=lambda x: x[1],

        reverse=True

    )

    picks = ranked[:6]

    special = ranked[6]

    return picks, special


# =========================================================
# 保存预测
# =========================================================

def generate_predictions(conn):

    row = conn.execute("""

    SELECT issue_no

    FROM draws

    ORDER BY CAST(issue_no AS INTEGER) DESC

    LIMIT 1

    """).fetchone()

    latest_issue = int(row["issue_no"])

    next_issue = str(latest_issue + 1)

    draws = [

        json.loads(r["numbers_json"])

        for r in conn.execute("""

        SELECT numbers_json

        FROM draws

        ORDER BY CAST(issue_no AS INTEGER) DESC

        LIMIT 120

        """).fetchall()

    ]

    for strategy in STRATEGY_IDS:

        cur = conn.execute("""

        INSERT INTO prediction_runs(

            issue_no,
            strategy,
            created_at

        )

        VALUES (?, ?, ?)

        """, (

            next_issue,

            strategy,

            datetime.now(timezone.utc).isoformat()

        ))

        run_id = cur.lastrowid

        picks, special = generate_prediction(draws)

        for rank, (num, score) in enumerate(picks, start=1):

            conn.execute("""

            INSERT INTO prediction_picks(

                run_id,
                number,
                rank_no,
                score

            )

            VALUES (?, ?, ?, ?)

            """, (

                run_id,

                num,

                rank,

                score

            ))

    conn.commit()

    return next_issue


# =========================================================
# 波色预测
# =========================================================

def predict_color(specials, window=10):

    recent = specials[-window:]

    scores = defaultdict(float)

    for i, num in enumerate(reversed(recent)):

        weight = (window - i) ** 1.3

        scores[get_color(num)] += weight

    ranked = sorted(

        scores.items(),

        key=lambda x: x[1],

        reverse=True

    )

    return ranked


# =========================================================
# 展示
# =========================================================

def print_dashboard(conn):

    latest = conn.execute("""

    SELECT *

    FROM draws

    ORDER BY CAST(issue_no AS INTEGER) DESC

    LIMIT 1

    """).fetchone()

    if latest:

        nums = json.loads(latest["numbers_json"])

        nums_str = " ".join(

            f"{n:02d}" for n in nums

        )

        print()

        print(

            f"最新开奖: "

            f"{latest['issue_no']} | "

            f"{nums_str} + "

            f"{latest['special_number']:02d}"

        )

    print()

    print("预测:")

    rows = conn.execute("""

    SELECT *

    FROM prediction_runs

    ORDER BY id DESC

    LIMIT 5

    """).fetchall()

    for r in rows:

        picks = conn.execute("""

        SELECT *

        FROM prediction_picks

        WHERE run_id=?

        ORDER BY rank_no

        """, (r["id"],)).fetchall()

        nums = " ".join(

            f"{x['number']:02d}"

            for x in picks

        )

        print(

            f"{r['strategy']} -> {nums}"

        )

    specials = [

        r["special_number"]

        for r in conn.execute("""

        SELECT special_number

        FROM draws

        ORDER BY CAST(issue_no AS INTEGER)

        """).fetchall()

    ]

    if len(specials) >= 10:

        colors = predict_color(specials)

        print()

        print("特码波色预测:")

        for c, s in colors:

            print(f"{c}: {s:.2f}")


# =========================================================
# sync
# =========================================================

def cmd_sync(args):

    conn = connect_db(args.db)

    init_db(conn)

    records = fetch_new_macau()

    ins = 0

    upd = 0

    for r in records:

        res = upsert_draw(conn, r)

        if res == "inserted":
            ins += 1
        else:
            upd += 1

    conn.commit()

    print()

    print(

        f"同步完成: "

        f"新增={ins} "

        f"更新={upd}"

    )

    issue = generate_predictions(conn)

    print(f"已生成预测: {issue}")

    print_dashboard(conn)

    conn.close()


# =========================================================
# show
# =========================================================

def cmd_show(args):

    conn = connect_db(args.db)

    init_db(conn)

    print_dashboard(conn)

    conn.close()


# =========================================================
# main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(

        "--db",

        default=DB_PATH_DEFAULT

    )

    sub = parser.add_subparsers(

        dest="cmd",

        required=True

    )

    sync_p = sub.add_parser("sync")

    sync_p.set_defaults(func=cmd_sync)

    show_p = sub.add_parser("show")

    show_p.set_defaults(func=cmd_show)

    args = parser.parse_args()

    args.func(args)


if __name__ == "__main__":

    main()