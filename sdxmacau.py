#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# V21-QUANT STATE SWITCH FINAL
#
# 新版核心：
#
# [√] 状态切换检测
# [√] 熵变动检测
# [√] 高频→低频切换
# [√] 连续同波识别
# [√] 连续反转识别
# [√] 动态策略切换
# [√] 不再固定双推
# [√] 不只看波色
# [√] 大小/单双联动
# [√] 混沌模式降权
#
# 目标：
#
# 波色主推:
# 45%~58%
#
# 波色双推:
# 72%~88%
#
# 大小:
# 55%~65%
#
# =========================================================

from __future__ import annotations

import argparse
import json
import math
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
# 熵
# =========================================================

def calc_entropy(seq):

    cnt = Counter(seq)

    total = sum(cnt.values())

    if total == 0:
        return 0

    ent = 0

    for v in cnt.values():

        p = v / total

        ent -= p * math.log(p)

    return ent

# =========================================================
# 状态检测
# =========================================================

def detect_state(color_seq):

    recent = color_seq[-12:]

    entropy = calc_entropy(recent)

    state = {
        "entropy": entropy,
        "same3": False,
        "reverse2": False,
        "chaos": False,
        "hot_to_cold": False
    }

    # 连续3期同波

    if len(color_seq) >= 3:

        if (
            color_seq[-1] ==
            color_seq[-2] ==
            color_seq[-3]
        ):

            state["same3"] = True

    # 连续反转

    if len(color_seq) >= 4:

        a = color_seq[-4:]

        if (
            a[0] != a[1] and
            a[1] != a[2] and
            a[2] != a[3]
        ):

            state["reverse2"] = True

    # 熵过高

    if entropy > 1.06:

        state["chaos"] = True

    # 高频转低频

    long_cnt = Counter(color_seq[-60:])
    short_cnt = Counter(color_seq[-12:])

    long_hot = long_cnt.most_common(1)[0][0]
    short_hot = short_cnt.most_common(1)[0][0]

    if long_hot != short_hot:

        state["hot_to_cold"] = True

    return state

# =========================================================
# 混合模型
# =========================================================

class HybridModel:

    def __init__(self, states, recent=60):

        self.states = states
        self.recent = recent

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

    def predict(self, recent_seq, state):

        probs = {}

        totalg = sum(self.global_cnt.values())

        last1 = recent_seq[-1]
        last2 = tuple(recent_seq[-2:])

        trans1 = self.trans1.get(last1, Counter())
        trans2 = self.trans2.get(last2, Counter())

        total1 = sum(trans1.values())
        total2 = sum(trans2.values())

        # =====================================================
        # 动态权重
        # =====================================================

        if state["chaos"]:

            wg = 0.50
            w1 = 0.35
            w2 = 0.15

        elif state["same3"]:

            wg = 0.25
            w1 = 0.50
            w2 = 0.25

        elif state["reverse2"]:

            wg = 0.30
            w1 = 0.30
            w2 = 0.40

        else:

            wg = 0.20
            w1 = 0.45
            w2 = 0.35

        # =====================================================

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

            probs[s] = (
                wg * pg +
                w1 * p1 +
                w2 * p2
            )

        # =====================================================
        # 连续同波惩罚
        # =====================================================

        if state["same3"]:

            same = recent_seq[-1]

            probs[same] *= 0.62

        # =====================================================
        # 高频失效修复
        # =====================================================

        if state["hot_to_cold"]:

            hot = Counter(recent_seq[-60:]).most_common(1)[0][0]

            probs[hot] *= 0.78

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

def choose_combo(sorted_probs, state):

    a = sorted_probs[0]
    b = sorted_probs[1]

    gap = a[1] - b[1]

    # 混沌状态 → 强制双推

    if state["chaos"]:

        return [a[0], b[0]], "混沌双推"

    # 连续同波 → 反转

    if state["same3"]:

        return [b[0], a[0]], "连续同波反转"

    # 概率接近

    if gap < 0.08:

        return [a[0], b[0]], "动态双推"

    # 正常

    return [a[0]], "单推"

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

    print("\n" + "="*60)
    print("最近10期详细回测")
    print("="*60)

    c1 = 0
    c2 = 0
    s1 = 0
    o1 = 0

    start = len(rows) - args.test

    for t in range(start, len(rows)):

        recent_color = color_seq[:t]

        state = detect_state(recent_color)

        model = HybridModel(
            ["红","蓝","绿"],
            recent=60
        )

        model.fit(recent_color)

        probs = model.predict(
            recent_color[-20:],
            state
        )

        sorted_probs = sorted(
            probs.items(),
            key=lambda x:x[1],
            reverse=True
        )

        combo, mode = choose_combo(
            sorted_probs,
            state
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

        # =====================================================
        # 大小
        # =====================================================

        pred_s = predict_simple(
            size_seq[:t],
            ["大","小"],
            recent=35
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

        # =====================================================
        # 单双
        # =====================================================

        pred_o = predict_simple(
            odd_seq[:t],
            ["单","双"],
            recent=25
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
            f"波色:{'+'.join(combo)} "
            f"| 开:{actual_c} "
            f"| 主推:{'√' if hit1 else '×'} "
            f"| 双推:{'√' if hit2 else '×'} "
            f"| 熵:{state['entropy']:.3f} "
            f"| 模式:{mode} "
            f"| 大小:{main_s}/{actual_s} {'√' if hit_s else '×'} "
            f"| 单双:{main_o}/{actual_o} {'√' if hit_o else '×'}"
        )

    # =========================================================

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

    # =========================================================
    # 下期预测
    # =========================================================

    next_issue = str(
        issue_int(rows[-1]["issue_no"]) + 1
    )

    state = detect_state(color_seq)

    model = HybridModel(
        ["红","蓝","绿"],
        recent=60
    )

    model.fit(color_seq)

    final_probs = model.predict(
        color_seq[-20:],
        state
    )

    sorted_final = sorted(
        final_probs.items(),
        key=lambda x:x[1],
        reverse=True
    )

    combo, mode = choose_combo(
        sorted_final,
        state
    )

    print("\n" + "="*60)
    print(f"下期预测（{next_issue}）")
    print("="*60)

    print("\n【波色】")

    for k,v in sorted_final:

        print(
            f"{k} : {v*100:.2f}%"
        )

    print(
        f"\n推荐: {' + '.join(combo)}"
    )

    print(
        f"策略模式: {mode}"
    )

    print(
        f"系统熵值: {state['entropy']:.4f}"
    )

    if state["chaos"]:

        print("状态: 混沌")

    elif state["same3"]:

        print("状态: 连续同波")

    elif state["reverse2"]:

        print("状态: 高频反转")

    else:

        print("状态: 正常")

    # =========================================================
    # 大小
    # =========================================================

    final_s = predict_simple(
        size_seq,
        ["大","小"],
        recent=35
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

    # =========================================================
    # 单双
    # =========================================================

    final_o = predict_simple(
        odd_seq,
        ["单","双"],
        recent=25
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