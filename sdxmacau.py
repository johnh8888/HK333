#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种属性预测 V16-PRO FINAL
#
# 修复版：
# 1. 修复香港彩年份期号错误
# 2. 修复最近10期日期错误
# 3. 修复数据库脏数据
# 4. 真实线上数据排序
# 5. 波色单双大小完整回测
# 6. 主推 + 次推 + 双推覆盖
# 7. Conditional Markov
# 8. 动态窗口
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3

from collections import defaultdict, Counter
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
    "香港彩": "hk_macau.db",
    "老澳门彩": "old_macau.db",
    "新澳门彩": "xin_macau.db"
}

URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]

# =========================================================
# 波色
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
    special_number: int

# =========================================================
# DB
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
            special_number INTEGER,
            created_at TEXT
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
# 获取线上数据
# =========================================================

def normalize_issue(issue):

    issue = str(issue).strip()

    nums = re.findall(r"\d+", issue)

    if not nums:
        return issue

    n = nums[0]

    if len(n) >= 7:
        return n

    year = datetime.now().year

    return f"{year}-{int(n):03d}"

# =========================================================

def fetch_online_records(lottery_name):

    for url in URLS:

        payload = fetch_json(url)

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

        history = target.get("history", [])

        open_time = target.get(
            "openTime",
            ""
        )

        records = []

        for item in history:

            try:

                parts = item.split("期：")

                if len(parts) != 2:
                    continue

                issue_raw = parts[0].strip()

                issue_no = normalize_issue(
                    issue_raw
                )

                nums = [
                    int(x.strip())
                    for x in parts[1].split(",")
                ]

                if len(nums) != 7:
                    continue

                special = nums[6]

                draw_date = open_time[:10]

                records.append(
                    DrawRecord(
                        issue_no,
                        draw_date,
                        special
                    )
                )

            except:
                continue

        if records:
            return records

    raise RuntimeError("无法获取线上数据")

# =========================================================
# 同步
# =========================================================

def sync_records(conn, records):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for r in records:

        conn.execute("""
            INSERT OR REPLACE INTO draws
            VALUES (?,?,?,?)
        """, (
            r.issue_no,
            r.draw_date,
            r.special_number,
            now
        ))

    conn.commit()

# =========================================================
# 排序
# =========================================================

def issue_sort_key(issue):

    nums = re.findall(r"\d+", issue)

    if len(nums) >= 2:
        return (
            int(nums[0]),
            int(nums[1])
        )

    if len(nums) == 1:
        return (
            datetime.now().year,
            int(nums[0])
        )

    return (0, 0)

# =========================================================
# 加载数据
# =========================================================

def load_draws(conn):

    rows = conn.execute("""
        SELECT *
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_sort_key(
            r["issue_no"]
        )
    )

    return rows

# =========================================================
# 条件马尔可夫
# =========================================================

class ConditionalMarkov:

    def __init__(
        self,
        states,
        alpha=1.2,
        decay=0.992,
        recent_periods=240
    ):

        self.states = states
        self.alpha = alpha
        self.decay = decay
        self.recent_periods = recent_periods

        self.global_counts = Counter()
        self.transitions = defaultdict(Counter)

    # =====================================================

    def train(self, seq):

        seq = seq[-self.recent_periods:]

        for age, i in enumerate(
            reversed(range(len(seq)-1))
        ):

            a = seq[i]
            b = seq[i+1]

            w = self.decay ** age

            self.transitions[a][b] += w
            self.global_counts[b] += w

    # =====================================================

    def predict(self, recent):

        if not recent:
            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        last = recent[-1]

        trans = self.transitions.get(
            last,
            Counter()
        )

        total = sum(trans.values())

        if total < 3:
            trans = self.global_counts
            total = sum(trans.values())

        probs = {}

        for s in self.states:

            probs[s] = (
                trans.get(s, 0) + self.alpha
            ) / (
                total + self.alpha * len(self.states)
            )

        total_p = sum(probs.values())

        return {
            k: v / total_p
            for k, v in probs.items()
        }

# =========================================================
# 温度校准
# =========================================================

def apply_temperature(probs, temp=1.0):

    logits = {}

    for k, v in probs.items():

        v = max(v, 1e-12)

        logits[k] = math.log(v)

    scaled = {}

    for k, v in logits.items():
        scaled[k] = math.exp(v / temp)

    total = sum(scaled.values())

    return {
        k: v / total
        for k, v in scaled.items()
    }

# =========================================================
# 主程序
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=["香港彩","老澳门彩","新澳门彩"],
        default="香港彩"
    )

    parser.add_argument(
        "--recent",
        type=int,
        default=240
    )

    parser.add_argument(
        "--test",
        type=int,
        default=10
    )

    args = parser.parse_args()

    print("="*60)
    print(args.lottery)
    print("="*60)

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    # =====================================================
    # 线上同步
    # =====================================================

    records = fetch_online_records(
        args.lottery
    )

    sync_records(conn, records)

    rows = load_draws(conn)

    # =====================================================
    # 自动过滤脏数据
    # =====================================================

    clean_rows = []

    for r in rows:

        issue = r["issue_no"]

        if "-" not in issue:
            continue

        clean_rows.append(r)

    rows = clean_rows

    # =====================================================
    # 序列
    # =====================================================

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

    # =====================================================
    # 动态窗口
    # =====================================================

    recent20 = color_seq[-20:]

    rc = Counter(recent20)

    dynamic_recent = 240

    if rc["红"] >= 12:
        dynamic_recent = 180

    print(f"动态窗口: {dynamic_recent}")

    # =====================================================
    # 最近20期趋势
    # =====================================================

    print()
    print("最近20期趋势:")

    print(f"红: {rc['红']}")
    print(f"蓝: {rc['蓝']}")
    print(f"绿: {rc['绿']}")

    # =====================================================
    # 最近10期回测
    # =====================================================

    print()
    print("="*60)
    print("最近10期回测")
    print("="*60)

    single_hit = 0
    double_hit = 0

    size_hit = 0
    odd_hit = 0

    start = len(rows) - args.test

    for t in range(start, len(rows)):

        train_color = color_seq[:t]
        train_size = size_seq[:t]
        train_odd = odd_seq[:t]

        model_color = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=dynamic_recent
        )

        model_size = ConditionalMarkov(
            ["大","小"],
            recent_periods=dynamic_recent
        )

        model_odd = ConditionalMarkov(
            ["单","双"],
            recent_periods=dynamic_recent
        )

        model_color.train(train_color)
        model_size.train(train_size)
        model_odd.train(train_odd)

        probs_color = apply_temperature(
            model_color.predict(
                train_color[-30:]
            ),
            1.0
        )

        probs_size = model_size.predict(
            train_size[-30:]
        )

        probs_odd = model_odd.predict(
            train_odd[-30:]
        )

        sorted_color = sorted(
            probs_color.items(),
            key=lambda x: x[1],
            reverse=True
        )

        main_color = sorted_color[0][0]
        second_color = sorted_color[1][0]

        actual_color = color_seq[t]

        actual_size = size_seq[t]
        actual_odd = odd_seq[t]

        single_ok = actual_color == main_color
        double_ok = actual_color in [
            main_color,
            second_color
        ]

        if single_ok:
            single_hit += 1

        if double_ok:
            double_hit += 1

        pred_size = max(
            probs_size,
            key=probs_size.get
        )

        pred_odd = max(
            probs_odd,
            key=probs_odd.get
        )

        if pred_size == actual_size:
            size_hit += 1

        if pred_odd == actual_odd:
            odd_hit += 1

        row = rows[t]

        print(
            f"{row['issue_no']} "
            f"{row['draw_date']} "
            f"特码:{row['special_number']:02d} "
            f"主推:{main_color} "
            f"次推:{second_color} "
            f"开奖:{actual_color} "
            f"| 单推:{'√' if single_ok else '×'} "
            f"| 双推:{'√' if double_ok else '×'} "
            f"| 大小:{pred_size}/{actual_size} "
            f"| 单双:{pred_odd}/{actual_odd}"
        )

    # =====================================================
    # 回测统计
    # =====================================================

    print()
    print("-"*60)

    print(
        f"波色单推命中率: "
        f"{single_hit/args.test*100:.2f}%"
    )

    print(
        f"波色双推命中率: "
        f"{double_hit/args.test*100:.2f}%"
    )

    print(
        f"大小命中率: "
        f"{size_hit/args.test*100:.2f}%"
    )

    print(
        f"单双命中率: "
        f"{odd_hit/args.test*100:.2f}%"
    )

    # =====================================================
    # 下期预测
    # =====================================================

    print()
    print("="*60)
    print("下期预测")
    print("="*60)

    final_model = ConditionalMarkov(
        ["红","蓝","绿"],
        recent_periods=dynamic_recent
    )

    final_model.train(color_seq)

    final_probs = apply_temperature(
        final_model.predict(
            color_seq[-30:]
        ),
        1.0
    )

    sorted_final = sorted(
        final_probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    print()
    print("【色波】")

    for i, (k, v) in enumerate(sorted_final):

        if i == 0:
            tag = "【主推】"

        elif i == 1:
            tag = "【次推】"

        else:
            tag = "        "

        print(
            f"{tag} {k} : {v*100:.2f}%"
        )

    cover = (
        sorted_final[0][1]
        +
        sorted_final[1][1]
    )

    print()

    if sorted_final[0][1] >= 0.45:
        stars = "★★★★★"
    elif sorted_final[0][1] >= 0.40:
        stars = "★★★★☆"
    elif sorted_final[0][1] >= 0.35:
        stars = "★★★☆☆"
    else:
        stars = "★★☆☆☆"

    print(f"主推强度: {stars}")

    print(
        f"双推覆盖: "
        f"{cover*100:.2f}%"
    )

    print(
        f"推荐组合: "
        f"{sorted_final[0][0]} + "
        f"{sorted_final[1][0]}"
    )

    # =====================================================
    # 大小
    # =====================================================

    print()
    print("【大小】")

    model_size = ConditionalMarkov(
        ["大","小"],
        recent_periods=dynamic_recent
    )

    model_size.train(size_seq)

    probs_size = model_size.predict(
        size_seq[-30:]
    )

    for k, v in sorted(
        probs_size.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        print(f"{k} : {v*100:.2f}%")

    # =====================================================
    # 单双
    # =====================================================

    print()
    print("【单双】")

    model_odd = ConditionalMarkov(
        ["单","双"],
        recent_periods=dynamic_recent
    )

    model_odd.train(odd_seq)

    probs_odd = model_odd.predict(
        odd_seq[-30:]
    )

    for k, v in sorted(
        probs_odd.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        print(f"{k} : {v*100:.2f}%")

    print()
    print("="*60)

    conn.close()

# =========================================================

if __name__ == "__main__":
    main()