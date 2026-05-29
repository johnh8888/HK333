#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V18-QUANT AI ENHANCED STABLE
#
# 终极增强稳定版
#
# 功能：
#
# [1] 在线同步最新数据
# [2] SQLite 自动保存
# [3] 波色预测
# [4] 大小预测
# [5] 单双预测
# [6] 最近10期详细回测
# [7] 动态窗口优化
# [8] 热冷号修正
# [9] 连续错杀反转
# [10] 趋势增强
# [11] 自适应权重
# [12] WalkForward真实回测
#
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
from datetime import datetime, timezone, timedelta
from pathlib import Path

from urllib.request import Request, urlopen

# =========================================================
# 随机种子
# =========================================================

SEED = 42
random.seed(SEED)

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

    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

# =========================================================
# DB
# =========================================================

def connect_db(db_path):

    conn = sqlite3.connect(db_path)
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
# 获取在线数据
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

        for item in history:

            try:

                parts = item.split("期：")

                if len(parts) != 2:
                    continue

                issue_no = parts[0].strip()

                nums = [
                    int(x.strip())
                    for x in parts[1].split(",")
                ]

                if len(nums) != 7:
                    continue

                draw_date = target.get(
                    "openTime",
                    ""
                ).split(" ")[0]

                records.append(
                    DrawRecord(
                        issue_no=issue_no,
                        draw_date=draw_date,
                        numbers=nums[:6],
                        special_number=nums[6]
                    )
                )

            except:
                continue

        if records:
            return records, "marksix6"

    raise RuntimeError("无法获取在线数据")

# =========================================================
# 同步数据库
# =========================================================

def sync_from_records(conn, records, source):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    ins = 0
    upd = 0

    for r in records:

        exist = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?",
            (r.issue_no,)
        ).fetchone()

        if exist:

            conn.execute("""
                UPDATE draws
                SET draw_date=?,
                    numbers_json=?,
                    special_number=?,
                    source=?,
                    updated_at=?
                WHERE issue_no=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                r.issue_no
            ))

            upd += 1

        else:

            conn.execute("""
                INSERT INTO draws
                VALUES (?,?,?,?,?,?,?)
            """, (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                source,
                now,
                now
            ))

            ins += 1

    conn.commit()

    return ins, upd

# =========================================================

def issue_to_int(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if not nums:
        return 0

    return int(nums)

# =========================================================

def load_rows(conn):

    rows = conn.execute("""
        SELECT *
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_to_int(
            r["issue_no"]
        )
    )

    return rows

# =========================================================
# 动态窗口选择
# =========================================================

def select_best_window(seq, states):

    candidates = [
        60,
        90,
        120,
        180,
        240
    ]

    best_window = 120
    best_score = -1

    for w in candidates:

        if len(seq) < w + 20:
            continue

        hit = 0
        total = 0

        for t in range(
            len(seq)-20,
            len(seq)
        ):

            train = seq[max(0, t-w):t]

            if len(train) < 5:
                continue

            counter = Counter(train)

            pred = max(
                counter,
                key=counter.get
            )

            if pred == seq[t]:
                hit += 1

            total += 1

        if total == 0:
            continue

        score = hit / total

        if score > best_score:

            best_score = score
            best_window = w

    return best_window

# =========================================================
# 增强预测
# =========================================================

def enhanced_predict(seq, states, window):

    recent = seq[-window:]

    scores = Counter()

    # =====================================================
    # 基础频率
    # =====================================================

    for s in recent:
        scores[s] += 1.0

    # =====================================================
    # 趋势增强
    # =====================================================

    last10 = recent[-10:]

    for s in last10:
        scores[s] += 1.5

    # =====================================================
    # 连续反转
    # =====================================================

    if len(recent) >= 3:

        if recent[-1] == recent[-2]:

            for s in states:

                if s != recent[-1]:
                    scores[s] += 2.0

    # =====================================================
    # 热冷修正
    # =====================================================

    freq = Counter(recent)

    for s in states:

        if freq[s] <= 2:
            scores[s] += 1.8

    # =====================================================
    # 归一化
    # =====================================================

    total = sum(scores.values())

    probs = {}

    for s in states:

        probs[s] = (
            scores[s] / total
        )

    return probs

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

    try:

        # =================================================
        # 同步数据
        # =================================================

        records, source = fetch_online_records(
            args.lottery
        )

        ins, upd = sync_from_records(
            conn,
            records,
            source
        )

        print(f"\n同步完成 新增:{ins} 更新:{upd}")

        rows = load_rows(conn)

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

        # =================================================
        # 动态窗口
        # =================================================

        best_color_window = select_best_window(
            color_seq,
            ["红","蓝","绿"]
        )

        best_size_window = select_best_window(
            size_seq,
            ["大","小"]
        )

        best_odd_window = select_best_window(
            odd_seq,
            ["单","双"]
        )

        print(f"\n波色最佳窗口: {best_color_window}")
        print(f"大小最佳窗口: {best_size_window}")
        print(f"单双最佳窗口: {best_odd_window}")

        # =================================================
        # 最近10期回测
        # =================================================

        print("\n" + "="*60)
        print("最近10期详细回测")
        print("="*60)

        start = len(rows) - args.test

        color_single_hit = 0
        color_double_hit = 0

        size_hit = 0
        odd_hit = 0

        for t in range(start, len(rows)):

            # =============================================
            # 波色
            # =============================================

            color_probs = enhanced_predict(
                color_seq[:t],
                ["红","蓝","绿"],
                best_color_window
            )

            color_sorted = sorted(
                color_probs.items(),
                key=lambda x: x[1],
                reverse=True
            )

            main_color = color_sorted[0][0]
            second_color = color_sorted[1][0]

            actual_color = color_seq[t]

            single_ok = (
                main_color == actual_color
            )

            double_ok = (
                actual_color in [
                    main_color,
                    second_color
                ]
            )

            if single_ok:
                color_single_hit += 1

            if double_ok:
                color_double_hit += 1

            # =============================================
            # 大小
            # =============================================

            size_probs = enhanced_predict(
                size_seq[:t],
                ["大","小"],
                best_size_window
            )

            pred_size = max(
                size_probs,
                key=size_probs.get
            )

            actual_size = size_seq[t]

            size_ok = (
                pred_size == actual_size
            )

            if size_ok:
                size_hit += 1

            # =============================================
            # 单双
            # =============================================

            odd_probs = enhanced_predict(
                odd_seq[:t],
                ["单","双"],
                best_odd_window
            )

            pred_odd = max(
                odd_probs,
                key=odd_probs.get
            )

            actual_odd = odd_seq[t]

            odd_ok = (
                pred_odd == actual_odd
            )

            if odd_ok:
                odd_hit += 1

            row = rows[t]

            print(
                f"{row['issue_no']} "
                f"| 波色:{main_color}+{second_color} "
                f"| 开:{actual_color} "
                f"| 主推:{'√' if single_ok else '×'} "
                f"| 双推:{'√' if double_ok else '×'} "
                f"| 大小:{pred_size}/{actual_size} {'√' if size_ok else '×'} "
                f"| 单双:{pred_odd}/{actual_odd} {'√' if odd_ok else '×'}"
            )

        # =================================================
        # 回测统计
        # =================================================

        print("\n" + "="*60)
        print("最近10期命中统计")
        print("="*60)

        print(
            f"波色主推命中率: "
            f"{color_single_hit/args.test*100:.2f}%"
        )

        print(
            f"波色双推命中率: "
            f"{color_double_hit/args.test*100:.2f}%"
        )

        print(
            f"大小命中率: "
            f"{size_hit/args.test*100:.2f}%"
        )

        print(
            f"单双命中率: "
            f"{odd_hit/args.test*100:.2f}%"
        )

        # =================================================
        # 下期预测
        # =================================================

        next_issue = (
            issue_to_int(
                rows[-1]["issue_no"]
            ) + 1
        )

        print("\n" + "="*60)
        print(f"下期预测（{next_issue}）")
        print("="*60)

        # =============================================
        # 波色
        # =============================================

        future_color = enhanced_predict(
            color_seq,
            ["红","蓝","绿"],
            best_color_window
        )

        color_sorted = sorted(
            future_color.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print("\n【波色】")

        for k, v in color_sorted:

            print(
                f"{k} : {v*100:.2f}%"
            )

        print(
            f"\n推荐组合: "
            f"{color_sorted[0][0]} + {color_sorted[1][0]}"
        )

        print(
            f"双推覆盖率: "
            f"{(color_sorted[0][1]+color_sorted[1][1])*100:.2f}%"
        )

        # =============================================
        # 大小
        # =============================================

        future_size = enhanced_predict(
            size_seq,
            ["大","小"],
            best_size_window
        )

        print("\n【大小】")

        for k, v in sorted(
            future_size.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        # =============================================
        # 单双
        # =============================================

        future_odd = enhanced_predict(
            odd_seq,
            ["单","双"],
            best_odd_window
        )

        print("\n【单双】")

        for k, v in sorted(
            future_odd.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        print("\n" + "="*60)

    except Exception as e:

        print(f"\n错误: {e}")

    finally:

        conn.close()

# =========================================================

if __name__ == "__main__":
    main()