#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import ssl
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

# ============================================================
# 配置
# ============================================================

LOTTERIES = {
    "老澳门彩": {
        "db": "old_macau.db",
        "url": "https://www.macaumarksix.com/api/macaujc.com",
    },
    "香港彩": {
        "db": "hk_macau.db",
        "url": "https://www.macaumarksix.com/api/hkjc.com",
    },
    "新澳门彩": {
        "db": "xin_macau.db",
        "url": "https://www.macaumarksix.com/api/macaujc2.com",
    },
}

# ============================================================
# 波色
# ============================================================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
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

def color_of(n: int) -> str:
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"


def size_of(n: int) -> str:
    return "大" if n >= 25 else "小"


def odd_even_of(n: int) -> str:
    return "单" if n % 2 else "双"


# ============================================================
# 数据库
# ============================================================

def init_db(conn):

    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        period TEXT PRIMARY KEY,
        code TEXT,
        tm INTEGER
    )
    """)

    conn.commit()


# ============================================================
# 拉取 JSON
# ============================================================

def fetch_json(url):

    ctx = ssl._create_unverified_context()

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(req, context=ctx, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# ============================================================
# 同步数据
# ============================================================

def sync_data(conn, url):

    try:
        data = fetch_json(url)
    except Exception as e:
        print(f"同步失败: {e}")
        return

    inserted = 0
    updated = 0

    for row in data:

        period = str(row.get("expect", ""))

        opentime = row.get("opencode", "")

        if "+" not in opentime:
            continue

        try:
            tm = int(opentime.split("+")[-1].strip())
        except:
            continue

        cur = conn.execute(
            "SELECT period FROM lottery WHERE period=?",
            (period,)
        ).fetchone()

        if cur:
            conn.execute(
                """
                UPDATE lottery
                SET code=?, tm=?
                WHERE period=?
                """,
                (opentime, tm, period)
            )
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO lottery(period, code, tm)
                VALUES (?, ?, ?)
                """,
                (period, opentime, tm)
            )
            inserted += 1

    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) FROM lottery"
    ).fetchone()[0]

    print(f"同步完成 总={total} 新增={inserted} 更新={updated}")


# ============================================================
# 读取数据
# ============================================================

def load_tm(conn):

    rows = conn.execute("""
    SELECT tm
    FROM lottery
    ORDER BY period
    """).fetchall()

    return [x[0] for x in rows]


# ============================================================
# 时序权重
# ============================================================

def time_weight(age):

    return math.exp(-age / 80)


# ============================================================
# 马尔可夫预测
# ============================================================

def markov_predict(seq, order=2):

    if len(seq) <= order:
        return {}

    counts = defaultdict(float)

    history = tuple(seq[-order:])

    for i in range(order, len(seq)):

        prev = tuple(seq[i-order:i])

        if prev != history:
            continue

        nxt = seq[i]

        age = len(seq) - i

        w = time_weight(age)

        counts[nxt] += w

    total = sum(counts.values())

    if total <= 0:

        uniq = sorted(set(seq))

        p = 1 / len(uniq)

        return {k: p for k in uniq}

    return {
        k: v / total
        for k, v in counts.items()
    }


# ============================================================
# 分类序列
# ============================================================

def build_features(tms):

    return {
        "color": [color_of(x) for x in tms],
        "size": [size_of(x) for x in tms],
        "odd_even": [odd_even_of(x) for x in tms],
    }


# ============================================================
# 回测
# ============================================================

def backtest(seq, order=2, n=100):

    if len(seq) < order + n + 5:
        return None

    ok = 0
    total_loss = 0

    for i in range(len(seq)-n, len(seq)):

        train = seq[:i]

        real = seq[i]

        probs = markov_predict(train, order)

        pred = max(probs.items(), key=lambda x: x[1])[0]

        if pred == real:
            ok += 1

        p = probs.get(real, 1e-9)

        total_loss += -math.log(p)

    return {
        "acc": ok / n,
        "logloss": total_loss / n
    }


# ============================================================
# 输出预测
# ============================================================

def show_prediction(name, tms, order=2, bt=100):

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    latest = tms[-1]

    print(f"最新特码: {latest}")

    if len(tms) < 30:
        print("数据不足，跳过预测")
        return

    feats = build_features(tms)

    results = {}

    print()
    print("========== 下一期预测 ==========")

    for k, seq in feats.items():

        probs = markov_predict(seq, order)

        probs = sorted(
            probs.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print()
        print(k)

        for label, p in probs:

            flag = "✓" if p == probs[0][1] else ""

            print(f"{label}: {p*100:.2f}% {flag}")

        results[k] = probs[0][1]

    avg_conf = sum(results.values()) / len(results)

    print()
    print("========== 元决策 ==========")

    advice = "下注" if avg_conf >= 0.58 else "观望"

    print(f"建议: {advice}")
    print(f"平均置信度: {avg_conf:.3f}")

    print()
    print("========== 回测结果 ==========")

    for k, seq in feats.items():

        r = backtest(seq, order, bt)

        if not r:
            continue

        print(
            f"{k} | "
            f"准确率={r['acc']:.3f} | "
            f"LogLoss={r['logloss']:.4f}"
        )


# ============================================================
# 主程序
# ============================================================

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--lottery",
        choices=list(LOTTERIES.keys())
    )

    ap.add_argument(
        "--order",
        type=int,
        default=2
    )

    ap.add_argument(
        "--backtest",
        type=int,
        default=100
    )

    args = ap.parse_args()

    targets = (
        [args.lottery]
        if args.lottery
        else list(LOTTERIES.keys())
    )

    for name in targets:

        cfg = LOTTERIES[name]

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        conn = sqlite3.connect(cfg["db"])

        init_db(conn)

        sync_data(conn, cfg["url"])

        tms = load_tm(conn)

        show_prediction(
            name,
            tms,
            order=args.order,
            bt=args.backtest
        )

        conn.close()


if __name__ == "__main__":
    main()