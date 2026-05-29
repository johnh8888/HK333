#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种属性预测 V16-FINAL
#
# 修复内容：
# 1. 修复期数错误
# 2. 修复日期错误
# 3. 在线实时获取最新数据
# 4. 最近10期真实回测
# 5. 波色单双大小预测
# 6. 动态窗口
# 7. 双推增强
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass
from urllib.request import Request, urlopen

import numpy as np

# =========================================================
# 随机种子
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

def get_big_small(num):
    return "大" if num >= 25 else "小"

def get_odd_even(num):
    return "单" if num % 2 else "双"

# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    special_number: int

# =========================================================
# 数据库
# =========================================================

def connect_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws(
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        special_number INTEGER
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

        with urlopen(req, timeout=20) as r:
            return json.loads(
                r.read().decode("utf-8", errors="ignore")
            )

    except:
        return None

# =========================================================
# 在线获取真实数据
# =========================================================

def fetch_online_records(lottery_name):

    for url in API_URLS:

        data = fetch_json(url)

        if not data:
            continue

        lottery_data = data.get("lottery_data", [])

        target = None

        for x in lottery_data:
            if x.get("name") == lottery_name:
                target = x
                break

        if not target:
            continue

        history = target.get("history", [])

        records = []

        for item in history:

            try:

                # 真实格式:
                # 2026048期：12,15,22,33,41,48,16

                if "期：" not in item:
                    continue

                left, right = item.split("期：")

                issue_no = left.strip()

                nums = [
                    int(x.strip())
                    for x in right.split(",")
                ]

                if len(nums) != 7:
                    continue

                special = nums[-1]

                # 自动推导年份日期
                # issue: 2026048

                year = issue_no[:4]
                seq = issue_no[4:]

                draw_date = f"{year}-第{seq}期"

                records.append(
                    DrawRecord(
                        issue_no=issue_no,
                        draw_date=draw_date,
                        special_number=special
                    )
                )

            except:
                continue

        if records:
            return records

    raise RuntimeError("无法获取在线数据")

# =========================================================
# 保存
# =========================================================

def save_records(conn, records):

    for r in records:

        conn.execute("""
        INSERT OR REPLACE INTO draws(
            issue_no,
            draw_date,
            special_number
        )
        VALUES(?,?,?)
        """, (
            r.issue_no,
            r.draw_date,
            r.special_number
        ))

    conn.commit()

# =========================================================
# 加载
# =========================================================

def load_records(conn):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no
    """).fetchall()

    return rows

# =========================================================
# 条件马尔可夫
# =========================================================

class ConditionalMarkov:

    def __init__(
        self,
        states,
        decay=0.992,
        alpha=1.2,
        recent=240
    ):

        self.states = states
        self.decay = decay
        self.alpha = alpha
        self.recent = recent

        self.trans = defaultdict(Counter)

    def train(self, seq):

        seq = seq[-self.recent:]

        for age, i in enumerate(
            reversed(range(len(seq)-1))
        ):

            a = seq[i]
            b = seq[i+1]

            w = self.decay ** age

            self.trans[a][b] += w

    def predict(self, recent):

        if not recent:
            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        last = recent[-1]

        c = self.trans.get(last, Counter())

        total = sum(c.values())

        probs = {}

        for s in self.states:

            probs[s] = (
                c.get(s, 0) + self.alpha
            ) / (
                total + self.alpha * len(self.states)
            )

        sm = sum(probs.values())

        return {
            k: v / sm
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
        "--test",
        type=int,
        default=10
    )

    args = parser.parse_args()

    print("=" * 50)
    print(args.lottery)
    print("=" * 50)

    # =====================================================
    # DB
    # =====================================================

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    # =====================================================
    # 获取在线最新数据
    # =====================================================

    records = fetch_online_records(args.lottery)

    save_records(conn, records)

    rows = load_records(conn)

    # =====================================================
    # 构建序列
    # =====================================================

    color_seq = []
    size_seq = []
    odd_seq = []

    for r in rows:

        num = r["special_number"]

        color_seq.append(
            get_color(num)
        )

        size_seq.append(
            get_big_small(num)
        )

        odd_seq.append(
            get_odd_even(num)
        )

    # =====================================================
    # 动态窗口
    # =====================================================

    recent20 = color_seq[-20:]

    c = Counter(recent20)

    if max(c.values()) >= 11:
        dynamic_recent = 180
    else:
        dynamic_recent = 240

    print(f"动态窗口: {dynamic_recent}")

    print("\n最近20期趋势:")

    print(f"红: {c['红']}")
    print(f"蓝: {c['蓝']}")
    print(f"绿: {c['绿']}")

    # =====================================================
    # 回测
    # =====================================================

    print("\n" + "=" * 50)
    print(f"最近{args.test}期回测")
    print("=" * 50)

    single_hit = 0
    double_hit = 0

    size_hit = 0
    odd_hit = 0

    start = len(rows) - args.test

    for t in range(start, len(rows)):

        train_color = color_seq[:t]
        train_size = size_seq[:t]
        train_odd = odd_seq[:t]

        # 波色
        eng_color = ConditionalMarkov(
            ["红","蓝","绿"],
            recent=dynamic_recent
        )

        eng_color.train(train_color)

        probs = eng_color.predict(
            train_color[-30:]
        )

        sorted_probs = sorted(
            probs.items(),
            key=lambda x: x[1],
            reverse=True
        )

        main_color = sorted_probs[0][0]
        second_color = sorted_probs[1][0]

        actual_color = color_seq[t]

        # 大小
        eng_size = ConditionalMarkov(
            ["大","小"],
            recent=dynamic_recent
        )

        eng_size.train(train_size)

        size_probs = eng_size.predict(
            train_size[-30:]
        )

        size_pred = max(
            size_probs,
            key=size_probs.get
        )

        actual_size = size_seq[t]

        # 单双
        eng_odd = ConditionalMarkov(
            ["单","双"],
            recent=dynamic_recent
        )

        eng_odd.train(train_odd)

        odd_probs = eng_odd.predict(
            train_odd[-30:]
        )

        odd_pred = max(
            odd_probs,
            key=odd_probs.get
        )

        actual_odd = odd_seq[t]

        # 命中
        single_ok = main_color == actual_color
        double_ok = actual_color in [
            main_color,
            second_color
        ]

        if single_ok:
            single_hit += 1

        if double_ok:
            double_hit += 1

        if size_pred == actual_size:
            size_hit += 1

        if odd_pred == actual_odd:
            odd_hit += 1

        issue = rows[t]["issue_no"]

        print(
            f"{issue}期 "
            f"主推:{main_color} "
            f"次推:{second_color} "
            f"开奖:{actual_color} "
            f"| 单推:{'√' if single_ok else '×'} "
            f"| 双推:{'√' if double_ok else '×'} "
            f"| 大小:{'√' if size_pred == actual_size else '×'} "
            f"| 单双:{'√' if odd_pred == actual_odd else '×'}"
        )

    # =====================================================
    # 回测结果
    # =====================================================

    print("\n" + "-" * 50)

    print(
        f"波色单推命中率: "
        f"{single_hit / args.test * 100:.2f}%"
    )

    print(
        f"波色双推命中率: "
        f"{double_hit / args.test * 100:.2f}%"
    )

    print(
        f"大小命中率: "
        f"{size_hit / args.test * 100:.2f}%"
    )

    print(
        f"单双命中率: "
        f"{odd_hit / args.test * 100:.2f}%"
    )

    # =====================================================
    # 下期预测
    # =====================================================

    print("\n" + "=" * 50)
    print("下期预测")
    print("=" * 50)

    # 波色
    eng_color = ConditionalMarkov(
        ["红","蓝","绿"],
        recent=dynamic_recent
    )

    eng_color.train(color_seq)

    probs = eng_color.predict(
        color_seq[-30:]
    )

    sorted_probs = sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    print("\n【波色】")

    for i, (k, v) in enumerate(sorted_probs):

        tag = ""

        if i == 0:
            tag = "【主推】"

        elif i == 1:
            tag = "【次推】"

        print(
            f"{tag} {k} : {v*100:.2f}%"
        )

    print(
        f"\n双推组合: "
        f"{sorted_probs[0][0]} + {sorted_probs[1][0]}"
    )

    print(
        f"双推覆盖: "
        f"{(sorted_probs[0][1] + sorted_probs[1][1]) * 100:.2f}%"
    )

    # 大小
    eng_size = ConditionalMarkov(
        ["大","小"],
        recent=dynamic_recent
    )

    eng_size.train(size_seq)

    size_probs = eng_size.predict(
        size_seq[-30:]
    )

    print("\n【大小】")

    for k, v in sorted(
        size_probs.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        print(
            f"{k} : {v*100:.2f}%"
        )

    # 单双
    eng_odd = ConditionalMarkov(
        ["单","双"],
        recent=dynamic_recent
    )

    eng_odd.train(odd_seq)

    odd_probs = eng_odd.predict(
        odd_seq[-30:]
    )

    print("\n【单双】")

    for k, v in sorted(
        odd_probs.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        print(
            f"{k} : {v*100:.2f}%"
        )

    print("\n" + "=" * 50)

    conn.close()

# =========================================================

if __name__ == "__main__":
    main()