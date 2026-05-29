#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V16-LITE FINAL STABLE
#
# 真正稳定精简版（推荐长期使用）
#
# 修复内容：
#
# [√] 删除 temperature calibration
# [√] 删除 entropy gating
# [√] 删除 Kelly bankroll
# [√] 删除 regime detection
#
# 保留：
#
# [√] Conditional Markov
# [√] Bayesian smoothing
# [√] WalkForward 回测
# [√] 色波主推
# [√] 色波双推
# [√] 最近10期详细统计
# [√] 大小单推
# [√] 单双单推
#
# 特点：
#
# - 更稳定
# - 更真实
# - 更少过拟合
# - 更低算力
# - 更清晰统计
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
# 固定随机种子
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
# 属性函数
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

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_issue
        ON draws(issue_no)
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
# 在线获取数据
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

        try:

            latest_time = datetime.strptime(
                target.get("openTime", ""),
                "%Y-%m-%d %H:%M:%S"
            )

        except:

            latest_time = datetime.now()

        records = []

        for idx, item in enumerate(
            target.get("history", [])
        ):

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
                    latest_time - timedelta(days=idx)
                ).strftime("%Y-%m-%d")

                records.append(
                    DrawRecord(
                        issue_no,
                        draw_date,
                        nums[:6],
                        nums[6]
                    )
                )

            except:
                continue

        if records:
            return records, "marksix6"

    raise RuntimeError("无法获取在线数据")

# =========================================================
# 同步数据
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

    return len(records), ins, upd

# =========================================================
# issue 修复
# =========================================================

def issue_to_int(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if nums == "":
        return 0

    return int(nums)

# =========================================================
# 加载序列
# =========================================================

def load_sequence(conn, attr_func):

    rows = conn.execute("""
        SELECT issue_no, special_number
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_to_int(
            r["issue_no"]
        )
    )

    return [
        attr_func(r["special_number"])
        for r in rows
    ]

# =========================================================
# Bayesian smoothing
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

        self.transitions1 = defaultdict(
            Counter
        )

        self.transitions2 = defaultdict(
            Counter
        )

    # =====================================================

    def train(self, seq):

        seq = seq[-self.recent_periods:]

        # 全局统计

        for age, i in enumerate(
            reversed(range(len(seq)))
        ):

            s = seq[i]

            w = self.decay ** age

            self.global_counts[s] += w

        # 转移统计

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

        # 二阶优先

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
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description="V16-LITE FINAL STABLE"
    )

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

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    try:

        # =================================================
        # 同步
        # =================================================

        records, source = fetch_online_records(
            args.lottery
        )

        total, ins, upd = sync_from_records(
            conn,
            records,
            source
        )

        print(
            f"\n{args.lottery} 同步完成 "
            f"总:{total} 新增:{ins} 更新:{upd}"
        )

        # =================================================
        # 序列
        # =================================================

        color_seq = load_sequence(
            conn,
            get_color
        )

        size_seq = load_sequence(
            conn,
            get_big_small
        )

        odd_seq = load_sequence(
            conn,
            get_odd_even
        )

        test_len = min(
            args.test,
            len(color_seq) - 40
        )

        start = len(color_seq) - test_len

        # =================================================
        # 统计
        # =================================================

        color_single_correct = 0
        color_double_correct = 0

        size_correct = 0
        odd_correct = 0

        recent_records = []

        # =================================================
        # WalkForward
        # =================================================

        for t in range(start, len(color_seq)):

            # =================================================
            # 色波
            # =================================================

            eng_c = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=args.recent
            )

            eng_c.train(
                color_seq[:t]
            )

            pred_c = eng_c.predict(
                color_seq[max(0, t-30):t]
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

            if single_hit:
                color_single_correct += 1

            if double_hit:
                color_double_correct += 1

            # =================================================
            # 大小
            # =================================================

            eng_s = ConditionalMarkov(
                ["大","小"],
                recent_periods=args.recent
            )

            eng_s.train(
                size_seq[:t]
            )

            pred_s = eng_s.predict(
                size_seq[max(0, t-30):t]
            )

            main_size = max(
                pred_s,
                key=pred_s.get
            )

            actual_size = size_seq[t]

            size_hit = (
                main_size == actual_size
            )

            if size_hit:
                size_correct += 1

            # =================================================
            # 单双
            # =================================================

            eng_o = ConditionalMarkov(
                ["单","双"],
                recent_periods=args.recent
            )

            eng_o.train(
                odd_seq[:t]
            )

            pred_o = eng_o.predict(
                odd_seq[max(0, t-30):t]
            )

            main_odd = max(
                pred_o,
                key=pred_o.get
            )

            actual_odd = odd_seq[t]

            odd_hit = (
                main_odd == actual_odd
            )

            if odd_hit:
                odd_correct += 1

            # =================================================
            # 保存最近回测
            # =================================================

            recent_records.append({

                "期号": t + 1,

                "色波主推": main_color,
                "色波次推": second_color,
                "实际色波": actual_color,
                "色波主推命中": (
                    "✅" if single_hit else "❌"
                ),
                "色波双推命中": (
                    "✅" if double_hit else "❌"
                ),

                "大小预测": main_size,
                "实际大小": actual_size,
                "大小命中": (
                    "✅" if size_hit else "❌"
                ),

                "单双预测": main_odd,
                "实际单双": actual_odd,
                "单双命中": (
                    "✅" if odd_hit else "❌"
                )
            })

        # =================================================
        # 输出统计
        # =================================================

        print("\n" + "="*100)
        print(f"V16-LITE FINAL STABLE 回测 ({test_len}期)")
        print("="*100)

        print("\n【色波】")

        print(
            f"主推命中率 : "
            f"{color_single_correct/test_len*100:.2f}%"
        )

        print(
            f"双推命中率 : "
            f"{color_double_correct/test_len*100:.2f}%"
        )

        print("\n【大小】")

        print(
            f"单推命中率 : "
            f"{size_correct/test_len*100:.2f}%"
        )

        print("\n【单双】")

        print(
            f"单推命中率 : "
            f"{odd_correct/test_len*100:.2f}%"
        )

        # =================================================
        # 最近10期
        # =================================================

        print("\n" + "="*100)
        print("最近10期详细回测")
        print("="*100)

        for r in recent_records:

            print(f"\n第{r['期号']}期")

            print(
                f"色波: "
                f"{r['色波主推']} + "
                f"{r['色波次推']} "
                f"| 实际:{r['实际色波']} "
                f"| 主推:{r['色波主推命中']} "
                f"| 双推:{r['色波双推命中']}"
            )

            print(
                f"大小: "
                f"{r['大小预测']} "
                f"| 实际:{r['实际大小']} "
                f"| {r['大小命中']}"
            )

            print(
                f"单双: "
                f"{r['单双预测']} "
                f"| 实际:{r['实际单双']} "
                f"| {r['单双命中']}"
            )

        # =================================================
        # 下期预测
        # =================================================

        print("\n" + "="*100)
        print("下期预测")
        print("="*100)

        # =================================================
        # 色波
        # =================================================

        final_c = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=args.recent
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

        print("\n【色波】")

        for i, (k, v) in enumerate(
            sorted_future_color
        ):

            tag = (
                "【主推】"
                if i == 0
                else "【次推】"
            )

            print(
                f"{tag} "
                f"{k} : {v*100:.2f}%"
            )

        print(
            f"\n推荐组合："
            f"{sorted_future_color[0][0]}"
            f" + "
            f"{sorted_future_color[1][0]}"
        )

        print(
            f"双推覆盖率："
            f"{(sorted_future_color[0][1] + sorted_future_color[1][1])*100:.2f}%"
        )

        # =================================================
        # 大小
        # =================================================

        final_s = ConditionalMarkov(
            ["大","小"],
            recent_periods=args.recent
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

        # =================================================
        # 单双
        # =================================================

        final_o = ConditionalMarkov(
            ["单","双"],
            recent_periods=args.recent
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

        print("\n" + "="*100)

    except Exception as e:

        print(f"\n错误: {e}")

    finally:

        conn.close()

# =========================================================

if __name__ == "__main__":

    main()