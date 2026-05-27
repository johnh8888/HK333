#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import ssl
import warnings

from collections import defaultdict, Counter
from pathlib import Path
from urllib.request import Request, urlopen

warnings.filterwarnings("ignore")

# =========================================================
# 配置
# =========================================================

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
        "url": "https://www.macaumarksix.com/api/macaukj.com",
    },
}

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
# 工具
# =========================================================

def get_color(num):

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"


def get_size(num):

    return "大" if num >= 25 else "小"


def get_odd_even(num):

    return "单" if num % 2 else "双"


# =========================================================
# 数据库
# =========================================================

def init_db(db_path):

    conn = sqlite3.connect(db_path)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        period TEXT PRIMARY KEY,
        numbers TEXT
    )
    """)

    conn.commit()

    return conn


# =========================================================
# 下载数据
# =========================================================

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


# =========================================================
# 同步数据
# =========================================================

def sync_data(conn, url):

    data = fetch_json(url)

    total = 0
    added = 0
    updated = 0

    for row in data:

        period = str(row.get("expect", ""))

        open_code = row.get("openCode", "")

        if not period or not open_code:
            continue

        numbers = [
            int(x)
            for x in open_code.replace("+", ",").split(",")
            if x.strip().isdigit()
        ]

        if len(numbers) < 7:
            continue

        total += 1

        cur = conn.execute(
            "SELECT period FROM lottery WHERE period=?",
            (period,)
        )

        exists = cur.fetchone()

        if exists:

            conn.execute(
                "UPDATE lottery SET numbers=? WHERE period=?",
                (json.dumps(numbers), period)
            )

            updated += 1

        else:

            conn.execute(
                "INSERT INTO lottery VALUES (?, ?)",
                (period, json.dumps(numbers))
            )

            added += 1

    conn.commit()

    print(f"同步完成 总={total} 新增={added} 更新={updated}")


# =========================================================
# 读取特码序列
# =========================================================

def load_sequence(conn, attr_func):

    rows = conn.execute("""
    SELECT period, numbers
    FROM lottery
    ORDER BY period
    """).fetchall()

    seq = []

    for _, numbers_json in rows:

        nums = json.loads(numbers_json)

        tema = nums[-1]

        seq.append(attr_func(tema))

    return seq


# =========================================================
# Markov 模型
# =========================================================

def build_markov_model(sequence, order=2):

    transition = defaultdict(lambda: defaultdict(float))

    n = len(sequence)

    if n <= order:
        return {}

    for idx in range(order, n):

        state = tuple(sequence[idx-order:idx])

        nxt = sequence[idx]

        # EMA80
        age = n - idx - 1

        weight = math.exp(-age / 80)

        transition[state][nxt] += weight

    model = {}

    for state, next_counts in transition.items():

        total = sum(next_counts.values())

        keys = list(next_counts.keys())

        k = len(keys)

        probs = {}

        for key in keys:

            # 拉普拉斯平滑
            probs[key] = (
                next_counts[key] + 1
            ) / (
                total + k
            )

        # 归一化
        s = sum(probs.values())

        for key in probs:
            probs[key] /= s

        model[state] = probs

    return model


# =========================================================
# 预测
# =========================================================

def predict_next(sequence, order=2):

    if len(sequence) <= order:
        return {}

    model = build_markov_model(sequence, order)

    state = tuple(sequence[-order:])

    if state not in model:
        return {}

    probs = dict(model[state])

    # 热度修正
    recent = sequence[-50:]

    freq = Counter(recent)

    for k in probs:

        hot = freq.get(k, 0)

        probs[k] *= 1 / (1 + hot * 0.03)

    # 重新归一化
    total = sum(probs.values())

    for k in probs:
        probs[k] /= total

    return dict(
        sorted(
            probs.items(),
            key=lambda x: x[1],
            reverse=True
        )
    )


# =========================================================
# 回测
# =========================================================

def backtest(sequence, order=2, window=100):

    if len(sequence) < order + window + 5:
        return None

    start = len(sequence) - window

    correct = 0

    logloss = 0

    total = 0

    for i in range(start, len(sequence)-1):

        train = sequence[:i]

        target = sequence[i]

        pred = predict_next(train, order)

        if not pred:
            continue

        best = max(pred, key=pred.get)

        if best == target:
            correct += 1

        p = pred.get(target, 1e-9)

        logloss += -math.log(p)

        total += 1

    if total == 0:
        return None

    return {
        "accuracy": correct / total,
        "logloss": logloss / total
    }


# =========================================================
# 显示
# =========================================================

def show_prediction(name, conn, order, backtest_n):

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    row = conn.execute("""
    SELECT period, numbers
    FROM lottery
    ORDER BY period DESC
    LIMIT 1
    """).fetchone()

    if not row:
        return

    period, numbers_json = row

    nums = json.loads(numbers_json)

    tema = nums[-1]

    print(f"最新特码: {tema}")

    attrs = {
        "color": get_color,
        "size": get_size,
        "odd_even": get_odd_even,
    }

    results = {}

    print()
    print("========== 下一期预测 ==========")

    for key, func in attrs.items():

        seq = load_sequence(conn, func)

        pred = predict_next(seq, order)

        if not pred:
            continue

        print()
        print(key)

        for k, v in pred.items():

            mark = "✓" if v == max(pred.values()) else ""

            print(f"{k}: {v*100:.2f}% {mark}")

        results[key] = max(pred.values())

    print()
    print("========== 元决策 ==========")

    avg_conf = (
        sum(results.values()) / len(results)
        if results else 0
    )

    if avg_conf < 0.55:
        advice = "观望"
    elif avg_conf < 0.65:
        advice = "小注"
    else:
        advice = "强信号"

    print(f"建议: {advice}")
    print(f"平均置信度: {avg_conf:.3f}")

    print()
    print("========== 回测结果 ==========")

    for key, func in attrs.items():

        seq = load_sequence(conn, func)

        bt = backtest(
            seq,
            order=order,
            window=backtest_n
        )

        if not bt:
            continue

        print(
            f"{key} | "
            f"准确率={bt['accuracy']:.3f} | "
            f"LogLoss={bt['logloss']:.4f}"
        )


# =========================================================
# 主程序
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=list(LOTTERIES.keys())
    )

    parser.add_argument(
        "--order",
        type=int,
        default=2
    )

    parser.add_argument(
        "--backtest",
        type=int,
        default=100
    )

    args = parser.parse_args()

    selected = (
        [args.lottery]
        if args.lottery
        else list(LOTTERIES.keys())
    )

    for name in selected:

        cfg = LOTTERIES[name]

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        conn = init_db(cfg["db"])

        sync_data(conn, cfg["url"])

        show_prediction(
            name,
            conn,
            args.order,
            args.backtest
        )

        conn.close()


if __name__ == "__main__":
    main()