#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V18-QUANT STABLE FINAL
#
# 核心稳定增强版
#
# 修复：
#
# [√] 动态窗口过拟合
# [√] 二阶状态稀疏
# [√] 单双0%问题
# [√] 最近10期详细回测
# [√] 下期真实期号
# [√] 在线同步最新数据
# [√] 日期修复
#
# 核心策略：
#
# 波色：
#   40% 一阶Markov
#   30% 全局频率
#   30% 均值回归
#
# 大小：
#   70% 近期频率
#   30% 一阶Markov
#
# 单双：
#   100% 近期频率
#
# 不再使用：
#
# [X] 二阶大小单双
# [X] entropy
# [X] temperature
# [X] 复杂AI幻觉优化
#
# =========================================================

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# =========================================================
# 固定随机种子
# =========================================================

SEED = 42
random.seed(SEED)

# =========================================================
# 路径
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "香港彩": "hk_macau.db",
    "老澳门彩": "old_macau.db",
    "新澳门彩": "xin_macau.db"
}

# =========================================================
# API
# =========================================================

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

            text = resp.read().decode(
                "utf-8",
                errors="ignore"
            )

            return json.loads(text)

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

        latest_time_str = target.get(
            "openTime",
            ""
        )

        try:

            latest_time = datetime.strptime(
                latest_time_str,
                "%Y-%m-%d %H:%M:%S"
            )

        except:

            latest_time = datetime.now()

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
                    latest_time - timedelta(days=idx)
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

    raise RuntimeError("无法获取在线数据")

# =========================================================
# 同步数据库
# =========================================================

def sync_records(conn, records, source):

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

    if nums == "":
        return 0

    return int(nums)

# =========================================================

def load_draws(conn):

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
# 贝叶斯平滑
# =========================================================

def smooth_prob(counter, states, alpha=1.2):

    total = sum(counter.values())

    probs = {}

    for s in states:

        probs[s] = (
            counter.get(s,0) + alpha
        ) / (
            total + alpha * len(states)
        )

    return probs

# =========================================================
# 一阶Markov
# =========================================================

class FirstOrderMarkov:

    def __init__(
        self,
        states,
        recent_periods=120,
        decay=0.996
    ):

        self.states = states
        self.recent_periods = recent_periods
        self.decay = decay

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

    # =====================================================

    def predict(self, recent):

        if not recent:

            return {
                s: 1/len(self.states)
                for s in self.states
            }

        last = recent[-1]

        trans = self.transitions.get(
            last,
            Counter()
        )

        return smooth_prob(
            trans,
            self.states
        )

# =========================================================
# 波色预测
# =========================================================

def predict_color(seq):

    states = ["红","蓝","绿"]

    # 一阶Markov

    eng = FirstOrderMarkov(
        states,
        recent_periods=120
    )

    eng.train(seq)

    markov = eng.predict(seq[-30:])

    # 全局频率

    global_counter = Counter(
        seq[-120:]
    )

    global_probs = smooth_prob(
        global_counter,
        states
    )

    # 均值回归

    recent20 = Counter(
        seq[-20:]
    )

    revert = {}

    for s in states:

        base = 1 / 3

        freq = recent20.get(s,0) / 20

        # 出现越多 -> 惩罚越大

        revert[s] = max(
            0.05,
            base - (freq - base) * 0.6
        )

    total_r = sum(revert.values())

    revert = {
        k: v / total_r
        for k,v in revert.items()
    }

    # 融合

    final_probs = {}

    for s in states:

        final_probs[s] = (
            markov[s] * 0.40 +
            global_probs[s] * 0.30 +
            revert[s] * 0.30
        )

    total = sum(final_probs.values())

    return {
        k: v / total
        for k,v in final_probs.items()
    }

# =========================================================
# 大小预测
# =========================================================

def predict_size(seq):

    states = ["大","小"]

    # 近期频率

    recent_counter = Counter(
        seq[-60:]
    )

    recent_probs = smooth_prob(
        recent_counter,
        states
    )

    # 一阶

    eng = FirstOrderMarkov(
        states,
        recent_periods=90
    )

    eng.train(seq)

    markov = eng.predict(seq[-20:])

    # 融合

    final_probs = {}

    for s in states:

        final_probs[s] = (
            recent_probs[s] * 0.70 +
            markov[s] * 0.30
        )

    total = sum(final_probs.values())

    return {
        k: v / total
        for k,v in final_probs.items()
    }

# =========================================================
# 单双预测
# =========================================================

def predict_odd(seq):

    states = ["单","双"]

    recent_counter = Counter(
        seq[-80:]
    )

    return smooth_prob(
        recent_counter,
        states
    )

# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        choices=["香港彩","老澳门彩","新澳门彩"],
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
        # 在线同步
        # =================================================

        records, source = fetch_online_records(
            args.lottery
        )

        ins, upd = sync_records(
            conn,
            records,
            source
        )

        print(
            f"\n同步完成 新增:{ins} 更新:{upd}"
        )

        # =================================================
        # 加载
        # =================================================

        rows = load_draws(conn)

        issues = [
            r["issue_no"]
            for r in rows
        ]

        dates = [
            r["draw_date"]
            for r in rows
        ]

        colors = [
            get_color(r["special_number"])
            for r in rows
        ]

        sizes = [
            get_big_small(r["special_number"])
            for r in rows
        ]

        odds = [
            get_odd_even(r["special_number"])
            for r in rows
        ]

        # =================================================
        # 最近10期回测
        # =================================================

        print("\n" + "="*60)
        print("最近10期详细回测")
        print("="*60)

        start = len(colors) - args.test

        color_single_hit = 0
        color_double_hit = 0

        size_hit = 0
        odd_hit = 0

        for t in range(start, len(colors)):

            # 波色

            pred_c = predict_color(
                colors[:t]
            )

            sorted_c = sorted(
                pred_c.items(),
                key=lambda x: x[1],
                reverse=True
            )

            main_c = sorted_c[0][0]
            second_c = sorted_c[1][0]

            actual_c = colors[t]

            hit_single = (
                main_c == actual_c
            )

            hit_double = (
                actual_c in [
                    main_c,
                    second_c
                ]
            )

            if hit_single:
                color_single_hit += 1

            if hit_double:
                color_double_hit += 1

            # 大小

            pred_s = predict_size(
                sizes[:t]
            )

            main_s = max(
                pred_s,
                key=pred_s.get
            )

            actual_s = sizes[t]

            hit_s = (
                main_s == actual_s
            )

            if hit_s:
                size_hit += 1

            # 单双

            pred_o = predict_odd(
                odds[:t]
            )

            main_o = max(
                pred_o,
                key=pred_o.get
            )

            actual_o = odds[t]

            hit_o = (
                main_o == actual_o
            )

            if hit_o:
                odd_hit += 1

            print(
                f"{issues[t]} {dates[t]} | "
                f"波色:{main_c}+{second_c} "
                f"| 开:{actual_c} "
                f"| 主推:{'√' if hit_single else '×'} "
                f"| 双推:{'√' if hit_double else '×'} "
                f"| 大小:{main_s}/{actual_s} "
                f"{'√' if hit_s else '×'} "
                f"| 单双:{main_o}/{actual_o} "
                f"{'√' if hit_o else '×'}"
            )

        # =================================================
        # 命中率
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

        next_issue = str(
            issue_to_int(issues[-1]) + 1
        )

        print("\n" + "="*60)
        print(f"下期预测（{next_issue}）")
        print("="*60)

        # 波色

        future_c = predict_color(colors)

        sorted_fc = sorted(
            future_c.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print("\n【波色】")

        for k,v in sorted_fc:

            print(
                f"{k} : {v*100:.2f}%"
            )

        print(
            f"\n推荐组合: "
            f"{sorted_fc[0][0]} + "
            f"{sorted_fc[1][0]}"
        )

        print(
            f"双推覆盖率: "
            f"{(sorted_fc[0][1] + sorted_fc[1][1])*100:.2f}%"
        )

        # 大小

        future_s = predict_size(sizes)

        print("\n【大小】")

        for k,v in sorted(
            future_s.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        # 单双

        future_o = predict_odd(odds)

        print("\n【单双】")

        for k,v in sorted(
            future_o.items(),
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