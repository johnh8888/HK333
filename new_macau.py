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

THIRD_PARTY_URLS_DEFAULT = [
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
# 六合彩标准波色
# =========================================================

RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE_WAVE = {
    3, 4, 9, 10, 14, 15,
    20, 25, 26, 31,
    36, 37, 41, 42,
    47, 48
}

GREEN_WAVE = {
    5, 6, 11, 16, 17,
    21, 22, 27, 28,
    32, 33, 38, 39,
    43, 44, 49
}


def get_color(num: int) -> str:

    if num in RED_WAVE:
        return "红"

    if num in BLUE_WAVE:
        return "蓝"

    if num in GREEN_WAVE:
        return "绿"

    return "未知"


# =========================================================
# 特码属性
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
        "五行": element,
    }


# =========================================================
# 波色预测
# =========================================================

def predict_color_weighted(
    specials: List[int],
    window: int = 10
) -> Tuple[str, str, float, float]:

    if not specials:
        return "蓝", "绿", 0.0, 0.0

    recent = specials[-window:]

    scores = defaultdict(float)

    total_weight = 0

    for i, num in enumerate(reversed(recent)):

        weight = window - i

        scores[get_color(num)] += weight

        total_weight += weight

    if total_weight == 0:
        return "蓝", "绿", 0.0, 0.0

    sorted_colors = sorted(
        scores.items(),
        key=lambda x: (-x[1], x[0])
    )

    main_color = sorted_colors[0][0]

    main_score = sorted_colors[0][1] / total_weight

    second_color = (
        sorted_colors[1][0]
        if len(sorted_colors) > 1
        else "绿"
    )

    second_score = (
        sorted_colors[1][1] / total_weight
        if len(sorted_colors) > 1
        else 0.0
    )

    return (
        main_color,
        second_color,
        main_score,
        second_score
    )


def backtest_colors(
    conn,
    recent_limit: int = 10,
    window: int = 10
):

    rows = conn.execute("""
        SELECT special_number
        FROM draws
        ORDER BY draw_date ASC, issue_no ASC
    """).fetchall()

    specials = [r["special_number"] for r in rows]

    if len(specials) < recent_limit + window:
        return 0, 0, 0, 0

    total = 0
    main_hit = 0
    second_hit = 0
    any_hit = 0

    start_idx = len(specials) - recent_limit

    for i in range(start_idx, len(specials)):

        train = specials[:i]

        actual = get_color(specials[i])

        (
            main_color,
            second_color,
            _,
            _
        ) = predict_color_weighted(train, window)

        if main_color == actual:
            main_hit += 1

        if second_color == actual:
            second_hit += 1

        if main_color == actual or second_color == actual:
            any_hit += 1

        total += 1

    return total, main_hit, second_hit, any_hit


# =========================================================
# 数据库
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        reviewed_at TEXT,
        UNIQUE(issue_no, strategy)
    );

    CREATE TABLE IF NOT EXISTS prediction_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        pick_type TEXT NOT NULL DEFAULT 'MAIN',
        number INTEGER NOT NULL,
        rank INTEGER NOT NULL,
        score REAL NOT NULL,
        reason TEXT NOT NULL,
        UNIQUE(run_id, number),
        FOREIGN KEY(run_id)
            REFERENCES prediction_runs(id)
            ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS model_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    conn.commit()


def get_model_state(conn, key):

    row = conn.execute("""
        SELECT value
        FROM model_state
        WHERE key=?
    """, (key,)).fetchone()

    return row["value"] if row else None


def set_model_state(conn, key, value):

    now = utc_now()

    conn.execute("""
        INSERT INTO model_state(key,value,updated_at)
        VALUES (?,?,?)
        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
    """, (key, value, now))


# =========================================================
# 获取在线数据
# =========================================================

def _parse_marksix6_response(payload):

    records = []

    hk_data = next(
        (
            l for l in payload.get("lottery_data", [])
            if l.get("name") == "新澳门彩"
        ),
        None
    )

    if not hk_data:
        return records

    try:

        latest_open_time = datetime.strptime(
            hk_data.get("openTime", ""),
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:

        latest_open_time = datetime.now()

    for idx, item in enumerate(hk_data.get("history", [])):

        try:

            parts = item.split("期：")

            if len(parts) != 2:
                continue

            issue_no = parts[0].strip()

            nums = [
                int(n.strip())
                for n in parts[1].split(",")
            ]

            if len(nums) != 7:
                continue

            draw_date = (
                latest_open_time - timedelta(days=idx)
            ).strftime("%Y-%m-%d")

            records.append(
                DrawRecord(
                    issue_no,
                    draw_date,
                    nums[:6],
                    nums[6]
                )
            )

        except Exception:
            continue

    return records


def fetch_online_records():

    for url in THIRD_PARTY_URLS_DEFAULT:

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

            records = _parse_marksix6_response(payload)

            if records:
                return records

        except Exception as e:

            print(f"获取失败: {e}")

    raise RuntimeError("无法获取数据")


def upsert_draw(conn, record, source="online"):

    now = utc_now()

    if conn.execute("""
        SELECT 1
        FROM draws
        WHERE issue_no=?
    """, (record.issue_no,)).fetchone():

        conn.execute("""
            UPDATE draws
            SET
                draw_date=?,
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


def sync_from_records(conn, records):

    for r in records:
        upsert_draw(conn, r)

    conn.commit()


def next_issue(issue_no):

    digits = ''.join(
        ch for ch in issue_no
        if ch.isdigit()
    )

    if not digits:
        return issue_no

    num = int(digits) + 1

    return str(num).zfill(len(digits))


# =========================================================
# 特征
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
            freq[n] += 1.0

    return freq


# =========================================================
# 修复 omission
# =========================================================

def _omission_map(draws):

    omission = {
        n: float(len(draws))
        for n in ALL_NUMBERS
    }

    for i, draw in enumerate(draws):

        for n in draw:

            if omission[n] == float(len(draws)):
                omission[n] = float(i)

    return omission


def _momentum_map(draws):

    m = {n: 0.0 for n in ALL_NUMBERS}

    for i, draw in enumerate(draws):

        w = 1.0 / (1.0 + i)

        for n in draw:
            m[n] += w

    return m


# =========================================================
# 获取最近数据
# =========================================================

def get_latest_draws(conn, limit=200):

    return [
        json.loads(r["numbers_json"])
        for r in conn.execute("""
            SELECT numbers_json
            FROM draws
            ORDER BY draw_date DESC, issue_no DESC
            LIMIT ?
        """, (limit,)).fetchall()
    ]


# =========================================================
# 策略
# =========================================================

def _pick_top_six(scores, reason):

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    result = []

    for idx, (n, s) in enumerate(ranked[:6]):

        result.append(
            (
                n,
                idx + 1,
                s,
                f"{reason} score={s:.4f}"
            )
        )

    return result


def _default_mined_config():

    return {
        "w_freq": 0.40,
        "w_omit": 0.35,
        "w_mom": 0.25,
    }


def _apply_weight_config(draws, config, reason):

    freq = _normalize(_freq_map(draws))

    omission = _normalize(_omission_map(draws))

    momentum = _normalize(_momentum_map(draws))

    scores = {}

    for n in ALL_NUMBERS:

        scores[n] = (
            freq[n] * config["w_freq"] +
            omission[n] * config["w_omit"] +
            momentum[n] * config["w_mom"]
        )

    picks = _pick_top_six(scores, reason)

    main_set = {
        n for n, _, _, _ in picks
    }

    candidates = [
        (n, s)
        for n, s in sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        if n not in main_set
    ]

    special_number, special_score = candidates[0]

    return (
        picks,
        special_number,
        special_score,
        scores
    )


def generate_strategy(draws, strategy, mined_config=None):

    if strategy == "hot_v1":

        return _apply_weight_config(
            draws,
            {
                "w_freq": 0.8,
                "w_omit": 0.0,
                "w_mom": 0.2,
            },
            "热号"
        )

    if strategy == "cold_rebound_v1":

        return _apply_weight_config(
            draws,
            {
                "w_freq": 0.0,
                "w_omit": 0.8,
                "w_mom": 0.2,
            },
            "冷号"
        )

    if strategy == "momentum_v1":

        return _apply_weight_config(
            draws,
            {
                "w_freq": 0.1,
                "w_omit": 0.0,
                "w_mom": 0.9,
            },
            "动量"
        )

    return _apply_weight_config(
        draws,
        mined_config or _default_mined_config(),
        "综合"
    )


# =========================================================
# 生成预测
# =========================================================

def generate_predictions(conn, issue_no=None):

    row = conn.execute("""
        SELECT issue_no
        FROM draws
        ORDER BY draw_date DESC, issue_no DESC
        LIMIT 1
    """).fetchone()

    if not row:
        raise RuntimeError("数据库没有开奖数据")

    target_issue = issue_no or next_issue(row["issue_no"])

    draws = get_latest_draws(conn, limit=200)

    config_json = get_model_state(conn, MINED_CONFIG_KEY)

    mined_cfg = (
        json.loads(config_json)
        if config_json
        else _default_mined_config()
    )

    for strategy in STRATEGY_IDS:

        now = utc_now()

        # 修复 run_id replace
        conn.execute("""
            INSERT INTO prediction_runs(
                issue_no,
                strategy,
                status,
                created_at
            )
            VALUES (?, ?, 'PENDING', ?)

            ON CONFLICT(issue_no, strategy)
            DO UPDATE SET
                status='PENDING'
        """, (
            target_issue,
            strategy,
            now
        ))

        run_id = conn.execute("""
            SELECT id
            FROM prediction_runs
            WHERE issue_no=? AND strategy=?
        """, (
            target_issue,
            strategy
        )).fetchone()["id"]

        conn.execute("""
            DELETE FROM prediction_picks
            WHERE run_id=?
        """, (run_id,))

        (
            picks,
            special_number,
            special_score,
            _
        ) = generate_strategy(
            draws,
            strategy,
            mined_cfg
        )

        conn.executemany("""
            INSERT INTO prediction_picks(
                run_id,
                pick_type,
                number,
                rank,
                score,
                reason
            )
            VALUES (?,?,?,?,?,?)
        """, [
            (
                run_id,
                "MAIN",
                n,
                rank,
                score,
                reason
            )
            for n, rank, score, reason in picks
        ] + [
            (
                run_id,
                "SPECIAL",
                special_number,
                1,
                special_score,
                "特别号"
            )
        ])

    conn.commit()

    return target_issue


# =========================================================
# 复盘
# =========================================================

def review_latest(conn):

    latest = conn.execute("""
        SELECT issue_no, numbers_json, special_number
        FROM draws
        ORDER BY draw_date DESC, issue_no DESC
        LIMIT 1
    """).fetchone()

    if not latest:
        return

    winning = set(json.loads(latest["numbers_json"]))

    winning_special = latest["special_number"]

    issue_no = latest["issue_no"]

    runs = conn.execute("""
        SELECT id
        FROM prediction_runs
        WHERE issue_no=?
        AND status='PENDING'
    """, (issue_no,)).fetchall()

    for run in runs:

        run_id = run["id"]

        mains = [
            r["number"]
            for r in conn.execute("""
                SELECT number
                FROM prediction_picks
                WHERE run_id=?
                AND pick_type='MAIN'
            """, (run_id,)).fetchall()
        ]

        special_row = conn.execute("""
            SELECT number
            FROM prediction_picks
            WHERE run_id=?
            AND pick_type='SPECIAL'
        """, (run_id,)).fetchone()

        special = special_row["number"]

        hit_count = len([
            n for n in mains
            if n in winning
        ])

        hit_rate = hit_count / 6.0

        special_hit = 1 if special == winning_special else 0

        conn.execute("""
            UPDATE prediction_runs
            SET
                status='REVIEWED',
                hit_count=?,
                hit_rate=?,
                special_hit=?,
                reviewed_at=?
            WHERE id=?
        """, (
            hit_count,
            hit_rate,
            special_hit,
            utc_now(),
            run_id
        ))

    conn.commit()


# =========================================================
# Walk Forward 回测
# =========================================================

def walk_forward_backtest(
    conn,
    strategy="balanced_v1",
    train_size=80,
    test_size=10
):

    rows = conn.execute("""
        SELECT issue_no, numbers_json
        FROM draws
        ORDER BY draw_date ASC, issue_no ASC
    """).fetchall()

    if len(rows) < train_size + test_size:
        print("数据不足")
        return

    total_hits = 0

    total_rounds = 0

    print("\n========== Walk Forward 回测 ==========")

    for idx in range(
        train_size,
        min(len(rows), train_size + test_size)
    ):

        history_rows = rows[:idx]

        draws = [
            json.loads(r["numbers_json"])
            for r in reversed(history_rows[-200:])
        ]

        actual = set(
            json.loads(rows[idx]["numbers_json"])
        )

        issue = rows[idx]["issue_no"]

        (
            picks,
            _,
            _,
            _
        ) = generate_strategy(
            draws,
            strategy,
            _default_mined_config()
        )

        predicted = {
            n for n, _, _, _ in picks
        }

        hit = len(predicted & actual)

        total_hits += hit

        total_rounds += 1

        print(
            f"{issue} | "
            f"命中 {hit}/6 | "
            f"预测={sorted(predicted)} | "
            f"实际={sorted(actual)}"
        )

    avg_hit = (
        total_hits / total_rounds
        if total_rounds
        else 0
    )

    print("\n========== 回测结果 ==========")

    print(f"策略: {strategy}")

    print(f"回测期数: {total_rounds}")

    print(f"平均命中: {avg_hit:.2f}")


# =========================================================
# Dashboard
# =========================================================

def print_dashboard(conn):

    latest = conn.execute("""
        SELECT *
        FROM draws
        ORDER BY draw_date DESC, issue_no DESC
        LIMIT 1
    """).fetchone()

    if latest:

        nums = " ".join(
            f"{n:02d}"
            for n in json.loads(latest["numbers_json"])
        )

        print(
            f"最新开奖: "
            f"{latest['issue_no']} | "
            f"{nums} + "
            f"{latest['special_number']:02d}"
        )

    pending = conn.execute("""
        SELECT id, issue_no, strategy
        FROM prediction_runs
        WHERE status='PENDING'
        ORDER BY strategy
    """).fetchall()

    if pending:

        print(f"\n预测期号: {pending[0]['issue_no']}")

        for r in pending:

            mains = [
                str(x["number"]).zfill(2)
                for x in conn.execute("""
                    SELECT number
                    FROM prediction_picks
                    WHERE run_id=?
                    AND pick_type='MAIN'
                    ORDER BY rank
                """, (r["id"],)).fetchall()
            ]

            special_row = conn.execute("""
                SELECT number
                FROM prediction_picks
                WHERE run_id=?
                AND pick_type='SPECIAL'
            """, (r["id"],)).fetchone()

            special = (
                str(special_row["number"]).zfill(2)
                if special_row
                else "--"
            )

            label = STRATEGY_LABELS.get(
                r["strategy"],
                r["strategy"]
            )

            print(
                f"{label:<10}: "
                f"{' '.join(mains)} + {special}"
            )

            # 恢复特码属性

            if special_row:

                attrs = special_attributes(
                    special_row["number"]
                )

                print(
                    f"    特码属性: "
                    f"{attrs['单双']}/"
                    f"{attrs['大小']} "
                    f"合{attrs['合单双']}/"
                    f"{attrs['合大小']} "
                    f"尾{attrs['尾大小']} "
                    f"{attrs['色波']} "
                    f"{attrs['五行']}"
                )

    # 波色预测

    all_specials = [
        r["special_number"]
        for r in conn.execute("""
            SELECT special_number
            FROM draws
            ORDER BY draw_date ASC, issue_no ASC
        """).fetchall()
    ]

    (
        main_color,
        second_color,
        main_score,
        second_score
    ) = predict_color_weighted(
        all_specials,
        10
    )

    print("\n特码波色预测:")

    print(
        f"主强: {main_color} ({main_score:.2f}) "
        f"次强: {second_color} ({second_score:.2f})"
    )

    (
        total,
        main_hit,
        second_hit,
        any_hit
    ) = backtest_colors(conn)

    if total > 0:

        print(
            f"波色回测: "
            f"主强={main_hit/total*100:.1f}% "
            f"次强={second_hit/total*100:.1f}% "
            f"二中一={any_hit/total*100:.1f}%"
        )

    # 最近10期统计

    stats = conn.execute("""
        SELECT
            strategy,

            COUNT(*) AS cnt,

            ROUND(AVG(hit_count), 2) AS avg_hit,

            ROUND(
                AVG(hit_rate) * 100,
                1
            ) AS hit_rate_pct,

            ROUND(
                AVG(COALESCE(special_hit, 0)) * 100,
                1
            ) AS special_rate_pct

        FROM (

            SELECT *
            FROM prediction_runs
            WHERE status='REVIEWED'
            ORDER BY id DESC
            LIMIT 10

        )

        GROUP BY strategy

        ORDER BY avg_hit DESC
    """).fetchall()

    if stats:

        print("\n最近10期历史命中统计:")

        for s in stats:

            label = STRATEGY_LABELS.get(
                s["strategy"],
                s["strategy"]
            )

            print(
                f"{label:<10}: "
                f"期数={s['cnt']} "
                f"平均命中={s['avg_hit']}个 "
                f"命中率={s['hit_rate_pct']}% "
                f"特别号命中率={s['special_rate_pct']}%"
            )


# =========================================================
# 命令
# =========================================================

def cmd_sync(args):

    conn = connect_db(args.db)

    try:

        init_db(conn)

        records = fetch_online_records()

        sync_from_records(conn, records)

        review_latest(conn)

        issue = generate_predictions(conn)

        print(f"已生成 {issue} 期预测")

        print_dashboard(conn)

    finally:

        conn.close()


def cmd_show(args):

    conn = connect_db(args.db)

    try:

        print_dashboard(conn)

    finally:

        conn.close()


def cmd_backtest(args):

    conn = connect_db(args.db)

    try:

        walk_forward_backtest(
            conn,
            strategy=args.strategy,
            train_size=args.train_size,
            test_size=args.test_size
        )

    finally:

        conn.close()


# =========================================================
# main
# =========================================================

def main():

    p = argparse.ArgumentParser(
        description="新澳门六合彩预测工具"
    )

    p.add_argument(
        "--db",
        default=DB_PATH_DEFAULT
    )

    sub = p.add_subparsers(
        dest="cmd",
        required=True
    )

    # sync

    sp = sub.add_parser("sync")

    sp.set_defaults(func=cmd_sync)

    # show

    sub.add_parser("show").set_defaults(func=cmd_show)

    # backtest

    bp = sub.add_parser("backtest")

    bp.add_argument(
        "--strategy",
        default="balanced_v1"
    )

    bp.add_argument(
        "--train-size",
        type=int,
        default=80
    )

    bp.add_argument(
        "--test-size",
        type=int,
        default=10
    )

    bp.set_defaults(func=cmd_backtest)

    args = p.parse_args()

    args.func(args)


if __name__ == "__main__":
    main()