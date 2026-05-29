#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V17-QUANT DYNAMIC STABLE
#
# 动态窗口增强版
#
# 功能：
#
# [√] 自动线上同步最新开奖
# [√] 自动保存 SQLite
# [√] 动态窗口 recent_periods
# [√] Conditional Markov
# [√] Bayesian smoothing
# [√] 波色预测
# [√] 大小预测
# [√] 单双预测
# [√] 最近10期回测
# [√] 主推/双推命中
# [√] 下期预测
# [√] 自动计算下期期号
#
# =========================================================

from __future__ import annotations

import argparse
import json
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
# 动态窗口
# =========================================================

def calc_dynamic_window(seq):

    if len(seq) < 80:
        return max(24, len(seq))

    recent = seq[-30:]

    counts = Counter(recent)

    mx = max(counts.values())

    ratio = mx / len(recent)

    if ratio >= 0.55:
        return 36

    if ratio >= 0.45:
        return 72

    if ratio >= 0.40:
        return 120

    if ratio >= 0.36:
        return 180

    return 240

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

                issue_no = parts[0].strip()

                nums = [
                    int(x.strip())
                    for x in parts[1].split(",")
                ]

                if len(nums) != 7:
                    continue

                draw_date = (
                    datetime.now() - timedelta(days=idx)
                ).strftime("%Y-%m-%d")

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

    raise RuntimeError("无法获取线上数据")

# =========================================================
# 同步
# =========================================================

def sync_from_records(conn, records, source):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    inserted = 0
    updated = 0

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

            updated += 1

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

            inserted += 1

    conn.commit()

    return inserted, updated

# =========================================================

def issue_to_int(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if nums == "":
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

def bayesian_prob(
    count,
    total,
    alpha,
    states
):

    return (
        count + alpha
    ) / (
        total + alpha * states
    )

# =========================================================
# Conditional Markov
# =========================================================

class ConditionalMarkov:

    def __init__(
        self,
        states,
        alpha=1.2,
        decay=0.995,
        recent_periods=240
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

        if total2 >= 8:
            base = trans2
            total = total2

        elif total1 >= 5:
            base = trans1
            total = total1

        else:
            base = self.global_counts
            total = totalg

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

        issue_seq = [
            r["issue_no"]
            for r in rows
        ]

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
            len(color_seq)-5
        )

        start = len(color_seq) - test_len

        print(
            f"\n动态窗口: "
            f"{calc_dynamic_window(color_seq)}"
        )

        print("\n" + "="*60)
        print(f"最近{test_len}期回测")
        print("="*60)

        for t in range(start, len(color_seq)):

            # 波色

            eng_c = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=calc_dynamic_window(
                    color_seq[:t]
                )
            )

            eng_c.train(color_seq[:t])

            pred_c = eng_c.predict(
                color_seq[max(0,t-30):t]
            )

            sorted_color = sorted(
                pred_c.items(),
                key=lambda x: x[1],
                reverse=True
            )

            main_color = sorted_color[0][0]
            second_color = sorted_color[1][0]

            actual_color = color_seq[t]

            single_hit = (
                main_color == actual_color
            )

            double_hit = (
                actual_color in [
                    main_color,
                    second_color
                ]
            )

            # 大小

            eng_s = ConditionalMarkov(
                ["大","小"],
                recent_periods=calc_dynamic_window(
                    size_seq[:t]
                )
            )

            eng_s.train(size_seq[:t])

            pred_s = eng_s.predict(
                size_seq[max(0,t-30):t]
            )

            main_size = max(
                pred_s,
                key=pred_s.get
            )

            actual_size = size_seq[t]

            size_hit = (
                main_size == actual_size
            )

            # 单双

            eng_o = ConditionalMarkov(
                ["单","双"],
                recent_periods=calc_dynamic_window(
                    odd_seq[:t]
                )
            )

            eng_o.train(odd_seq[:t])

            pred_o = eng_o.predict(
                odd_seq[max(0,t-30):t]
            )

            main_odd = max(
                pred_o,
                key=pred_o.get
            )

            actual_odd = odd_seq[t]

            odd_hit = (
                main_odd == actual_odd
            )

            print(
                f"{issue_seq[t]} | "
                f"波色:{main_color}+{second_color} | "
                f"开:{actual_color} | "
                f"主推:{'√' if single_hit else '×'} | "
                f"双推:{'√' if double_hit else '×'} | "
                f"大小:{main_size}/{actual_size} "
                f"{'√' if size_hit else '×'} | "
                f"单双:{main_odd}/{actual_odd} "
                f"{'√' if odd_hit else '×'}"
            )

        # =================================================
        # 下期预测
        # =================================================

        next_issue = str(
            issue_to_int(issue_seq[-1]) + 1
        )

        print("\n" + "="*60)
        print(f"下期预测（{next_issue}）")
        print("="*60)

        # 波色

        final_c = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=calc_dynamic_window(
                color_seq
            )
        )

        final_c.train(color_seq)

        future_color = final_c.predict(
            color_seq[-30:]
        )

        sorted_future_color = sorted(
            future_color.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print("\n【波色】")

        for k, v in sorted_future_color:

            print(
                f"{k} : {v*100:.2f}%"
            )

        print(
            f"\n推荐组合: "
            f"{sorted_future_color[0][0]}"
            f" + "
            f"{sorted_future_color[1][0]}"
        )

        print(
            f"双推覆盖率: "
            f"{(sorted_future_color[0][1] + sorted_future_color[1][1])*100:.2f}%"
        )

        # 大小

        final_s = ConditionalMarkov(
            ["大","小"],
            recent_periods=calc_dynamic_window(
                size_seq
            )
        )

        final_s.train(size_seq)

        future_size = final_s.predict(
            size_seq[-30:]
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

        # 单双

        final_o = ConditionalMarkov(
            ["单","双"],
            recent_periods=calc_dynamic_window(
                odd_seq
            )
        )

        final_o.train(odd_seq)

        future_odd = final_o.predict(
            odd_seq[-30:]
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