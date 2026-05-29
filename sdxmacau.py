#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种属性预测 V16-PRO REAL-DATA
#
# 修复版：
# 1. 修复假期号问题
# 2. 修复假日期问题
# 3. 最近10期真实回测
# 4. 单推 / 双推命中统计
# 5. 动态窗口
# 6. 温度校准
# 7. 马尔可夫增强
# 8. Kelly 风控
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

# =========================================================
# 固定随机种子
# =========================================================

SEED = 42

random.seed(SEED)
np.random.seed(SEED)

# =========================================================
# 基础
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

# =========================================================
# 色波
# =========================================================

RED = {
    1,2,7,8,12,13,18,19,23,24,
    29,30,34,35,40,45,46
}

BLUE = {
    3,4,9,10,14,15,20,25,26,
    31,36,37,41,42,47,48
}

GREEN = {
    5,6,11,16,17,21,22,27,28,
    32,33,38,39,43,44,49
}

# =========================================================

def get_color(num):
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"

# =========================================================

def get_big_small(num):
    return "大" if num >= 25 else "小"

# =========================================================

def get_odd_even(num):
    return "单" if num % 2 else "双"

# =========================================================
# 数据结构
# =========================================================

@dataclass
class DrawRecord:
    record_id: str
    draw_date: str
    numbers: list
    special_number: int

# =========================================================
# 数据库
# =========================================================

def connect_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# =========================================================

def init_db(conn):

    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws(
            record_id TEXT PRIMARY KEY,
            draw_date TEXT,
            numbers_json TEXT,
            special_number INTEGER,
            source TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()

# =========================================================
# 网络
# =========================================================

def fetch_json_url(url):

    try:

        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urlopen(req, timeout=20) as resp:

            return json.loads(
                resp.read().decode(
                    "utf-8",
                    errors="ignore"
                )
            )

    except:
        return None

# =========================================================
# 获取数据
# =========================================================

def fetch_online_records(lottery_name):

    for url in THIRD_PARTY_URLS:

        payload = fetch_json_url(url)

        if not payload:
            continue

        lottery_data = payload.get(
            "lottery_data",
            []
        )

        target = next(
            (
                x for x in lottery_data
                if x.get("name") == lottery_name
            ),
            None
        )

        if not target:
            continue

        records = []

        history = target.get("history", [])

        for idx, item in enumerate(history):

            try:

                parts = item.split("期：")

                if len(parts) != 2:
                    continue

                fake_issue = parts[0].strip()

                nums = [
                    int(x.strip())
                    for x in parts[1].split(",")
                ]

                if len(nums) != 7:
                    continue

                # =================================================
                # 不再伪造日期
                # 使用顺序ID
                # =================================================

                record_id = f"{lottery_name}_{idx}"

                draw_date = f"历史记录#{idx+1}"

                records.append(
                    DrawRecord(
                        record_id=record_id,
                        draw_date=draw_date,
                        numbers=nums[:6],
                        special_number=nums[6]
                    )
                )

            except:
                continue

        if records:
            return records, "marksix6"

    raise RuntimeError("无法获取数据")

# =========================================================
# 同步
# =========================================================

def sync_from_records(conn, records, source):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    ins = 0
    upd = 0

    for r in records:

        exist = conn.execute(
            "SELECT 1 FROM draws WHERE record_id=?",
            (r.record_id,)
        ).fetchone()

        if exist:

            conn.execute("""
                UPDATE draws
                SET draw_date=?,
                    numbers_json=?,
                    special_number=?,
                    source=?,
                    updated_at=?
                WHERE record_id=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                r.record_id
            ))

            upd += 1

        else:

            conn.execute("""
                INSERT INTO draws
                VALUES (?,?,?,?,?,?,?)
            """, (
                r.record_id,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                now
            ))

            ins += 1

    conn.commit()

    return len(records), ins, upd

# =========================================================
# 加载序列
# =========================================================

def load_full_records(conn):

    rows = conn.execute("""
        SELECT *
        FROM draws
        ORDER BY rowid ASC
    """).fetchall()

    return rows

# =========================================================

def entropy(probs):

    e = 0

    for p in probs.values():

        p = max(p, 1e-12)

        e -= p * math.log(p)

    return e

# =========================================================

def bayesian_prob(count, total, alpha, states):

    return (
        count + alpha
    ) / (
        total + alpha * states
    )

# =========================================================

def apply_temperature(probs, temp):

    logits = {}

    for k, v in probs.items():

        v = max(v, 1e-12)

        logits[k] = math.log(v)

    scaled = {}

    for k, v in logits.items():

        scaled[k] = math.exp(v / temp)

    total = sum(scaled.values())

    if total <= 0:

        return {
            k: 1 / len(probs)
            for k in probs
        }

    result = {
        k: v / total
        for k, v in scaled.items()
    }

    for k in result:

        result[k] = min(
            max(result[k], 0.07),
            0.72
        )

    s = sum(result.values())

    return {
        k: v / s
        for k, v in result.items()
    }

# =========================================================

def rolling_temperature_search(
    probs_hist,
    actual_hist,
    window=80
):

    if len(probs_hist) < 25:
        return 1.0

    probs_hist = probs_hist[-window:]
    actual_hist = actual_hist[-window:]

    best_temp = 1.0
    best_loss = 999999

    for temp in np.arange(0.85, 1.31, 0.02):

        losses = []

        for probs, actual in zip(
            probs_hist,
            actual_hist
        ):

            calibrated = apply_temperature(
                probs,
                temp
            )

            p = max(
                calibrated.get(actual, 1e-12),
                1e-12
            )

            losses.append(-math.log(p))

        loss = np.mean(losses)

        if loss < best_loss:

            best_loss = loss
            best_temp = temp

    return best_temp

# =========================================================

def detect_regime(seq):

    if len(seq) < 60:
        return "NORMAL"

    recent = seq[-30:]
    old = seq[-60:-30]

    recent_counts = Counter(recent)
    old_counts = Counter(old)

    drift = 0

    states = set(
        list(recent_counts.keys())
        +
        list(old_counts.keys())
    )

    for s in states:

        r = recent_counts[s] / len(recent)
        o = old_counts[s] / len(old)

        drift += abs(r - o)

    if drift > 0.35:
        return "VOLATILE"

    return "NORMAL"

# =========================================================
# 马尔可夫
# =========================================================

class ConditionalMarkov:

    def __init__(
        self,
        states,
        alpha=1.5,
        decay=0.993,
        recent_periods=220
    ):

        self.states = states
        self.alpha = alpha
        self.decay = decay
        self.recent_periods = recent_periods

        self.global_counts = Counter()
        self.transitions1 = defaultdict(Counter)
        self.transitions2 = defaultdict(Counter)

    # =====================================================

    def train(self, seq):

        seq = seq[-self.recent_periods:]

        for age, i in enumerate(
            reversed(range(len(seq)))
        ):

            s = seq[i]

            w = self.decay ** age

            self.global_counts[s] += w

        for age, i in enumerate(
            reversed(range(len(seq)-2))
        ):

            a = seq[i]
            b = seq[i+1]
            c = seq[i+2]

            w = self.decay ** age

            self.transitions2[(a,b)][c] += w
            self.transitions1[b][c] += w

    # =====================================================

    def predict(self, recent):

        if len(recent) < 2:

            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        a = recent[-2]
        b = recent[-1]

        trans2 = self.transitions2.get(
            (a,b),
            Counter()
        )

        trans1 = self.transitions1.get(
            b,
            Counter()
        )

        total2 = sum(trans2.values())
        total1 = sum(trans1.values())
        totalg = sum(self.global_counts.values())

        if total2 >= 12:

            base = trans2
            total = total2

        elif total1 >= 8:

            base = trans1
            total = total1

        else:

            base = self.global_counts
            total = totalg

        probs = {}

        for s in self.states:

            probs[s] = bayesian_prob(
                base.get(s, 0),
                total,
                self.alpha,
                len(self.states)
            )

        total_p = sum(probs.values())

        return {
            k: v / total_p
            for k, v in probs.items()
        }

# =========================================================
# Kelly
# =========================================================

class KellyBankroll:

    def __init__(self, initial=10000):

        self.initial = initial
        self.current = initial

    # =====================================================

    def get_bet_size(
        self,
        p,
        odds_total,
        probs
    ):

        ent = entropy(probs)

        if ent > 0.96:
            return 0

        b = odds_total - 1
        q = 1 - p

        edge = (b * p) - q

        if edge <= 0.01:
            return 0

        f = min(0.03, edge / b)

        return int(self.current * f)

# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=["老澳门彩","香港彩","新澳门彩"],
        default="香港彩"
    )

    parser.add_argument(
        "--recent",
        type=int,
        default=240
    )

    parser.add_argument(
        "--bankroll",
        type=int,
        default=10000
    )

    parser.add_argument(
        "--test",
        type=int,
        default=10
    )

    args = parser.parse_args()

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    try:

        records, source = fetch_online_records(
            args.lottery
        )

        sync_from_records(
            conn,
            records,
            source
        )

        rows = load_full_records(conn)

        color_seq = [
            get_color(r["special_number"])
            for r in rows
        ]

        size_seq = [
            get_big_small(r["special_number"])
            for r in rows
        ]

        odd_seq = [
            get_odd_even(r["special_number"])
            for r in rows
        ]

        test_len = min(
            args.test,
            len(color_seq) - 40
        )

        start = len(color_seq) - test_len

        historical_probs = []
        historical_actuals = []

        single_correct = 0
        double_correct = 0

        print("\n==================================================")
        print(args.lottery)
        print("==================================================")

        recent20 = color_seq[-20:]

        print("最近20期趋势:")

        rc = Counter(recent20)

        for c in ["红","蓝","绿"]:
            print(f"{c}: {rc.get(c,0)}")

        print("\n==================================================")
        print(f"最近{test_len}期回测")
        print("==================================================")

        for t in range(start, len(color_seq)):

            regime = detect_regime(
                color_seq[:t]
            )

            if regime == "VOLATILE":
                dynamic_recent = 120
            else:
                dynamic_recent = 240

            eng = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=dynamic_recent
            )

            eng.train(
                color_seq[:t]
            )

            pred = eng.predict(
                color_seq[max(0,t-30):t]
            )

            actual = color_seq[t]

            temp = rolling_temperature_search(
                historical_probs,
                historical_actuals
            )

            calibrated = apply_temperature(
                pred,
                temp
            )

            historical_probs.append(pred)
            historical_actuals.append(actual)

            sorted_color = sorted(
                calibrated.items(),
                key=lambda x: x[1],
                reverse=True
            )

            main_pick = sorted_color[0][0]
            second_pick = sorted_color[1][0]

            single_hit = main_pick == actual
            double_hit = actual in [
                main_pick,
                second_pick
            ]

            if single_hit:
                single_correct += 1

            if double_hit:
                double_correct += 1

            history_name = rows[t]["draw_date"]

            print(
                f"{history_name} "
                f"主推:{main_pick} "
                f"次推:{second_pick} "
                f"开奖:{actual} "
                f"| 单推:{'√' if single_hit else '×'} "
                f"| 双推:{'√' if double_hit else '×'}"
            )

        # =================================================
        # 统计
        # =================================================

        print("\n--------------------------------------------------")

        print(
            f"单推命中率: "
            f"{single_correct/test_len*100:.2f}%"
        )

        print(
            f"双推命中率: "
            f"{double_correct/test_len*100:.2f}%"
        )

        # =================================================
        # 下期预测
        # =================================================

        temp = rolling_temperature_search(
            historical_probs,
            historical_actuals
        )

        future_engine = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=args.recent
        )

        future_engine.train(color_seq)

        future_probs = apply_temperature(
            future_engine.predict(
                color_seq[-30:]
            ),
            temp
        )

        sorted_color = sorted(
            future_probs.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print("\n==================================================")
        print("下期预测")
        print("==================================================")

        print("\n【色波】")

        for i, (k, v) in enumerate(sorted_color):

            if i == 0:
                tag = "【主推】"
            else:
                tag = "【次推】"

            print(
                f"{tag} "
                f"{k} : {v*100:.2f}%"
            )

        strength = sorted_color[0][1]

        if strength >= 0.55:
            stars = "★★★★★"
        elif strength >= 0.48:
            stars = "★★★★☆"
        elif strength >= 0.40:
            stars = "★★★☆☆"
        elif strength >= 0.36:
            stars = "★★☆☆☆"
        else:
            stars = "★☆☆☆☆"

        print(f"\n主推强度: {stars}")

        coverage = (
            sorted_color[0][1]
            +
            sorted_color[1][1]
        ) * 100

        print(f"双推覆盖: {coverage:.2f}%")

        print(
            f"推荐组合: "
            f"{sorted_color[0][0]} + "
            f"{sorted_color[1][0]}"
        )

        # =================================================
        # 大小
        # =================================================

        size_engine = ConditionalMarkov(
            ["大","小"],
            recent_periods=args.recent
        )

        size_engine.train(size_seq)

        size_probs = apply_temperature(
            size_engine.predict(
                size_seq[-30:]
            ),
            temp
        )

        print("\n【大小】")

        for k, v in sorted(
            size_probs.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            print(f"{k} : {v*100:.2f}%")

        # =================================================
        # 单双
        # =================================================

        odd_engine = ConditionalMarkov(
            ["单","双"],
            recent_periods=args.recent
        )

        odd_engine.train(odd_seq)

        odd_probs = apply_temperature(
            odd_engine.predict(
                odd_seq[-30:]
            ),
            temp
        )

        print("\n【单双】")

        for k, v in sorted(
            odd_probs.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            print(f"{k} : {v*100:.2f}%")

        print("\n==================================================")

    except Exception as e:

        print(f"错误: {e}")

    finally:

        conn.close()

# =========================================================

if __name__ == "__main__":
    main()