#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种属性预测 V16-PRO FINAL
#
# 修复版：
# 1. 修复 issue_no / record_id 崩溃
# 2. 修复香港彩“245期”错误
# 3. 最近10期真实回测
# 4. 单推 / 双推统计
# 5. 大小 / 单双预测
# 6. 动态窗口
# 7. 温度校准
# 8. 马尔可夫增强
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import re

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

# =========================================================
# 固定随机
# =========================================================

SEED = 42

random.seed(SEED)
np.random.seed(SEED)

# =========================================================
# 路径
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "香港彩": "hk_macau.db",
    "老澳门彩": "old_macau.db",
    "新澳门彩": "xin_macau.db"
}

API_URLS = [
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
    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

# =========================================================
# 数据库
# =========================================================

def connect_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

# =========================================================

def init_db(conn):

    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws(
            issue_no TEXT PRIMARY KEY,
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

def fetch_json(url):

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

    for url in API_URLS:

        payload = fetch_json(url)

        if not payload:
            continue

        lottery_data = payload.get("lottery_data", [])

        target = next(
            (
                x for x in lottery_data
                if x.get("name") == lottery_name
            ),
            None
        )

        if not target:
            continue

        history = target.get("history", [])

        records = []

        for idx, item in enumerate(history):

            try:

                parts = item.split("期：")

                if len(parts) != 2:
                    continue

                real_issue = parts[0].strip()

                nums = [
                    int(x.strip())
                    for x in parts[1].split(",")
                ]

                if len(nums) != 7:
                    continue

                issue_no = f"{lottery_name}_{real_issue}"

                records.append(
                    DrawRecord(
                        issue_no=issue_no,
                        draw_date=str(datetime.now().date()),
                        numbers=nums[:6],
                        special_number=nums[6]
                    )
                )

            except:
                continue

        if records:
            return records

    raise RuntimeError("无法获取数据")

# =========================================================
# 同步数据库
# =========================================================

def sync_records(conn, records):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for r in records:

        exist = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r.issue_no,)
        ).fetchone()

        if exist:

            conn.execute("""
                UPDATE draws
                SET
                    draw_date=?,
                    numbers_json=?,
                    special_number=?,
                    updated_at=?
                WHERE issue_no=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                now,
                r.issue_no
            ))

        else:

            conn.execute("""
                INSERT INTO draws
                VALUES (?,?,?,?,?,?,?)
            """, (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                "api",
                now,
                now
            ))

    conn.commit()

# =========================================================
# 排序
# =========================================================

def issue_sort(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if nums == "":
        return 0

    return int(nums)

# =========================================================
# 加载序列
# =========================================================

def load_special_numbers(conn):

    rows = conn.execute("""
        SELECT issue_no, special_number
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_sort(r["issue_no"])
    )

    return rows

# =========================================================
# 熵
# =========================================================

def entropy(probs):

    e = 0

    for p in probs.values():

        p = max(p, 1e-12)

        e -= p * math.log(p)

    return e

# =========================================================
# 贝叶斯
# =========================================================

def bayesian_prob(count, total, alpha, states):

    return (
        count + alpha
    ) / (
        total + alpha * states
    )

# =========================================================
# 温度
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

    result = {
        k: v / total
        for k, v in scaled.items()
    }

    return result

# =========================================================
# 马尔可夫
# =========================================================

class ConditionalMarkov:

    def __init__(
        self,
        states,
        alpha=1.5,
        decay=0.992,
        recent_periods=180
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

        if total2 >= 10:

            base = trans2
            total = total2

        elif total1 >= 6:

            base = trans1
            total = total1

        else:

            base = self.global_counts
            total = sum(base.values())

        probs = {}

        for s in self.states:

            probs[s] = bayesian_prob(
                base.get(s,0),
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
# 主程序
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
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

        records = fetch_online_records(
            args.lottery
        )

        sync_records(conn, records)

        rows = load_special_numbers(conn)

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

        dynamic_recent = min(
            args.recent,
            max(120, len(color_seq)//2)
        )

        print("\n==================================================")
        print(args.lottery)
        print("==================================================")

        print(f"动态窗口: {dynamic_recent}")

        recent20 = color_seq[-20:]

        c = Counter(recent20)

        print("\n最近20期趋势:")
        print(f"红: {c['红']}")
        print(f"蓝: {c['蓝']}")
        print(f"绿: {c['绿']}")

        # =================================================
        # 最近10期回测
        # =================================================

        print("\n==================================================")
        print("最近10期回测")
        print("==================================================")

        single_hit = 0
        double_hit = 0

        for t in range(
            len(color_seq)-args.test,
            len(color_seq)
        ):

            eng = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=dynamic_recent
            )

            eng.train(color_seq[:t])

            probs = eng.predict(
                color_seq[max(0,t-30):t]
            )

            probs = apply_temperature(
                probs,
                1.0
            )

            sorted_probs = sorted(
                probs.items(),
                key=lambda x:x[1],
                reverse=True
            )

            main_pick = sorted_probs[0][0]
            second_pick = sorted_probs[1][0]

            actual = color_seq[t]

            single_ok = actual == main_pick
            double_ok = actual in [
                main_pick,
                second_pick
            ]

            if single_ok:
                single_hit += 1

            if double_ok:
                double_hit += 1

            print(
                f"历史#{t+1} "
                f"主推:{main_pick} "
                f"次推:{second_pick} "
                f"开奖:{actual} | "
                f"单推:{'√' if single_ok else '×'} | "
                f"双推:{'√' if double_ok else '×'}"
            )

        # =================================================

        print("\n--------------------------------------------------")

        print(
            f"单推命中率: "
            f"{single_hit/args.test*100:.2f}%"
        )

        print(
            f"双推命中率: "
            f"{double_hit/args.test*100:.2f}%"
        )

        # =================================================
        # 下期预测
        # =================================================

        eng_color = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=dynamic_recent
        )

        eng_size = ConditionalMarkov(
            ["大","小"],
            recent_periods=dynamic_recent
        )

        eng_odd = ConditionalMarkov(
            ["单","双"],
            recent_periods=dynamic_recent
        )

        eng_color.train(color_seq)
        eng_size.train(size_seq)
        eng_odd.train(odd_seq)

        color_probs = apply_temperature(
            eng_color.predict(color_seq[-30:]),
            1.0
        )

        size_probs = apply_temperature(
            eng_size.predict(size_seq[-30:]),
            1.0
        )

        odd_probs = apply_temperature(
            eng_odd.predict(odd_seq[-30:]),
            1.0
        )

        print("\n==================================================")
        print("下期预测")
        print("==================================================")

        # =================================================
        # 色波
        # =================================================

        print("\n【色波】")

        sorted_color = sorted(
            color_probs.items(),
            key=lambda x:x[1],
            reverse=True
        )

        for i, (k,v) in enumerate(sorted_color):

            if i == 0:
                tag = "【主推】"

            elif i == 1:
                tag = "【次推】"

            else:
                tag = "        "

            print(
                f"{tag} {k} : {v*100:.2f}%"
            )

        top_cover = (
            sorted_color[0][1]
            +
            sorted_color[1][1]
        )

        # 主推强度

        if sorted_color[0][1] >= 0.50:
            stars = "★★★★★"
        elif sorted_color[0][1] >= 0.45:
            stars = "★★★★☆"
        elif sorted_color[0][1] >= 0.40:
            stars = "★★★☆☆"
        elif sorted_color[0][1] >= 0.36:
            stars = "★★☆☆☆"
        else:
            stars = "★☆☆☆☆"

        print(f"\n主推强度: {stars}")
        print(f"双推覆盖: {top_cover*100:.2f}%")

        print(
            f"推荐组合: "
            f"{sorted_color[0][0]} + {sorted_color[1][0]}"
        )

        # =================================================
        # 大小
        # =================================================

        print("\n【大小】")

        for k,v in sorted(
            size_probs.items(),
            key=lambda x:x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        # =================================================
        # 单双
        # =================================================

        print("\n【单双】")

        for k,v in sorted(
            odd_probs.items(),
            key=lambda x:x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        print("\n==================================================")

    except Exception as e:

        print(f"错误: {e}")

    finally:

        conn.close()

# =========================================================

if __name__ == "__main__":
    main()