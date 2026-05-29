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
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# =========================================================
# 固定随机种子
# =========================================================

SEED = 42
random.seed(SEED)

SCRIPT_DIR = Path(__file__).resolve().parent

DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
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

def fetch_online_records(lottery_name):

    for url in API_URLS:

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

        records = []

        history = target.get(
            "history",
            []
        )

        for item in history:

            try:

                item = item.strip()

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
                )[:10]

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

def sync_db(conn, records, source):

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

def issue_to_int(issue):

    n = re.sub(r"\D", "", issue)

    return int(n) if n else 0

# =========================================================

def load_rows(conn):

    rows = conn.execute("""
    SELECT issue_no, draw_date, special_number
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

def build_sequence(rows, func):

    return [
        func(r["special_number"])
        for r in rows
    ]

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
                s: 1/len(self.states)
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
        totalg = sum(
            self.global_counts.values()
        )

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

            probs[s] = (
                base.get(s,0) + self.alpha
            ) / (
                total + self.alpha * len(self.states)
            )

        t = sum(probs.values())

        return {
            k: v/t
            for k,v in probs.items()
        }

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

    db_path = SCRIPT_DIR / DB_FILES[args.lottery]

    conn = connect_db(db_path)

    init_db(conn)

    print("="*60)
    print(args.lottery)
    print("="*60)

    try:

        records, source = fetch_online_records(
            args.lottery
        )

        ins, upd = sync_db(
            conn,
            records,
            source
        )

        print(
            f"\n同步完成 新增:{ins} 更新:{upd}"
        )

        rows = load_rows(conn)

        if len(rows) < 30:

            raise RuntimeError(
                "历史数据不足"
            )

        color_seq = build_sequence(
            rows,
            get_color
        )

        size_seq = build_sequence(
            rows,
            get_big_small
        )

        odd_seq = build_sequence(
            rows,
            get_odd_even
        )

        print("\n最近10期回测")
        print("="*60)

        start = max(
            20,
            len(rows) - args.test
        )

        for t in range(start, len(rows)):

            eng = ConditionalMarkov(
                ["红","蓝","绿"],
                recent_periods=args.recent
            )

            eng.train(color_seq[:t])

            pred = eng.predict(
                color_seq[max(0,t-30):t]
            )

            sorted_pred = sorted(
                pred.items(),
                key=lambda x:x[1],
                reverse=True
            )

            main_color = sorted_pred[0][0]
            second_color = sorted_pred[1][0]

            actual_color = color_seq[t]

            size_engine = ConditionalMarkov(
                ["大","小"]
            )

            size_engine.train(size_seq[:t])

            size_pred = size_engine.predict(
                size_seq[max(0,t-30):t]
            )

            odd_engine = ConditionalMarkov(
                ["单","双"]
            )

            odd_engine.train(odd_seq[:t])

            odd_pred = odd_engine.predict(
                odd_seq[max(0,t-30):t]
            )

            print(
                f"{rows[t]['issue_no']} "
                f"{rows[t]['draw_date']} "
                f"| 波色:{main_color}+{second_color} "
                f"| 开:{actual_color} "
                f"| 大小:{max(size_pred,key=size_pred.get)} "
                f"| 单双:{max(odd_pred,key=odd_pred.get)}"
            )

        print("\n" + "="*60)
        print("下期预测")
        print("="*60)

        final = ConditionalMarkov(
            ["红","蓝","绿"],
            recent_periods=args.recent
        )

        final.train(color_seq)

        future = final.predict(
            color_seq[-30:]
        )

        s = sorted(
            future.items(),
            key=lambda x:x[1],
            reverse=True
        )

        print("\n【波色】")

        for k,v in s:

            print(
                f"{k} : {v*100:.2f}%"
            )

        print(
            f"\n推荐组合: "
            f"{s[0][0]} + {s[1][0]}"
        )

        size_final = ConditionalMarkov(
            ["大","小"]
        )

        size_final.train(size_seq)

        sf = size_final.predict(
            size_seq[-30:]
        )

        print("\n【大小】")

        for k,v in sorted(
            sf.items(),
            key=lambda x:x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

        odd_final = ConditionalMarkov(
            ["单","双"]
        )

        odd_final.train(odd_seq)

        of = odd_final.predict(
            odd_seq[-30:]
        )

        print("\n【单双】")

        for k,v in sorted(
            of.items(),
            key=lambda x:x[1],
            reverse=True
        ):

            print(
                f"{k} : {v*100:.2f}%"
            )

    except Exception as e:

        print(f"\n错误: {e}")

    finally:

        conn.close()

# =========================================================

if __name__ == "__main__":
    main()