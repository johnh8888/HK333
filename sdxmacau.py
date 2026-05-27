#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import ssl
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ============================================================
# 基础配置
# ============================================================

LOTTERIES = {
    "老澳门彩": {
        "db": "old_macau.db",
    },
    "香港彩": {
        "db": "hk_macau.db",
    },
    "新澳门彩": {
        "db": "xin_macau.db",
    },
}

API_URL = "https://marksix6.net/index.php?api=1"

RED = {
    1, 2, 7, 8, 12, 13, 18, 19, 23, 24,
    29, 30, 34, 35, 40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25, 26,
    31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27, 28,
    32, 33, 38, 39, 43, 44, 49
}

# ============================================================
# 工具函数
# ============================================================

def get_color(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_size(n):
    return "大" if n >= 25 else "小"

def get_odd_even(n):
    return "双" if n % 2 == 0 else "单"

def fetch_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    req = Request(url, headers=headers)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(req, timeout=20, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        print(f"[ERROR] HTTP错误: {e.code}")
    except URLError as e:
        print(f"[ERROR] 网络错误: {e}")
    except Exception as e:
        print(f"[ERROR] 获取失败: {e}")

    return None

# ============================================================
# 数据库
# ============================================================

def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS marksix (
        period TEXT PRIMARY KEY,
        tm INTEGER
    )
    """)
    conn.commit()

def sync_data(conn):
    data = fetch_json(API_URL)

    if not data:
        print("[WARN] API 获取失败，继续使用本地数据库")
        return

    added = 0
    updated = 0

    for row in data:

        period = str(row.get("expect", ""))

        opencode = row.get("opencode", "")

        if "+" not in opencode:
            continue

        try:
            tm = int(opencode.split("+")[-1].strip())
        except:
            continue

        cur = conn.execute(
            "SELECT tm FROM marksix WHERE period=?",
            (period,)
        ).fetchone()

        if cur is None:
            conn.execute(
                "INSERT INTO marksix(period, tm) VALUES(?, ?)",
                (period, tm)
            )
            added += 1
        else:
            conn.execute(
                "UPDATE marksix SET tm=? WHERE period=?",
                (tm, period)
            )
            updated += 1

    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) FROM marksix"
    ).fetchone()[0]

    print(
        f"同步完成: 总计 {total}, 新增 {added}, 更新 {updated}"
    )

# ============================================================
# 获取特码序列
# ============================================================

def load_tms(conn):

    rows = conn.execute("""
    SELECT tm
    FROM marksix
    ORDER BY period
    """).fetchall()

    return [x[0] for x in rows]

# ============================================================
# 时间衰减 Markov
# ============================================================

def build_markov(seq, order=2):

    table = defaultdict(Counter)

    for i in range(order, len(seq)):

        state = tuple(seq[i-order:i])

        nxt = seq[i]

        age = len(seq) - i

        weight = math.exp(-age / 80)

        table[state][nxt] += weight

    return table

def predict_next(seq, mapper, labels, order=2):

    if len(seq) <= order:
        return None

    mapped = [mapper(x) for x in seq]

    table = build_markov(mapped, order)

    state = tuple(mapped[-order:])

    counts = table.get(state)

    if not counts:
        return None

    total = sum(counts.values())

    probs = {}

    for k, v in counts.items():
        probs[k] = v / total

    for label in labels:
        probs.setdefault(label, 0.0)

    return sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

# ============================================================
# 回测
# ============================================================

def backtest(seq, mapper, labels, order=2, backtest=30):

    if len(seq) < order + backtest + 1:
        return 0.0, 0.0

    mapped = [mapper(x) for x in seq]

    correct = 0
    brier_total = 0
    total = 0

    for i in range(order, len(mapped)-1):

        if i < len(mapped) - backtest:
            continue

        train = mapped[:i]

        target = mapped[i]

        table = build_markov(train, order)

        state = tuple(train[-order:])

        counts = table.get(state)

        if not counts:
            continue

        s = sum(counts.values())

        probs = {}

        for k, v in counts.items():
            probs[k] = v / s

        pred = max(probs.items(), key=lambda x: x[1])[0]

        if pred == target:
            correct += 1

        brier = 0

        for lab in labels:
            p = probs.get(lab, 0)
            y = 1 if lab == target else 0
            brier += (p - y) ** 2

        brier_total += brier

        total += 1

    if total == 0:
        return 0.0, 0.0

    return correct / total, brier_total / total

# ============================================================
# 展示预测
# ============================================================

def show_prediction(name, conn, order, backtest_n):

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    tms = load_tms(conn)

    if not tms:
        print("数据库没有数据")
        return

    latest = tms[-1]

    print(f"最新特码: {latest}")
    print()

    attrs = {
        "color": (
            get_color,
            ["红", "蓝", "绿"]
        ),
        "size": (
            get_size,
            ["大", "小"]
        ),
        "odd_even": (
            get_odd_even,
            ["单", "双"]
        )
    }

    print("========== 下一期预测 ==========")

    avg_acc = []

    for key, (mapper, labels) in attrs.items():

        print()
        print(f"{key}:")

        pred = predict_next(
            tms,
            mapper,
            labels,
            order
        )

        if pred:

            for i, (k, v) in enumerate(pred):

                flag = " ✓" if i == 0 else ""

                print(f"   {k}: {v*100:.1f}%{flag}")

        acc, brier = backtest(
            tms,
            mapper,
            labels,
            order,
            backtest_n
        )

        avg_acc.append(acc)

        print(
            f"   回测准确率: {acc*100:.1f}%"
            f"  Brier: {brier:.4f}"
        )

    avg = sum(avg_acc) / len(avg_acc)

    print()
    print("========== 元决策 ==========")

    if avg >= 0.55:
        print("建议: 出手")
    else:
        print("建议: 观望")

    print(f"平均准确率: {avg*100:.1f}%")

# ============================================================
# 主程序
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--order",
        type=int,
        default=2
    )

    parser.add_argument(
        "--backtest",
        type=int,
        default=30
    )

    args = parser.parse_args()

    for name, cfg in LOTTERIES.items():

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        db_path = Path(cfg["db"])

        conn = sqlite3.connect(db_path)

        init_db(conn)

        sync_data(conn)

        show_prediction(
            name,
            conn,
            args.order,
            args.backtest
        )

        conn.close()

if __name__ == "__main__":
    main()