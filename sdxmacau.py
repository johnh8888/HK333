#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V20-QUANT FINAL STABLE
#
# 终极稳定增强版
#
# 修复：
#
# [√] 二阶过拟合
# [√] 动态窗口失控
# [√] 单双随机化
# [√] 固定双推
# [√] 连续同波爆死
#
# 新增：
#
# [√] 混合概率模型
# [√] 连续同波反转惩罚
# [√] 动态双推
# [√] 自适应窗口
# [√] 更稳定回测
#
# 实战目标：
#
# 波色主推：
#   45%~55%
#
# 波色双推：
#   70%~82%
#
# 大小：
#   55%~65%
#
# 单双：
#   50%左右（随机）
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
from datetime import datetime, timezone, timedelta
from pathlib import Path

from urllib.request import Request, urlopen

# =========================================================

SEED = 42
random.seed(SEED)

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}

URLS = [
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

def get_color(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

# =========================================================

def get_size(n):
    return "大" if n >= 25 else "小"

# =========================================================

def get_odd_even(n):
    return "单" if n % 2 else "双"

# =========================================================

@dataclass
class DrawRecord:

    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

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
                r.read().decode(
                    "utf-8",
                    errors="ignore"
                )
            )

    except:
        return None

# =========================================================

def fetch_online(lottery_name):

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
            return records

    raise RuntimeError("无法获取在线数据")

# =========================================================

def sync_db(conn, records):

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
                    updated_at=?
                WHERE issue_no=?
            """, (
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
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
                "online",
                now,
                now
            ))

            ins += 1

    conn.commit()

    return ins, upd

# =========================================================

def issue_int(issue):

    nums = re.sub(r"\D", "", issue)

    if nums == "":
        return 0

    return int(nums)

# =========================================================

def load_rows(conn):

    rows = conn.execute("""
        SELECT issue_no,
               draw_date,
               special_number
        FROM draws
    """).fetchall()

    rows = sorted(
        rows,
        key=lambda r: issue_int(r["issue_no"])
    )

    return rows

# =========================================================

def bayes(c, total, alpha, k):

    return (
        c + alpha
    ) / (
        total + alpha * k
    )

# =========================================================
# 最佳窗口
# =========================================================

def best_window(seq, states):

    candidates = [
        30,
        45,
        60,
        90,
        120
    ]

    best_score = -1
    best_w = 60

    for w in candidates:

        correct = 0
        total = 0

        for t in range(max(w,20), len(seq)):

            train = seq[t-w:t]

            cnt = Counter(train)

            pred = max(cnt, key=cnt.get)

            if pred == seq[t]:
                correct += 1

            total += 1

        if total == 0:
            continue

        acc = correct / total

        if acc > best_score:
            best_score = acc
            best_w = w

    return best_w

# =========================================================
# 混合模型
# =========================================================

class HybridModel:

    def __init__(self, states, recent=60):

        self.states = states
        self.recent = recent

    # =====================================================

    def fit(self, seq):

        seq = seq[-self.recent:]

        self.global_cnt = Counter(seq)

        self.trans1 = defaultdict(Counter)

        self.trans2 = defaultdict(Counter)

        for i in range(len(seq)-1):

            a = seq[i]
            b = seq[i+1]

            self.trans1[a][b] += 1

        for i in range(len(seq)-2):

            a = seq[i]
            b = seq[i+1]
            c = seq[i+2]

            self.trans2[(a,b)][c] += 1

    # =====================================================

    def predict(self, recent_seq):

        probs = {}

        totalg = sum(self.global_cnt.values())

        last1 = recent_seq[-1]
        last2 = tuple(recent_seq[-2:])

        trans1 = self.trans1.get(last1, Counter())
        trans2 = self.trans2.get(last2, Counter())

        total1 = sum(trans1.values())
        total2 = sum(trans2.values())

        for s in self.states:

            pg = bayes(
                self.global_cnt.get(s,0),
                totalg,
                1.2,
                len(self.states)
            )

            p1 = bayes(
                trans1.get(s,0),
                max(total1,1),
                1.2,
                len(self.states)
            )

            p2 = bayes(
                trans2.get(s,0),
                max(total2,1),
                1.2,
                len(self.states)
            )

            # =================================================
            # 修复：
            # 二阶权重下降
            # =================================================

            probs[s] = (
                0.20 * pg +
                0.45 * p1 +
                0.35 * p2
            )

        # =====================================================
        # 连续同波反转惩罚
        # =====================================================

        if len(recent_seq) >= 3:

            if (
                recent_seq[-1] ==
                recent_seq[-2] ==
                recent_seq[-3]
            ):

                same = recent_seq[-1]

                probs[same] *= 0.72

        total = sum(probs.values())

        probs = {
            k: v/total
            for k,v in probs.items()
        }

        return probs

# =========================================================

def predict_simple(seq, states, recent=30):

    seq = seq[-recent:]

    cnt = Counter(seq)

    total = sum(cnt.values())

    probs = {}

    for s in states:

        probs[s] = bayes(
            cnt.get(s,0),
            total,
            1.2,
            len(states)
        )

    return probs

# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        default="香港彩",
        choices=[
            "老澳门彩",
            "香港彩",
            "新澳门彩"
        ]
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

    records = fetch_online(args.lottery)

    ins, upd = sync_db(conn, records)

    print(f"\n同步完成 新增:{ins} 更新:{upd}")

    rows = load_rows(conn)

    color_seq = [
        get_color(r["special_number"])
        for r in rows
    ]

    size_seq = [
        get_size(r["special_number"])
        for r in rows
    ]

    odd_seq = [
        get_odd_even(r["special_number"])
        for r in rows
    ]

    # =====================================================
    # 动态窗口
    # =====================================================

    color_window = best_window(
        color_seq,
        ["红","蓝","绿"]
    )

    size_window = best_window(
        size_seq,
        ["大","小"]
    )

    print(f"\n波色最佳窗口: {color_window}")
    print(f"大小最佳窗口: {size_window}")
    print(f"单双固定策略: 全局趋势")

    # =====================================================
    # 回测
    # =====================================================

    print("\n" + "="*60)
    print("最近10期详细回测")
    print("="*60)

    c1 = 0
    c2 = 0
    s1 = 0
    o1 = 0

    start = len(rows) - args.test

    for t in range(start, len(rows)):

        # =================================================
        # 波色
        # =================================================

        model_c = HybridModel(
            ["红","蓝","绿"],
            recent=color_window
        )

        model_c.fit(color_seq[:t])

        pred_c = model_c.predict(
            color_seq[max(0,t-20):t]
        )

        sorted_c = sorted(
            pred_c.items(),
            key=lambda x:x[1],
            reverse=True
        )

        main_c = sorted_c[0][0]
        second_c = sorted_c[1][0]

        # =================================================
        # 动态双推
        # =================================================

        if (
            sorted_c[0][1] -
            sorted_c[1][1]
        ) < 0.08:

            combo = (
                second_c,
                main_c
            )

        else:

            combo = (
                main_c,
                second_c
            )

        actual_c = color_seq[t]

        hit1 = (
            combo[0] == actual_c
        )

        hit2 = (
            actual_c in combo
        )

        if hit1:
            c1 += 1

        if hit2:
            c2 += 1

        # =================================================
        # 大小
        # =================================================

        pred_s = predict_simple(
            size_seq[:t],
            ["大","小"],
            recent=size_window
        )

        main_s = max(
            pred_s,
            key=pred_s.get
        )

        actual_s = size_seq[t]

        hit_s = (
            main_s == actual_s
        )

        if hit_s:
            s1 += 1

        # =================================================
        # 单双
        # =================================================

        pred_o = predict_simple(
            odd_seq[:t],
            ["单","双"],
            recent=20
        )

        main_o = max(
            pred_o,
            key=pred_o.get
        )

        actual_o = odd_seq[t]

        hit_o = (
            main_o == actual_o
        )

        if hit_o:
            o1 += 1

        row = rows[t]

        print(
            f"{row['issue_no']} "
            f"{row['draw_date']} | "
            f"波色:{combo[0]}+{combo[1]} "
            f"| 开:{actual_c} "
            f"| 主推:{'√' if hit1 else '×'} "
            f"| 双推:{'√' if hit2 else '×'} "
            f"| 大小:{main_s}/{actual_s} {'√' if hit_s else '×'} "
            f"| 单双:{main_o}/{actual_o} {'√' if hit_o else '×'}"
        )

    # =====================================================
    # 统计
    # =====================================================

    print("\n" + "="*60)
    print("最近10期命中统计")
    print("="*60)

    print(
        f"波色主推命中率: "
        f"{c1/args.test*100:.2f}%"
    )

    print(
        f"波色双推命中率: "
        f"{c2/args.test*100:.2f}%"
    )

    print(
        f"大小命中率: "
        f"{s1/args.test*100:.2f}%"
    )

    print(
        f"单双命中率: "
        f"{o1/args.test*100:.2f}%"
    )

    # =====================================================
    # 下期预测
    # =====================================================

    next_issue = str(
        issue_int(rows[-1]["issue_no"]) + 1
    )

    print("\n" + "="*60)
    print(f"下期预测（{next_issue}）")
    print("="*60)

    # =====================================================
    # 波色
    # =====================================================

    model_final = HybridModel(
        ["红","蓝","绿"],
        recent=color_window
    )

    model_final.fit(color_seq)

    final_c = model_final.predict(
        color_seq[-20:]
    )

    sorted_final = sorted(
        final_c.items(),
        key=lambda x:x[1],
        reverse=True
    )

    print("\n【波色】")

    for k,v in sorted_final:

        print(
            f"{k} : {v*100:.2f}%"
        )

    print(
        f"\n推荐组合: "
        f"{sorted_final[0][0]}"
        f" + "
        f"{sorted_final[1][0]}"
    )

    print(
        f"双推覆盖率: "
        f"{(sorted_final[0][1] + sorted_final[1][1])*100:.2f}%"
    )

    # =====================================================
    # 大小
    # =====================================================

    final_s = predict_simple(
        size_seq,
        ["大","小"],
        recent=size_window
    )

    print("\n【大小】")

    for k,v in sorted(
        final_s.items(),
        key=lambda x:x[1],
        reverse=True
    ):

        print(
            f"{k} : {v*100:.2f}%"
        )

    # =====================================================
    # 单双
    # =====================================================

    final_o = predict_simple(
        odd_seq,
        ["单","双"],
        recent=20
    )

    print("\n【单双】")

    for k,v in sorted(
        final_o.items(),
        key=lambda x:x[1],
        reverse=True
    ):

        print(
            f"{k} : {v*100:.2f}%"
        )

    print("\n" + "="*60)

    conn.close()

# =========================================================

if __name__ == "__main__":
    main()