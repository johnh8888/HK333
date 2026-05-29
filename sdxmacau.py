#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
# 数据库
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

@dataclass
class DrawRecord:

    issue_no: str
    draw_date: str
    numbers: list
    special_number: int

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

    raise RuntimeError("无法获取数据")

# =========================================================

def sync_from_records(conn, records, source):

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

    conn.commit()

# =========================================================

def issue_to_int(issue_no):

    nums = re.sub(r"\D", "", issue_no)

    if nums == "":
        return 0

    return int(nums)

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

def dynamic_recent_window(seq):

    recent = seq[-20:]

    counts = Counter(recent)

    mx = max(counts.values())

    if mx >= 11:
        return 120

    elif mx >= 8:
        return 180

    return 240

# =========================================================

def hot_cold_score(seq, state):

    recent = seq[-20:]

    count = recent.count(state)

    return count / 20

# =========================================================

def abnormal_run(seq):

    if len(seq) < 5:
        return False

    last = seq[-1]

    cnt = 0

    for x in reversed(seq):

        if x == last:
            cnt += 1
        else:
            break

    return cnt >= 5

# =========================================================

class EnsembleMarkov:

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

        self.trans1 = defaultdict(Counter)
        self.trans2 = defaultdict(Counter)

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

            self.trans2[(a,b)][c] += w
            self.trans1[b][c] += w

    # =====================================================

    def normalize(self, probs):

        s = sum(probs.values())

        return {
            k: v / s
            for k, v in probs.items()
        }

    # =====================================================

    def predict(self, recent, seq):

        if len(recent) < 2:

            return {
                s: 1 / len(self.states)
                for s in self.states
            }

        a = recent[-2]
        b = recent[-1]

        p2 = {}
        p1 = {}
        pg = {}

        t2 = self.trans2.get(
            (a,b),
            Counter()
        )

        t1 = self.trans1.get(
            b,
            Counter()
        )

        total2 = sum(t2.values())
        total1 = sum(t1.values())
        totalg = sum(self.global_counts.values())

        for s in self.states:

            p2[s] = (
                t2.get(s,0) + self.alpha
            ) / (
                total2 + self.alpha * len(self.states)
            )

            p1[s] = (
                t1.get(s,0) + self.alpha
            ) / (
                total1 + self.alpha * len(self.states)
            )

            pg[s] = (
                self.global_counts.get(s,0) + self.alpha
            ) / (
                totalg + self.alpha * len(self.states)
            )

        p2 = self.normalize(p2)
        p1 = self.normalize(p1)
        pg = self.normalize(pg)

        final_probs = {}

        for s in self.states:

            final_probs[s] = (
                0.5 * p2[s]
                +
                0.3 * p1[s]
                +
                0.2 * pg[s]
            )

            final_probs[s] += (
                hot_cold_score(seq, s) * 0.03
            )

        if abnormal_run(seq):

            for s in final_probs:
                final_probs[s] *= 0.92

        return self.normalize(final_probs)

# =========================================================

def strength_stars(diff):

    if diff >= 0.18:
        return "★★★★★"

    if diff >= 0.12:
        return "★★★★☆"

    if diff >= 0.08:
        return "★★★☆☆"

    if diff >= 0.04:
        return "★★☆☆☆"

    return "★☆☆☆☆"

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

    conn = connect_db(
        SCRIPT_DIR / DB_FILES[args.lottery]
    )

    init_db(conn)

    records, source = fetch_online_records(
        args.lottery
    )

    sync_from_records(
        conn,
        records,
        source
    )

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

    recent_window = dynamic_recent_window(
        color_seq
    )

    print("\n==================================================")
    print(f"{args.lottery}")
    print("==================================================")

    print(f"动态窗口: {recent_window}")

    # =====================================================
    # 最近20期趋势
    # =====================================================

    recent20 = color_seq[-20:]

    trend = Counter(recent20)

    print("\n最近20期趋势:")

    for k, v in trend.items():

        print(f"{k}: {v}")

    # =====================================================
    # 回测最近10期
    # =====================================================

    print("\n==================================================")
    print("最近10期回测")
    print("==================================================")

    single_hit = 0
    double_hit = 0

    for t in range(len(color_seq)-args.test, len(color_seq)):

        train_seq = color_seq[:t]

        model = EnsembleMarkov(
            ["红","蓝","绿"],
            recent_periods=recent_window
        )

        model.train(train_seq)

        probs = model.predict(
            train_seq[-30:],
            train_seq
        )

        sorted_probs = sorted(
            probs.items(),
            key=lambda x: x[1],
            reverse=True
        )

        main_pick = sorted_probs[0][0]
        second_pick = sorted_probs[1][0]

        actual = color_seq[t]

        single_ok = main_pick == actual
        double_ok = actual in [main_pick, second_pick]

        if single_ok:
            single_hit += 1

        if double_ok:
            double_hit += 1

        print(
            f"第{t+1}期 "
            f"主推:{main_pick} "
            f"次推:{second_pick} "
            f"开奖:{actual} "
            f"| 单推:{'√' if single_ok else '×'} "
            f"| 双推:{'√' if double_ok else '×'}"
        )

    print("\n--------------------------------------------------")

    print(
        f"单推命中率: "
        f"{single_hit/args.test*100:.2f}%"
    )

    print(
        f"双推命中率: "
        f"{double_hit/args.test*100:.2f}%"
    )

    # =====================================================
    # 下期预测
    # =====================================================

    model = EnsembleMarkov(
        ["红","蓝","绿"],
        recent_periods=recent_window
    )

    model.train(color_seq)

    probs = model.predict(
        color_seq[-30:],
        color_seq
    )

    sorted_probs = sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    main_color = sorted_probs[0][0]
    second_color = sorted_probs[1][0]

    diff = (
        sorted_probs[0][1]
        -
        sorted_probs[1][1]
    )

    stars = strength_stars(diff)

    print("\n==================================================")
    print("下期预测")
    print("==================================================")

    print("\n【色波】")

    for i, (k, v) in enumerate(sorted_probs):

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
        f"\n主推强度: {stars}"
    )

    print(
        f"双推覆盖: "
        f"{(sorted_probs[0][1] + sorted_probs[1][1])*100:.2f}%"
    )

    print(
        f"推荐组合: "
        f"{main_color} + {second_color}"
    )

    # =====================================================
    # 大小
    # =====================================================

    size_model = EnsembleMarkov(
        ["大","小"],
        recent_periods=recent_window
    )

    size_model.train(size_seq)

    size_probs = size_model.predict(
        size_seq[-30:],
        size_seq
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

    # =====================================================
    # 单双
    # =====================================================

    odd_model = EnsembleMarkov(
        ["单","双"],
        recent_periods=recent_window
    )

    odd_model.train(odd_seq)

    odd_probs = odd_model.predict(
        odd_seq[-30:],
        odd_seq
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

    print("\n==================================================")

    conn.close()

# =========================================================

if __name__ == "__main__":

    main()