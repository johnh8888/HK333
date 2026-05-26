#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

OFFICIAL_URL_DEFAULT = "https://marksix6.net/index.php?api=1"

THIRD_PARTY_URLS_DEFAULT: List[str] = [
    "https://marksix6.net/index.php?api=1"
]

MINED_CONFIG_KEY = "mined_strategy_config_v1"

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
# 波色
# =========================================================

RED = {
    1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46
}

BLUE = {
    3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48
}

GREEN = {
    5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49
}


def get_color(num: int) -> str:

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"


# =========================================================
# 属性
# =========================================================

def special_attributes(num: int) -> Dict[str, str]:

    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    tens, ones = divmod(num, 10)

    total = tens + ones

    total_odd_even = "单" if total % 2 else "双"

    total_big_small = "大" if total >= 7 else "小"

    tail_big_small = "大" if ones >= 5 else "小"

    color = get_color(num)

    if ones in (1, 6):
        element = "水"

    elif ones in (2, 7):
        element = "火"

    elif ones in (3, 8):
        element = "木"

    elif ones in (4, 9):
        element = "金"

    else:
        element = "土"

    return {
        "单双": odd_even,
        "大小": big_small,
        "合单双": total_odd_even,
        "合大小": total_big_small,
        "尾大小": tail_big_small,
        "色波": color,
        "五行": element
    }


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
# 时间
# =========================================================

def utc_now() -> str:

    return datetime.now(timezone.utc).isoformat()


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

        draw_date TEXT NOT NULL,

        numbers_json TEXT NOT NULL,

        special_number INTEGER NOT NULL,

        source TEXT,

        created_at TEXT NOT NULL,

        updated_at TEXT NOT NULL

    );

    CREATE TABLE IF NOT EXISTS prediction_runs (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        issue_no TEXT NOT NULL,

        strategy TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'PENDING',

        hit_count INTEGER,

        hit_rate REAL,

        special_hit INTEGER,

        created_at TEXT NOT NULL,

        reviewed_at TEXT

    );

    CREATE TABLE IF NOT EXISTS prediction_picks (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        run_id INTEGER NOT NULL,

        pick_type TEXT NOT NULL DEFAULT 'MAIN',

        number INTEGER NOT NULL,

        rank INTEGER NOT NULL,

        score REAL NOT NULL,

        reason TEXT NOT NULL

    );

    """)

    conn.commit()


# =========================================================
# 获取新澳门数据
# =========================================================

def fetch_new_macau():

    req = Request(

        OFFICIAL_URL_DEFAULT,

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

    latest_nums = [

        int(x)

        for x in target["openCode"].split(",")

    ]

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

            nums = [

                int(x)

                for x in parts[1].split(",")

            ]

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

    conn.execute("""

    INSERT INTO draws

    VALUES (?, ?, ?, ?, ?, ?, ?)

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


# =========================================================
# 同步
# =========================================================

def sync_from_records(conn, records, source):

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
# 工具
# =========================================================

def next_issue(issue_no):

    digits = ''.join(

        ch for ch in issue_no

        if ch.isdigit()

    )

    if not digits:
        return issue_no

    num = int(digits) + 1

    return f"{num:0{len(digits)}d}"


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
# 评分
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


# =========================================================
# 策略
# =========================================================

def generate_prediction(draws):

    scores = generate_scores(draws)

    ranked = sorted(

        scores.items(),

        key=lambda x: x[1],

        reverse=True

    )

    picks = ranked[:6]

    special = ranked[6]

    return picks, special, scores


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

    latest_issue = row["issue_no"]

    target_issue = next_issue(latest_issue)

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

        now = utc_now()

        cur = conn.execute("""

        INSERT INTO prediction_runs(

            issue_no,
            strategy,
            status,
            created_at

        )

        VALUES (?, ?, 'PENDING', ?)

        """, (

            target_issue,

            strategy,

            now

        ))

        run_id = cur.lastrowid

        picks, special, scores = generate_prediction(draws)

        for rank, (num, score) in enumerate(picks, start=1):

            conn.execute("""

            INSERT INTO prediction_picks(

                run_id,
                pick_type,
                number,
                rank,
                score,
                reason

            )

            VALUES (?, 'MAIN', ?, ?, ?, ?)

            """, (

                run_id,

                num,

                rank,

                score,

                strategy

            ))

        conn.execute("""

        INSERT INTO prediction_picks(

            run_id,
            pick_type,
            number,
            rank,
            score,
            reason

        )

        VALUES (?, 'SPECIAL', ?, 1, ?, '特别号')

        """, (

            run_id,

            special[0],

            special[1]

        ))

    conn.commit()

    return target_issue


# =========================================================
# 波色预测
# =========================================================

def predict_color_weighted(

    specials,

    window=10

):

    recent = specials[-window:]

    scores = defaultdict(float)

    total_weight = 0.0

    for i, num in enumerate(reversed(recent)):

        weight = (window - i) ** 1.4

        color = get_color(num)

        scores[color] += weight

        total_weight += weight

    ranked = sorted(

        scores.items(),

        key=lambda x: x[1],

        reverse=True

    )

    main_color = ranked[0][0]

    main_score = ranked[0][1] / total_weight

    second_color = ranked[1][0]

    second_score = ranked[1][1] / total_weight

    return (

        main_color,

        second_color,

        main_score,

        second_score

    )


# =========================================================
# 回测
# =========================================================

def backtest_colors(conn, recent_limit=12):

    rows = conn.execute("""

    SELECT special_number

    FROM draws

    ORDER BY CAST(issue_no AS INTEGER)

    """).fetchall()

    specials = [

        r["special_number"]

        for r in rows

    ]

    total = 0

    hit = 0

    miss = 0

    max_miss = 0

    for i in range(

        len(specials) - recent_limit,

        len(specials)

    ):

        train = specials[:i]

        actual = get_color(specials[i])

        main_color, _, _, _ = predict_color_weighted(train)

        total += 1

        if actual == main_color:

            hit += 1

            miss = 0

        else:

            miss += 1

            max_miss = max(max_miss, miss)

    return total, hit, max_miss


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

    rows = conn.execute("""

    SELECT *

    FROM prediction_runs

    WHERE status='PENDING'

    ORDER BY id DESC

    LIMIT 6

    """).fetchall()

    if rows:

        print()

        print(f"预测期号: {rows[0]['issue_no']}")

        print()

        for r in rows:

            picks = conn.execute("""

            SELECT *

            FROM prediction_picks

            WHERE run_id=?
            AND pick_type='MAIN'

            ORDER BY rank

            """, (r["id"],)).fetchall()

            nums = " ".join(

                f"{x['number']:02d}"

                for x in picks

            )

            special = conn.execute("""

            SELECT number

            FROM prediction_picks

            WHERE run_id=?
            AND pick_type='SPECIAL'

            """, (r["id"],)).fetchone()

            label = STRATEGY_LABELS.get(

                r["strategy"],

                r["strategy"]

            )

            print(

                f"{label} : "

                f"{nums} + "

                f"{special['number']:02d}"

            )

            attrs = special_attributes(

                special["number"]

            )

            print(

                f"特码属性: "

                f"{attrs['单双']}/"

                f"{attrs['大小']} "

                f"合{attrs['合单双']}/"

                f"{attrs['合大小']} "

                f"尾{attrs['尾大小']} "

                f"{attrs['色波']} "

                f"{attrs['五行']}"

            )

            print()

    specials = [

        r["special_number"]

        for r in conn.execute("""

        SELECT special_number

        FROM draws

        ORDER BY CAST(issue_no AS INTEGER)

        """).fetchall()

    ]

    if len(specials) >= 10:

        main_color, second_color, main_score, second_score = predict_color_weighted(

            specials

        )

        print("🎨 波色预测")

        print(

            f"主强: {main_color} "

            f"({main_score:.3f})"

        )

        print(

            f"次强: {second_color} "

            f"({second_score:.3f})"

        )

        total, hit, max_miss = backtest_colors(conn)

        print()

        print("📊 波色回测")

        print(

            f"命中率: "

            f"{hit}/{total} "

            f"({hit/total*100:.1f}%)"

        )

        print(

            f"最大连错: {max_miss}期"

        )


# =========================================================
# sync
# =========================================================

def cmd_sync(args):

    conn = connect_db(args.db)

    init_db(conn)

    records = fetch_new_macau()

    total, ins, upd = sync_from_records(

        conn,

        records,

        "new_macau"

    )

    print()

    print(

        f"同步完成: "

        f"total={total} "

        f"new={ins} "

        f"updated={upd}"

    )

    issue = generate_predictions(conn)

    print()

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

    parser = argparse.ArgumentParser(

        description="新澳门六合彩预测工具"

    )

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