#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# =========================================================
# 基础配置
# =========================================================

DB_PATH = "new_macau.db"

API_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced": "组合策略",
    "hot": "热号策略",
    "cold": "冷号回补",
    "momentum": "近期动量",
    "ensemble": "集成投票",
    "pattern": "规律挖掘",
}

# =========================================================
# 正确波色
# =========================================================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20,
    25, 26, 31, 36, 37, 41,
    42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}

# =========================================================
# 时间
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()

# =========================================================
# 数据库
# =========================================================

def connect_db():

    conn = sqlite3.connect(DB_PATH)

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

    CREATE TABLE IF NOT EXISTS prediction_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_no TEXT,
        strategy TEXT,
        status TEXT,
        hit_count INTEGER,
        hit_rate REAL,
        special_hit INTEGER,
        created_at TEXT,
        reviewed_at TEXT,
        UNIQUE(issue_no,strategy)
    );

    CREATE TABLE IF NOT EXISTS prediction_picks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        pick_type TEXT,
        number INTEGER,
        rank INTEGER,
        score REAL,
        reason TEXT
    );

    """)

    conn.commit()

# =========================================================
# 波色
# =========================================================

def get_color(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

# =========================================================
# 特码属性
# =========================================================

def special_attributes(n):

    odd_even = "单" if n % 2 else "双"

    big_small = "大" if n >= 25 else "小"

    tail = n % 10

    if tail in [1, 6]:
        wx = "水"
    elif tail in [2, 7]:
        wx = "火"
    elif tail in [3, 8]:
        wx = "木"
    elif tail in [4, 9]:
        wx = "金"
    else:
        wx = "土"

    return {
        "单双": odd_even,
        "大小": big_small,
        "色波": get_color(n),
        "五行": wx
    }

# =========================================================
# 拉取数据
# =========================================================

def fetch_latest():

    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(req, timeout=20) as resp:

        data = json.loads(
            resp.read().decode("utf-8")
        )

    lottery = next(
        (
            x for x in data["lottery_data"]
            if x["name"] == "新澳门彩"
        ),
        None
    )

    if not lottery:
        raise RuntimeError("无法获取新澳门彩")

    out = []

    history = lottery["history"]

    for row in history:

        parts = row.split("期：")

        issue = parts[0].strip()

        nums = [
            int(x)
            for x in parts[1].split(",")
        ]

        out.append({
            "issue_no": issue,
            "numbers": nums[:6],
            "special": nums[6]
        })

    return out

# =========================================================
# 保存开奖
# =========================================================

def save_draws(conn, draws):

    now = utc_now()

    for d in draws:

        conn.execute("""

        INSERT INTO draws(
            issue_no,
            draw_date,
            numbers_json,
            special_number,
            source,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?,?)

        ON CONFLICT(issue_no)
        DO UPDATE SET
            numbers_json=excluded.numbers_json,
            special_number=excluded.special_number,
            updated_at=excluded.updated_at

        """, (
            d["issue_no"],
            "",
            json.dumps(d["numbers"]),
            d["special"],
            "marksix6",
            now,
            now
        ))

    conn.commit()

# =========================================================
# omission 修复
# =========================================================

def omission_scores(draws):

    miss = {
        n: 0
        for n in ALL_NUMBERS
    }

    for n in ALL_NUMBERS:

        c = 0

        for d in draws:

            if n in d:
                break

            c += 1

        miss[n] = c

    mx = max(miss.values()) or 1

    return {
        k: v / mx
        for k, v in miss.items()
    }

# =========================================================
# 频率
# =========================================================

def freq_scores(draws):

    c = Counter()

    for d in draws:
        c.update(d)

    mx = max(c.values()) or 1

    return {
        n: c[n] / mx
        for n in ALL_NUMBERS
    }

# =========================================================
# 动量
# =========================================================

def momentum_scores(draws):

    s = {
        n: 0
        for n in ALL_NUMBERS
    }

    for i, d in enumerate(draws):

        w = 1 / (i + 1)

        for n in d:
            s[n] += w

    mx = max(s.values()) or 1

    return {
        k: v / mx
        for k, v in s.items()
    }

# =========================================================
# 策略
# =========================================================

def strategy_scores(draws, strategy):

    freq = freq_scores(draws)

    omit = omission_scores(draws)

    mom = momentum_scores(draws)

    scores = {}

    for n in ALL_NUMBERS:

        if strategy == "hot":

            scores[n] = (
                freq[n] * 0.8 +
                mom[n] * 0.2
            )

        elif strategy == "cold":

            scores[n] = (
                omit[n] * 0.7 +
                mom[n] * 0.3
            )

        elif strategy == "momentum":

            scores[n] = (
                mom[n] * 0.9 +
                freq[n] * 0.1
            )

        elif strategy == "pattern":

            scores[n] = (
                freq[n] * 0.4 +
                omit[n] * 0.3 +
                mom[n] * 0.3
            )

        else:

            scores[n] = (
                freq[n] * 0.4 +
                omit[n] * 0.3 +
                mom[n] * 0.3
            )

    return scores

# =========================================================
# 选号码
# =========================================================

def pick_numbers(scores):

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    mains = [
        x[0]
        for x in ranked[:6]
    ]

    special = ranked[6][0]

    return mains, special

# =========================================================
# 集成策略
# =========================================================

def ensemble_strategy(draws):

    all_scores = []

    for s in ["balanced", "hot", "cold", "momentum", "pattern"]:

        all_scores.append(
            strategy_scores(draws, s)
        )

    final = {
        n: 0
        for n in ALL_NUMBERS
    }

    for sc in all_scores:

        for n, v in sc.items():

            final[n] += v

    return final

# =========================================================
# 下一期
# =========================================================

def next_issue(issue):

    return str(int(issue) + 1)

# =========================================================
# 生成预测
# =========================================================

def generate_predictions(conn):

    rows = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no DESC

    """).fetchall()

    latest_issue = rows[0]["issue_no"]

    next_no = next_issue(latest_issue)

    draws = [
        json.loads(x["numbers_json"])
        for x in rows[:80]
    ]

    strategies = [
        "balanced",
        "hot",
        "cold",
        "momentum",
        "ensemble",
        "pattern"
    ]

    for strategy in strategies:

        if strategy == "ensemble":

            scores = ensemble_strategy(draws)

        else:

            scores = strategy_scores(
                draws,
                strategy
            )

        mains, special = pick_numbers(scores)

        now = utc_now()

        conn.execute("""

        INSERT INTO prediction_runs(
            issue_no,
            strategy,
            status,
            created_at
        )
        VALUES(?,?,?,?)

        ON CONFLICT(issue_no,strategy)
        DO UPDATE SET
            created_at=excluded.created_at

        """, (
            next_no,
            strategy,
            "PENDING",
            now
        ))

        run = conn.execute("""

        SELECT id
        FROM prediction_runs
        WHERE issue_no=?
        AND strategy=?

        """, (
            next_no,
            strategy
        )).fetchone()

        run_id = run["id"]

        conn.execute("""

        DELETE FROM prediction_picks
        WHERE run_id=?

        """, (run_id,))

        for idx, n in enumerate(mains):

            conn.execute("""

            INSERT INTO prediction_picks(
                run_id,
                pick_type,
                number,
                rank,
                score,
                reason
            )
            VALUES(?,?,?,?,?,?)

            """, (
                run_id,
                "MAIN",
                n,
                idx + 1,
                1,
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
        VALUES(?,?,?,?,?,?)

        """, (
            run_id,
            "SPECIAL",
            special,
            1,
            1,
            "特别号"
        ))

    conn.commit()

    return next_no

# =========================================================
# 自动复盘
# =========================================================

def auto_review(conn):

    latest = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 1

    """).fetchone()

    issue = latest["issue_no"]

    winning = set(
        json.loads(latest["numbers_json"])
    )

    sp = latest["special_number"]

    runs = conn.execute("""

    SELECT *
    FROM prediction_runs
    WHERE issue_no=?
    AND status='PENDING'

    """, (issue,)).fetchall()

    for r in runs:

        picks = conn.execute("""

        SELECT *
        FROM prediction_picks
        WHERE run_id=?

        """, (r["id"],)).fetchall()

        mains = [
            x["number"]
            for x in picks
            if x["pick_type"] == "MAIN"
        ]

        special = next(
            (
                x["number"]
                for x in picks
                if x["pick_type"] == "SPECIAL"
            ),
            None
        )

        hit = len(
            set(mains) & winning
        )

        sp_hit = 1 if special == sp else 0

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
            hit,
            hit / 6,
            sp_hit,
            utc_now(),
            r["id"]
        ))

    conn.commit()

# =========================================================
# 波色预测
# =========================================================

def predict_color(specials):

    recent = specials[-10:]

    scores = {
        "红": 0,
        "蓝": 0,
        "绿": 0
    }

    for i, n in enumerate(reversed(recent)):

        w = 10 - i

        scores[get_color(n)] += w

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[0][0], ranked[1][0]

# =========================================================
# 大小单双
# =========================================================

def predict_bs_oe(specials):

    recent = specials[-10:]

    big = sum(
        1 for x in recent
        if x >= 25
    )

    small = len(recent) - big

    odd = sum(
        1 for x in recent
        if x % 2
    )

    even = len(recent) - odd

    bs = "大" if big >= small else "小"

    oe = "单" if odd >= even else "双"

    return bs, oe

# =========================================================
# 最大连空
# =========================================================

def max_miss(specials):

    out = {}

    for c in ["红", "蓝", "绿"]:

        mx = 0
        cur = 0

        for n in specials:

            if get_color(n) != c:

                cur += 1

                mx = max(mx, cur)

            else:

                cur = 0

        out[c] = mx

    return out

# =========================================================
# 投注比例
# =========================================================

def betting_plan(main_color, second_color, bs, oe):

    bankroll = 1000

    return {
        main_color: int(bankroll * 0.45),
        second_color: int(bankroll * 0.15),
        bs: int(bankroll * 0.20),
        oe: int(bankroll * 0.20),
    }

# =========================================================
# 最近10期统计
# =========================================================

def recent_stats(conn):

    rows = conn.execute("""

    SELECT
        strategy,
        COUNT(*) cnt,
        ROUND(AVG(hit_count),2) avg_hit,
        ROUND(AVG(hit_rate)*100,1) rate,
        ROUND(AVG(special_hit)*100,1) sp_rate

    FROM prediction_runs

    WHERE status='REVIEWED'

    GROUP BY strategy

    """).fetchall()

    print("\n最近10期历史命中统计:")

    for r in rows:

        print(
            f"{STRATEGY_LABELS.get(r['strategy'],r['strategy']):<10s}"
            f": 期数={r['cnt']} "
            f"平均命中={r['avg_hit']}个 "
            f"命中率={r['rate']}% "
            f"特别号命中率={r['sp_rate']}%"
        )

# =========================================================
# 主流程
# =========================================================

def sync():

    conn = connect_db()

    init_db(conn)

    draws = fetch_latest()

    save_draws(conn, draws)

    print(f"同步完成: {len(draws)} 条")

    auto_review(conn)

    issue = generate_predictions(conn)

    print(f"\n已生成 {issue} 期预测")

    latest = conn.execute("""

    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 1

    """).fetchone()

    nums = json.loads(
        latest["numbers_json"]
    )

    print("\n最新开奖:")

    print(
        f"{latest['issue_no']} | "
        f"{' '.join(f'{x:02d}' for x in nums)} "
        f"+ {latest['special_number']:02d}"
    )

    print(f"\n预测期号: {issue}")

    runs = conn.execute("""

    SELECT *
    FROM prediction_runs
    WHERE issue_no=?

    """, (issue,)).fetchall()

    for r in runs:

        picks = conn.execute("""

        SELECT *
        FROM prediction_picks
        WHERE run_id=?
        ORDER BY pick_type,rank

        """, (r["id"],)).fetchall()

        mains = [
            f"{x['number']:02d}"
            for x in picks
            if x["pick_type"] == "MAIN"
        ]

        special = next(
            (
                x["number"]
                for x in picks
                if x["pick_type"] == "SPECIAL"
            ),
            None
        )

        print(
            f"{STRATEGY_LABELS.get(r['strategy'],r['strategy']):<10s}"
            f": {' '.join(mains)} + {special:02d}"
        )

        attr = special_attributes(special)

        print(
            f"特码属性: "
            f"{attr['单双']}/"
            f"{attr['大小']} "
            f"{attr['色波']} "
            f"{attr['五行']}"
        )

    specials = [
        x["special_number"]
        for x in conn.execute("""

        SELECT special_number
        FROM draws
        ORDER BY issue_no

        """).fetchall()
    ]

    main_color, second_color = predict_color(specials)

    print("\n特码波色预测:")

    print(
        f"主强: {main_color} "
        f"次强: {second_color}"
    )

    bs, oe = predict_bs_oe(specials)

    print("\n大小单双预测:")

    print(f"大小: {bs}")
    print(f"单双: {oe}")

    miss = max_miss(specials)

    print("\n最大连空:")

    for k, v in miss.items():

        print(f"{k}波: {v} 期")

    print("\n推荐投注方案:")

    plan = betting_plan(
        main_color,
        second_color,
        bs,
        oe
    )

    for k, v in plan.items():

        print(f"{k}: {v} 元")

    recent_stats(conn)

    conn.close()

# =========================================================
# main
# =========================================================

if __name__ == "__main__":

    sync()