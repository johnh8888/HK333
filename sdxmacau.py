#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import math
import sqlite3
from collections import Counter, defaultdict

# ============================================================
# 彩种配置
# ============================================================

LOTTERIES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

# ============================================================
# 波色
# ============================================================

RED = {
    1,2,7,8,12,13,18,19,23,24,
    29,30,34,35,40,45,46
}

BLUE = {
    3,4,9,10,14,15,20,25,26,
    31,36,37,41,42,47,48
}

GREEN = {
    5,6,11,16,17,21,22,27,
    28,32,33,38,39,43,44,49
}

# ============================================================
# 属性函数
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
# 获取特码序列
# ============================================================

def get_tm_series(conn):

    cur = conn.cursor()

    # 自动识别字段
    possible_tables = [
        ("records", "tm"),
        ("lottery", "tm"),
        ("data", "tm"),
        ("records", "special"),
    ]

    for table, col in possible_tables:

        try:

            cur.execute(f"""
            SELECT {col}
            FROM {table}
            ORDER BY rowid
            """)

            rows = cur.fetchall()

            if rows:

                result = []

                for x in rows:

                    try:
                        n = int(x[0])

                        if 1 <= n <= 49:
                            result.append(n)

                    except:
                        pass

                if result:
                    return result

        except:
            pass

    return []


# ============================================================
# Markov预测
# ============================================================

def markov_predict(seq, order=2):

    if len(seq) <= order:
        return {}

    trans = defaultdict(Counter)

    for i in range(order, len(seq)):

        state = tuple(seq[i-order:i])

        target = seq[i]

        age = len(seq) - i

        # 指数衰减
        weight = math.exp(-age / 80)

        trans[state][target] += weight

    current = tuple(seq[-order:])

    if current not in trans:
        return {}

    cnt = trans[current]

    total = sum(cnt.values())

    return {
        k: v / total
        for k, v in cnt.items()
    }


# ============================================================
# 特征预测
# ============================================================

def predict_feature(tms, func, order):

    seq = [func(x) for x in tms]

    return markov_predict(seq, order)


# ============================================================
# LogLoss
# ============================================================

def log_loss(prob):

    eps = 1e-15

    p = max(min(prob, 1 - eps), eps)

    return -math.log(p)


# ============================================================
# 回测
# ============================================================

def backtest(tms, func, order):

    seq = [func(x) for x in tms]

    correct = 0

    total = 0

    losses = []

    for i in range(order, len(seq)-1):

        train = seq[:i]

        real = seq[i]

        pred = markov_predict(train, order)

        if not pred:
            continue

        best = max(
            pred.items(),
            key=lambda x: x[1]
        )[0]

        if best == real:
            correct += 1

        prob = pred.get(real, 0.0001)

        losses.append(log_loss(prob))

        total += 1

    if total == 0:
        return None

    return {
        "acc": correct / total,
        "loss": sum(losses) / total
    }


# ============================================================
# 显示预测
# ============================================================

def show_prediction(name, conn, order):

    tms = get_tm_series(conn)

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    if not tms:

        print("数据库没有数据")

        return

    latest = tms[-1]

    print(f"历史数据量: {len(tms)}")
    print(f"最新特码: {latest}")

    features = {
        "color": get_color,
        "size": get_size,
        "odd_even": get_odd_even
    }

    avg_conf = []

    print()
    print("========== 下一期预测 ==========")

    for fname, func in features.items():

        print()
        print(fname)

        pred = predict_feature(
            tms,
            func,
            order
        )

        if not pred:

            print("无预测")

            continue

        sorted_pred = sorted(
            pred.items(),
            key=lambda x: x[1],
            reverse=True
        )

        best_conf = sorted_pred[0][1]

        avg_conf.append(best_conf)

        for i, (k, v) in enumerate(sorted_pred):

            flag = " ✓" if i == 0 else ""

            print(f"{k}: {v:.2%}{flag}")

    avg = (
        sum(avg_conf) / len(avg_conf)
        if avg_conf else 0
    )

    print()
    print("========== 元决策 ==========")

    if avg >= 0.65:
        decision = "强信号"

    elif avg >= 0.55:
        decision = "可关注"

    else:
        decision = "观望"

    print(f"建议: {decision}")
    print(f"平均置信度: {avg:.3f}")

    print()
    print("========== 回测结果 ==========")

    for fname, func in features.items():

        bt = backtest(
            tms,
            func,
            order
        )

        if not bt:
            continue

        print(
            f"{fname} | "
            f"准确率={bt['acc']:.3f} | "
            f"LogLoss={bt['loss']:.4f}"
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

    args = parser.parse_args()

    for name, dbfile in LOTTERIES.items():

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        if not sqlite3.connect:

            print("SQLite不可用")

            continue

        try:

            conn = sqlite3.connect(dbfile)

        except Exception as e:

            print(f"数据库打开失败: {e}")

            continue

        show_prediction(
            name,
            conn,
            args.order
        )

        conn.close()


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    main()