#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
V9 AI 时序稳定版
-----------------------------------
特点：
1. 多接口容灾
2. 空数据保护
3. 指数时间衰减
4. Markov 时序预测
5. 自动回测
6. GitHub Actions 稳定运行
7. 无 scipy 依赖
8. Python 3.10+ 可运行
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import ssl
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ============================================================
# 彩种配置
# ============================================================

LOTTERIES = {
    "老澳门彩": {
        "db": "old_macau.db",
        "urls": [
            "https://www.macaumarksix.com/api/macau",
        ]
    },
    "香港彩": {
        "db": "hk_macau.db",
        "urls": [
            "https://www.macaumarksix.com/api/hk"
        ]
    },
    "新澳门彩": {
        "db": "xin_macau.db",
        "urls": [
            "https://www.macaumarksix.com/api/xin"
        ]
    }
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
    3, 4, 9, 10, 14, 15, 20,
    25, 26, 31, 36, 37, 41,
    42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}

# ============================================================
# 工具函数
# ============================================================

def get_color(n: int) -> str:
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"


def get_size(n: int) -> str:
    return "大" if n >= 25 else "小"


def get_odd_even(n: int) -> str:
    return "单" if n % 2 else "双"


# ============================================================
# 数据库
# ============================================================

def init_db(conn):
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS records(
        issue TEXT PRIMARY KEY,
        tm INTEGER
    )
    """)

    conn.commit()


# ============================================================
# 获取 JSON（稳定版）
# ============================================================

def fetch_json(url):

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    req = Request(url, headers=headers)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(req, context=ctx, timeout=20) as r:

            text = r.read().decode("utf-8")

            if not text.strip():
                print(f"[WARN] 接口空数据: {url}")
                return []

            return json.loads(text)

    except HTTPError as e:
        print(f"[ERROR] HTTP错误 {e.code}: {url}")

    except URLError as e:
        print(f"[ERROR] 网络错误: {e}")

    except Exception as e:
        print(f"[ERROR] 获取失败: {e}")

    return []


# ============================================================
# 多接口容灾
# ============================================================

def fetch_from_urls(urls):

    for url in urls:

        data = fetch_json(url)

        if data:
            print(f"[OK] 接口成功: {url}")
            return data

    print("[WARN] 所有接口失败")
    return []


# ============================================================
# 同步数据
# ============================================================

def sync_data(conn, urls):

    data = fetch_from_urls(urls)

    if not data:
        print("[WARN] 本次未获取到数据")
        return

    cur = conn.cursor()

    added = 0
    updated = 0

    for row in data:

        issue = str(
            row.get("expect")
            or row.get("issue")
            or row.get("period")
            or ""
        )

        tm = (
            row.get("openCode")
            or row.get("opencode")
            or row.get("tm")
            or ""
        )

        if not issue or not tm:
            continue

        try:
            if isinstance(tm, str):

                if "+" in tm:
                    tm = tm.split("+")[-1].strip()

                elif "," in tm:
                    tm = tm.split(",")[-1].strip()

            tm = int(tm)

        except:
            continue

        cur.execute(
            "SELECT issue FROM records WHERE issue=?",
            (issue,)
        )

        old = cur.fetchone()

        if old:

            cur.execute(
                "UPDATE records SET tm=? WHERE issue=?",
                (tm, issue)
            )

            updated += 1

        else:

            cur.execute(
                "INSERT INTO records(issue, tm) VALUES(?, ?)",
                (issue, tm)
            )

            added += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM records")
    total = cur.fetchone()[0]

    print(f"同步完成 总={total} 新增={added} 更新={updated}")


# ============================================================
# 获取特码序列
# ============================================================

def get_tm_series(conn):

    cur = conn.cursor()

    cur.execute("""
    SELECT tm
    FROM records
    ORDER BY issue
    """)

    rows = cur.fetchall()

    if not rows:
        return []

    return [x[0] for x in rows]


# ============================================================
# Markov 时序预测
# ============================================================

def markov_predict(seq, order=2):

    if len(seq) <= order:
        return {}

    trans = defaultdict(Counter)

    for i in range(order, len(seq)):

        state = tuple(seq[i - order:i])

        target = seq[i]

        age = len(seq) - i

        # 时间衰减
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
# 分类预测
# ============================================================

def predict_feature(tms, func, order=2):

    seq = [func(x) for x in tms]

    return markov_predict(seq, order)


# ============================================================
# LogLoss
# ============================================================

def log_loss(prob, truth):

    eps = 1e-15

    p = max(min(prob, 1 - eps), eps)

    return -math.log(p if truth else (1 - p))


# ============================================================
# 回测
# ============================================================

def backtest(tms, func, order=2, n=100):

    if len(tms) < n + order + 1:
        return None

    seq = [func(x) for x in tms]

    correct = 0
    losses = []

    for i in range(order, len(seq) - 1):

        train = seq[:i]

        real = seq[i]

        pred = markov_predict(train, order)

        if not pred:
            continue

        best = max(pred.items(), key=lambda x: x[1])[0]

        if best == real:
            correct += 1

        prob = pred.get(real, 0.0001)

        losses.append(log_loss(prob, True))

    if not losses:
        return None

    return {
        "acc": correct / len(losses),
        "loss": sum(losses) / len(losses)
    }


# ============================================================
# 输出预测
# ============================================================

def show_prediction(conn, name, order, backtest_n):

    tms = get_tm_series(conn)

    print()
    print("=" * 60)
    print(name)
    print("=" * 60)

    if not tms:
        print("[WARN] 当前数据库没有数据")
        return

    latest = tms[-1]

    print(f"最新特码: {latest}")

    features = {
        "color": get_color,
        "size": get_size,
        "odd_even": get_odd_even
    }

    print()
    print("========== 下一期预测 ==========")

    avg_conf = []

    for fname, func in features.items():

        print()
        print(fname)

        pred = predict_feature(tms, func, order)

        if not pred:
            print("无预测数据")
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

    avg = sum(avg_conf) / len(avg_conf) if avg_conf else 0

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
            order,
            backtest_n
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
        "--lottery",
        default="全部",
        choices=["全部"] + list(LOTTERIES.keys())
    )

    parser.add_argument(
        "--order",
        type=int,
        default=2
    )

    parser.add_argument(
        "--backtest",
        type=int,
        default=120
    )

    args = parser.parse_args()

    for name, cfg in LOTTERIES.items():

        if args.lottery != "全部":

            if name != args.lottery:
                continue

        print()
        print("=" * 60)
        print(f"处理彩种: {name}")
        print("=" * 60)

        conn = sqlite3.connect(cfg["db"])

        init_db(conn)

        sync_data(conn, cfg["urls"])

        show_prediction(
            conn,
            name,
            args.order,
            args.backtest
        )

        conn.close()


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    main()