# ================================
# 新澳门六合彩 AI 预测系统 Pro
# 修复版 + 真实历史回测 + 波色/大小单双 + 收益统计
# Python 3.11+
# ================================

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from urllib.request import Request, urlopen

# ================================
# 基础配置
# ================================

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = str(SCRIPT_DIR / "new_macau.db")

API_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

BET_PER_ROUND = 1000

ODDS = {
    "红": 2.7,
    "蓝": 2.8,
    "绿": 2.8,
    "大": 1.95,
    "小": 1.95,
    "单": 1.95,
    "双": 1.95,
}

STRATEGIES = [
    "balanced",
    "hot",
    "cold",
    "momentum",
]

# ================================
# 数据结构
# ================================

@dataclass
class Draw:
    issue: str
    date: str
    nums: List[int]
    special: int

# ================================
# 工具函数
# ================================

def utc():
    return datetime.utcnow().isoformat()

def color(num: int):
    if 1 <= num <= 16:
        return "红"
    elif 17 <= num <= 32:
        return "蓝"
    return "绿"

def size(num: int):
    return "大" if num >= 25 else "小"

def odd_even(num: int):
    return "单" if num % 2 else "双"

def wuxing(num: int):
    tail = num % 10
    if tail in [1, 6]:
        return "水"
    if tail in [2, 7]:
        return "火"
    if tail in [3, 8]:
        return "木"
    if tail in [4, 9]:
        return "金"
    return "土"

def attrs(num: int):
    return {
        "单双": odd_even(num),
        "大小": size(num),
        "色波": color(num),
        "五行": wuxing(num),
    }

# ================================
# 数据库
# ================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special INTEGER
    );

    CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT,
        strategy TEXT,
        main_json TEXT,
        special INTEGER,
        hit INTEGER,
        reviewed INTEGER DEFAULT 0,
        created_at TEXT
    );

    """)

    conn.commit()

# ================================
# 获取最新数据
# ================================

def fetch_data():

    req = Request(
        API_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())

    records = []

    target = None

    for item in data["lottery_data"]:
        if item["name"] == "新澳门彩":
            target = item
            break

    if not target:
        raise RuntimeError("找不到新澳门彩数据")

    history = target["history"]

    for idx, row in enumerate(history):

        parts = row.split("期：")

        issue = parts[0].strip()

        nums = [int(x) for x in parts[1].split(",")]

        records.append(
            Draw(
                issue=issue,
                date="",
                nums=nums[:6],
                special=nums[6]
            )
        )

    return records

# ================================
# 保存数据
# ================================

def save_draws(conn, draws):

    for d in draws:

        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES(?,?,?,?)
        """, (
            d.issue,
            d.date,
            json.dumps(d.nums),
            d.special
        ))

    conn.commit()

# ================================
# 统计
# ================================

def freq(draws):

    f = Counter()

    for d in draws:
        for n in d:
            f[n] += 1

    return f

def omission(draws):

    # 修复版 omission
    result = {}

    for n in ALL_NUMBERS:

        miss = 0

        found = False

        for draw in draws:

            if n in draw:
                found = True
                break

            miss += 1

        if not found:
            miss = len(draws)

        result[n] = miss

    return result

def momentum(draws):

    result = defaultdict(float)

    for i, draw in enumerate(draws):

        w = 1 / (i + 1)

        for n in draw:
            result[n] += w

    return result

# ================================
# 策略
# ================================

def score_numbers(draws, strategy):

    f = freq(draws)

    o = omission(draws)

    m = momentum(draws)

    scores = {}

    for n in ALL_NUMBERS:

        if strategy == "hot":
            s = f[n] * 0.8 + m[n] * 0.2

        elif strategy == "cold":
            s = o[n] * 0.7 + m[n] * 0.3

        elif strategy == "momentum":
            s = m[n]

        else:
            s = (
                f[n] * 0.4 +
                o[n] * 0.3 +
                m[n] * 0.3
            )

        scores[n] = s

    return scores

def pick(scores):

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    mains = [x[0] for x in ranked[:6]]

    special = None

    for n, _ in ranked:

        if n not in mains:
            special = n
            break

    return mains, special

# ================================
# 波色预测
# ================================

def predict_color(specials):

    recent = specials[-10:]

    score = defaultdict(float)

    for i, n in enumerate(reversed(recent)):

        w = 10 - i

        score[color(n)] += w

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[0][0], ranked[1][0]

# ================================
# 大小单双预测
# ================================

def predict_size(specials):

    recent = specials[-10:]

    c = Counter(size(x) for x in recent)

    return c.most_common(1)[0][0]

def predict_odd_even(specials):

    recent = specials[-10:]

    c = Counter(odd_even(x) for x in recent)

    return c.most_common(1)[0][0]

# ================================
# 最大连空
# ================================

def max_miss(values, target):

    best = 0
    current = 0

    for v in values:

        if v != target:
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best

# ================================
# 收益计算
# ================================

def calc_profit(pred, actual, odds, bet):

    if pred == actual:
        return bet * (odds - 1)

    return -bet

# ================================
# 历史真实回测
# ================================

def backtest(conn):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue ASC
    """).fetchall()

    all_draws = []

    for r in rows:

        all_draws.append({
            "issue": r["issue"],
            "nums": json.loads(r["numbers_json"]),
            "special": r["special"]
        })

    if len(all_draws) < 30:
        return

    conn.execute("DELETE FROM predictions")

    bankroll = 0

    for idx in range(20, len(all_draws)):

        history = all_draws[:idx]

        target = all_draws[idx]

        history_nums = [x["nums"] for x in reversed(history)]

        specials = [x["special"] for x in history]

        for strategy in STRATEGIES:

            scores = score_numbers(
                history_nums,
                strategy
            )

            mains, special = pick(scores)

            hit = len(
                set(mains) &
                set(target["nums"])
            )

            conn.execute("""
            INSERT INTO predictions(
                issue,
                strategy,
                main_json,
                special,
                hit,
                reviewed,
                created_at
            )
            VALUES(?,?,?,?,?,?,?)
            """, (
                target["issue"],
                strategy,
                json.dumps(mains),
                special,
                hit,
                1,
                utc()
            ))

        # 波色收益

        main_color, second_color = predict_color(specials)

        actual_color = color(target["special"])

        bankroll += calc_profit(
            main_color,
            actual_color,
            ODDS[main_color],
            300
        )

        # 大小

        size_pred = predict_size(specials)

        bankroll += calc_profit(
            size_pred,
            size(target["special"]),
            1.95,
            350
        )

        # 单双

        oe_pred = predict_odd_even(specials)

        bankroll += calc_profit(
            oe_pred,
            odd_even(target["special"]),
            1.95,
            350
        )

    conn.commit()

    print(f"\n累计收益: {bankroll:.2f}")

# ================================
# 显示 Dashboard
# ================================

def dashboard(conn):

    row = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT 1
    """).fetchone()

    latest_nums = json.loads(row["numbers_json"])

    print("\n最新开奖:")
    print(
        row["issue"],
        "|",
        " ".join(f"{x:02d}" for x in latest_nums),
        "+",
        f"{row['special']:02d}"
    )

    draws = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue DESC
    LIMIT 100
    """).fetchall()

    history_nums = [
        json.loads(x["numbers_json"])
        for x in draws
    ]

    specials = [
        x["special"]
        for x in reversed(draws)
    ]

    next_issue = str(int(row["issue"]) + 1)

    print(f"\n预测期号: {next_issue}")

    for s in STRATEGIES:

        scores = score_numbers(history_nums, s)

        mains, special = pick(scores)

        print(
            f"{s:<10}:",
            " ".join(f"{x:02d}" for x in mains),
            "+",
            f"{special:02d}"
        )

        a = attrs(special)

        print(
            f"特码属性: "
            f"{a['单双']}/"
            f"{a['大小']} "
            f"{a['色波']} "
            f"{a['五行']}"
        )

    # 波色

    mc, sc = predict_color(specials)

    print("\n特码波色预测:")
    print(f"主强: {mc} 次强: {sc}")

    # 大小单双

    print("\n大小单双预测:")

    print(
        "大小:",
        predict_size(specials)
    )

    print(
        "单双:",
        predict_odd_even(specials)
    )

    # 最大连空

    colors = [color(x) for x in specials]

    print("\n最大连空:")

    for c in ["红", "蓝", "绿"]:

        print(
            f"{c}波:",
            max_miss(colors, c),
            "期"
        )

    # 最近10期统计

    stats = conn.execute("""

    SELECT
    strategy,
    COUNT(*) c,
    ROUND(AVG(hit),2) avg_hit

    FROM predictions

    WHERE reviewed=1

    GROUP BY strategy

    """).fetchall()

    print("\n最近历史命中统计:")

    for s in stats:

        print(
            f"{s['strategy']:<10}: "
            f"期数={s['c']} "
            f"平均命中={s['avg_hit']}"
        )

# ================================
# 主流程
# ================================

def sync():

    conn = db()

    init_db(conn)

    draws = fetch_data()

    save_draws(conn, draws)

    print(f"同步完成: {len(draws)} 条")

    backtest(conn)

    dashboard(conn)

    conn.close()

# ================================
# CLI
# ================================

def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync")

    args = parser.parse_args()

    if args.cmd == "sync":
        sync()

if __name__ == "__main__":
    main()