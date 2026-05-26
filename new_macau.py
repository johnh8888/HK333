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

# =========================================================
# 基础配置
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau_v3.db")

API_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

DYNAMIC_WINDOWS = [10, 30, 60, 80, 100]

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "动量策略",
    "ensemble_v2": "集成投票",
}

STRATEGY_IDS = list(STRATEGY_LABELS.keys())

AI_STATE = {
    "market_cycle": "normal",
    "risk_level": 1.0,
    "current_window": 30,
    "last_hits": [],
}

MARKOV_CHAIN = {
    "红": {"红": 0, "蓝": 0, "绿": 0},
    "蓝": {"红": 0, "蓝": 0, "绿": 0},
    "绿": {"红": 0, "蓝": 0, "绿": 0},
}

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
# 数据库保存
# =========================================================

def upsert_draw(conn, r: DrawRecord):

    now = datetime.now(timezone.utc).isoformat()

    exists = conn.execute(

        "SELECT 1 FROM draws WHERE issue_no=?",

        (r.issue_no,)

    ).fetchone()

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
# 基础统计
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

    freq = {

        n: 0.0

        for n in ALL_NUMBERS

    }

    for draw in draws:

        for n in draw:

            freq[n] += 1

    return freq


def _momentum_map(draws):

    m = {

        n: 0.0

        for n in ALL_NUMBERS

    }

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


def _pair_affinity_map(draws):

    pair = {

        n: 0.0

        for n in ALL_NUMBERS

    }

    for draw in draws:

        for n in draw:

            pair[n] += 1

    return pair


# =========================================================
# AI动态窗口
# =========================================================

def choose_best_window(draws):

    best_window = 30

    best_score = -99999

    for w in DYNAMIC_WINDOWS:

        if len(draws) < w + 20:
            continue

        sample = draws[:w]

        freq = _freq_map(sample)

        omission = _omission_map(sample)

        momentum = _momentum_map(sample)

        pair = _pair_affinity_map(sample)

        total_score = (

            max(freq.values()) * 0.35 +

            max(omission.values()) * 0.25 +

            max(momentum.values()) * 0.25 +

            max(pair.values()) * 0.15

        )

        if w == 10:
            total_score *= 1.08

        if w == 30:
            total_score *= 1.05

        if total_score > best_score:

            best_score = total_score

            best_window = w

    AI_STATE["current_window"] = best_window

    return best_window


# =========================================================
# 动态权重
# =========================================================

def dynamic_weights(draws):

    recent = draws[:30]

    freq = _freq_map(recent)

    omission = _omission_map(recent)

    momentum = _momentum_map(recent)

    heat = max(freq.values())

    omit = max(omission.values())

    mom = max(momentum.values())

    if heat > 6:

        AI_STATE["market_cycle"] = "hot"

        return 0.60, 0.15, 0.25

    if omit > 25:

        AI_STATE["market_cycle"] = "cold"

        return 0.15, 0.65, 0.20

    if mom > 2.5:

        AI_STATE["market_cycle"] = "momentum"

        return 0.25, 0.15, 0.60

    AI_STATE["market_cycle"] = "normal"

    return 0.45, 0.30, 0.25


# =========================================================
# 风险控制
# =========================================================

def risk_control(scores):

    fails = AI_STATE["last_hits"][-5:].count(0)

    if fails >= 4:

        AI_STATE["risk_level"] = 0.6

        return {

            k: v * 0.85

            for k, v in scores.items()

        }

    AI_STATE["risk_level"] = 1.0

    return scores


# =========================================================
# 马尔可夫波色
# =========================================================

def train_markov(specials):

    colors = [get_color(x) for x in specials]

    for i in range(len(colors) - 1):

        MARKOV_CHAIN[colors[i]][colors[i + 1]] += 1


def predict_markov_color(last_color):

    data = MARKOV_CHAIN[last_color]

    return max(data.items(), key=lambda x: x[1])[0]


# =========================================================
# 特码AI
# =========================================================

def special_ai(draws):

    freq = Counter()

    for d in draws[:80]:

        for n in d:

            freq[n] += 1

    ranked = sorted(

        freq.items(),

        key=lambda x: x[1],

        reverse=True

    )

    return ranked[0][0]


# =========================================================
# 主预测
# =========================================================

def generate_scores(draws):

    window = choose_best_window(draws)

    sample = draws[:window]

    freq = _normalize(_freq_map(sample))

    omission = _normalize(_omission_map(sample))

    momentum = _normalize(_momentum_map(sample))

    w_freq, w_omit, w_mom = dynamic_weights(draws)

    scores = {}

    for n in ALL_NUMBERS:

        scores[n] = (

            freq[n] * w_freq +

            omission[n] * w_omit +

            momentum[n] * w_mom

        )

    scores = risk_control(scores)

    return scores


def generate_prediction(draws):

    scores = generate_scores(draws)

    ranked = sorted(

        scores.items(),

        key=lambda x: x[1],

        reverse=True

    )

    picks = ranked[:6]

    special = special_ai(draws)

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

        LIMIT 150

        """).fetchall()

    ]

    specials = [

        r["special_number"]

        for r in conn.execute("""

        SELECT special_number

        FROM draws

        ORDER BY CAST(issue_no AS INTEGER)

        """).fetchall()

    ]

    train_markov(specials)

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

def predict_colors(conn):

    specials = [

        r["special_number"]

        for r in conn.execute("""

        SELECT special_number

        FROM draws

        ORDER BY CAST(issue_no AS INTEGER)

        """).fetchall()

    ]

    if len(specials) < 20:
        return

    train_markov(specials)

    last_color = get_color(specials[-1])

    next_color = predict_markov_color(last_color)

    print()

    print("🎨 AI波色预测")

    print(f"上期波色: {last_color}")

    print(f"下期预测: {next_color}")


# =========================================================
# Dashboard
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

            f"{n:02d}"

            for n in nums

        )

        print()

        print(

            f"最新开奖: "

            f"{latest['issue_no']} | "

            f"{nums_str} + "

            f"{latest['special_number']:02d}"

        )

    print()

    print("🧠 AI状态")

    print(

        f"周期={AI_STATE['market_cycle']} "

        f"窗口={AI_STATE['current_window']} "

        f"风险={AI_STATE['risk_level']}"

    )

    rows = conn.execute("""

    SELECT *

    FROM prediction_runs

    ORDER BY id DESC

    LIMIT 5

    """).fetchall()

    print()

    print("📈 AI预测")

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

    predict_colors(conn)


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