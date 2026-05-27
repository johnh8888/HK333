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

# ============================================================
# 数据源
# ============================================================

API_URL = "https://marksix6.net/index.php?api=1"

DB_CONFIG = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db",
}

# ============================================================
# 波色
# ============================================================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

# ============================================================
# 工具
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
    return "单" if n % 2 else "双"

# ============================================================
# 数据库
# ============================================================

def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        period TEXT PRIMARY KEY,
        tm INTEGER
    )
    """)
    conn.commit()

# ============================================================
# 获取数据
# ============================================================

def fetch_json(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urlopen(req, context=ctx, timeout=20) as r:
        return json.loads(r.read().decode())

# ============================================================
# 同步数据
# ============================================================

def sync_data(conn):
    try:
        data = fetch_json(API_URL)
    except Exception as e:
        print(f"[ERROR] 获取数据失败: {e}")
        return

    if not isinstance(data, list):
        print("[ERROR] API返回不是列表")
        return

    added = 0
    updated = 0

    for row in data:

        if not isinstance(row, dict):
            continue

        period = str(row.get("expect", "")).strip()

        opencode = str(row.get("opencode", "")).strip()

        if not period or "+" not in opencode:
            continue

        try:
            tm = int(opencode.split("+")[-1].strip())
        except:
            continue

        cur = conn.execute(
            "SELECT tm FROM lottery WHERE period=?",
            (period,)
        )

        old = cur.fetchone()

        if old is None:
            conn.execute(
                "INSERT INTO lottery VALUES (?, ?)",
                (period, tm)
            )
            added += 1
        else:
            conn.execute(
                "UPDATE lottery SET tm=? WHERE period=?",
                (tm, period)
            )
            updated += 1

    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) FROM lottery"
    ).fetchone()[0]

    print(
        f"同步完成: 总计 {total}, 新增 {added}, 更新 {updated}"
    )

# ============================================================
# 获取特码
# ============================================================

def load_tms(conn):
    rows = conn.execute("""
    SELECT tm
    FROM lottery
    ORDER BY period
    """).fetchall()

    return [r[0] for r in rows]

# ============================================================
# 马尔可夫预测
# ============================================================

def build_model(seq, order=2):

    table = defaultdict(Counter)

    if len(seq) <= order:
        return table

    for i in range(order, len(seq)):

        state = tuple(seq[i-order:i])

        nxt = seq[i]

        age = len(seq) - i

        # 时间衰减
        weight = math.exp(-age / 80)

        table[state][nxt] += weight

    return table

def predict_next(seq, categories, order=2):

    if len(seq) <= order:
        return {}

    model = build_model(seq, order)

    state = tuple(seq[-order:])

    counts = model.get(state)

    if not counts:
        return {}

    total = sum(counts.values())

    probs = {}

    for k, v in counts.items():
        probs[k] = v / total

    return probs

# ============================================================
# 回测
# ============================================================

def backtest(seq, order=2, n=30):

    if len(seq) < order + n + 5:
        return 0.0

    correct = 0
    total = 0

    for i in range(order + 20, len(seq)-1):

        train = seq[:i]

        probs = predict_next(
            train,
            list(set(seq)),
            order
        )

        if not probs:
            continue

        pred = max(probs.items(), key=lambda x: x[1])[0]

        actual = seq[i]

        if pred == actual:
            correct += 1

        total += 1

    if total == 0:
        return 0.0

    return correct / total

# ============================================================
# 显示预测
# ============================================================

def show_prediction(conn, name, order, backtest_n):

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

    color_seq = [get_color(x) for x in tms]
    size_seq = [get_size(x) for x in tms]
    odd_seq = [get_odd_even(x) for x in tms]

    attrs = {
        "color": color_seq,
        "size": size_seq,
        "odd_even": odd_seq,
    }

    print()
    print("========== 下一期预测 ==========")

    avg_conf = []

    for key, seq in attrs.items():

        probs = predict_next(
            seq,
            list(set(seq)),
            order
        )

        if not probs:
            continue

        print()
        print(key)

        sorted_probs = sorted(
            probs.items(),
            key=lambda x: x[1],
            reverse=True
        )

        best = sorted_probs[0][0]

        avg_conf.append(sorted_probs[0][1])

        for k, v in sorted_probs:

            flag = "✓" if k == best else ""

            print(f"{k}: {v:.2%} {flag}")

    print()
    print("========== 元决策 ==========")

    if avg_conf:
        avg = sum(avg_conf) / len(avg_conf)
    else:
        avg = 0

    decision = "出手" if avg >= 0.55 else "观望"

    print(f"建议: {decision}")
    print(f"平均置信度: {avg:.3f}")

    print()
    print("========== 回测结果 ==========")

    for key, seq in attrs.items():

        acc = backtest(
            seq,
            order,
            backtest_n
        )

        print(
            f"{key} | 准确率={acc:.3f}"
        )

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

    for name, db_file in DB_CONFIG.items():

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        conn = sqlite3.connect(db_file)

        init_db(conn)

        sync_data(conn)

        show_prediction(
            conn,
            name,
            args.order,
            args.backtest
        )

        conn.close()

if __name__ == "__main__":
    main()